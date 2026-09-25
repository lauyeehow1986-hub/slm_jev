"""Fit the judge's calibrator and thresholds (P4). Synthetic data only.

Input is two ``eval/judge_eval.py --prod`` reports on disjoint splits:

    uv run python eval/fit_calibration.py --train results/train.json --test results/test.json

1. On the *train* report's model-judged rows (fast-path rows skip the model and are not
   calibrated), pick the calibrator kind (identity / temperature / isotonic) by grouped k-fold
   NLL. Folds are split by document so one note never sits on both sides.
2. Choose ``drop_below`` / ``accept_at`` from the *out-of-fold* calibrated scores. Thresholds
   fitted on the scores the calibrator was trained on are optimistic, especially for isotonic.
3. Fit the chosen calibrator on all of train, and write ``models/calibration.json``.
4. Apply it once to the *test* report and print before/after metrics, with decisions replayed by
   ``judge.decide`` (the judge's own rule), so no model calls are needed.

Nothing is tuned on test.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from slmjev import calibrate
from slmjev import judge as J
from slmjev.labels import load_labels

KINDS = ("identity", "temperature", "isotonic")
# a more flexible calibrator must beat the simpler one's CV NLL by this much to be chosen
SIMPLER_MARGIN = 0.005
_PRIOR = ("uncertain", "category_uncertain")  # reasons decide() itself adds


def load_rows(path: Path) -> tuple[dict, list[dict]]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in doc.items() if k not in ("rows", "summary")}, doc["rows"]


def model_rows(rows: list[dict]) -> list[dict]:
    """Rows the model judged successfully: the ones calibration applies to."""
    return [r for r in rows if r["rec"]["p_identifier"] is not None
            and not r["rec"]["judge"].get("fast_path")]


def xy(rows: list[dict]) -> tuple[list[float], list[bool]]:
    return [r["rec"]["p_identifier"] for r in rows], [r["gold"]["is_pii"] for r in rows]


def folds(rows: list[dict], k: int) -> list[int]:
    """A fold per row, by document (docs in first-seen order, dealt round-robin)."""
    order = list(dict.fromkeys(r["doc"] for r in rows))
    if len(order) < k:
        raise ValueError(f"{len(order)} documents cannot fill {k} folds")
    fold_of = {d: i % k for i, d in enumerate(order)}
    return [fold_of[r["doc"]] for r in rows]


def out_of_fold(kind: str, p: list[float], y: list[bool], fold: list[int]) -> list[float]:
    """Each score calibrated by a calibrator fitted without its fold."""
    out = [0.0] * len(p)
    for f in sorted(set(fold)):
        tr = [i for i, g in enumerate(fold) if g != f]
        cal = calibrate.fit(kind, [p[i] for i in tr], [y[i] for i in tr])
        for i, g in enumerate(fold):
            if g == f:
                out[i] = cal(p[i])
    return out


def choose_kind(cv_nll: dict[str, float]) -> str:
    """The simplest kind within ``SIMPLER_MARGIN`` of the best CV NLL."""
    best = min(cv_nll.values())
    return next(k for k in KINDS if k in cv_nll and cv_nll[k] <= best + SIMPLER_MARGIN)


def replay(rows: list[dict], cal: Callable[[float], float] | None,
           th: J.Thresholds) -> list[str]:
    """The judge's decision for each row under ``cal`` and ``th``, without the model."""
    out = []
    for r in rows:
        rec = r["rec"]
        if rec["judge"].get("fast_path"):
            out.append(rec["decision"])
        elif rec["p_identifier"] is None:
            out.append("review")  # fail closed
        else:
            conf = cal(rec["p_identifier"]) if cal else rec["p_identifier"]
            prior = [x for x in rec["judge"].get("reasons", []) if x not in _PRIOR]
            out.append(J.decide(conf, max(rec["category_probs"].values()), th, prior))
    return out


def decision_metrics(rows: list[dict], decisions: list[str], identifiers: list[str]) -> dict:
    def rate(n, d):
        return round(n / d, 4) if d else None

    gold = [(r, d) for r, d in zip(rows, decisions, strict=True) if r["gold"]["is_pii"]]
    decoy = [d for r, d in zip(rows, decisions, strict=True) if not r["gold"]["is_pii"]]
    direct = [(r, d) for r, d in gold if r["gold"]["label"] in identifiers]
    accepted = sum(d == "identifier" for d in decisions)
    per_label = defaultdict(lambda: [0, 0])
    for r, d in gold:
        per_label[r["gold"]["label"]][0] += 1
        per_label[r["gold"]["label"]][1] += d != "not_identifier"
    missed = [{"label": r["gold"]["label"], "match": r["rec"]["match"],
               "p": r["rec"]["p_identifier"]} for r, d in gold if d == "not_identifier"]
    return {
        "n": len(rows),
        "recall_flagged": rate(sum(d != "not_identifier" for _, d in gold), len(gold)),
        "recall_flagged_direct": rate(sum(d != "not_identifier" for _, d in direct), len(direct)),
        "recall_auto": rate(sum(d == "identifier" for _, d in gold), len(gold)),
        "precision_auto": rate(sum(d == "identifier" for _, d in gold), accepted),
        "decoy_dropped": rate(sum(d == "not_identifier" for d in decoy), len(decoy)),
        "review_rate": rate(decisions.count("review"), len(decisions)),
        "per_label_recall": {k: rate(v[1], v[0]) for k, v in sorted(per_label.items())},
        "missed": missed,
    }


def prob_metrics(p: list[float], y: list[bool]) -> dict:
    return {"auroc": calibrate.auroc(p, y), "ece": round(calibrate.ece(p, y), 4),
            "brier": round(calibrate.brier(p, y), 4), "nll": round(calibrate.nll(p, y), 4)}


def fit(train_rows: list[dict], *, k: int = 5, kinds: tuple[str, ...] = KINDS,
        recall_target: float = 0.98, precision_target: float = 0.98,
        min_support: int = 20) -> dict:
    """Calibrator + thresholds from train rows; see the module docstring."""
    rows = model_rows(train_rows)
    p, y = xy(rows)
    fold = folds(rows, k)
    oof = {kind: out_of_fold(kind, p, y, fold) for kind in kinds}
    cv_nll = {kind: round(calibrate.nll(v, y), 5) for kind, v in oof.items()}
    kind = choose_kind(cv_nll)
    choice = calibrate.choose_thresholds(oof[kind], y, recall_target=recall_target,
                                         precision_target=precision_target,
                                         min_support=min_support)
    return {"kind": kind, "calibrator": calibrate.fit(kind, p, y), "cv_nll": cv_nll,
            "cv": {kk: prob_metrics(v, y) for kk, v in oof.items()}, "choice": choice,
            "n": len(rows), "n_pos": sum(y), "folds": k}


def evaluate(rows: list[dict], cal, th: J.Thresholds, identifiers: list[str]) -> dict:
    mr = model_rows(rows)
    p, y = xy(mr)
    return {"probs": prob_metrics([cal(v) for v in p] if cal else p, y),
            "decisions": decision_metrics(rows, replay(rows, cal, th), identifiers)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fit calibration + thresholds on a train report")
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("models/calibration.json"))
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--recall-target", type=float, default=0.98)
    ap.add_argument("--precision-target", type=float, default=0.98)
    args = ap.parse_args(argv)

    identifiers = load_labels()["identifiers"]
    tr_meta, tr_rows = load_rows(args.train)
    te_meta, te_rows = load_rows(args.test)
    for m, name in ((tr_meta, "train"), (te_meta, "test")):
        if m.get("split") != name or not m.get("prod"):
            raise SystemExit(f"--{name} must be a --prod report on the {name} split")

    f = fit(tr_rows, k=args.folds, recall_target=args.recall_target,
            precision_target=args.precision_target)
    ch = f["choice"]
    th = replace(J.Thresholds(), drop_below=ch.drop_below, accept_at=ch.accept_at)
    base = J.Thresholds()
    meta = {"fitted": time.strftime("%Y-%m-%d"), "model": tr_meta.get("model"),
            "generator": tr_meta.get("generator"), "train": {k: tr_meta.get(k) for k in (
                "split", "seed", "notes", "cells", "rotations")},
            "n": f["n"], "n_pos": f["n_pos"], "folds": f["folds"], "cv_nll": f["cv_nll"],
            "recall_target": args.recall_target, "precision_target": args.precision_target,
            "recall_at_drop_oof": ch.recall_at_drop,
            "precision_at_accept_oof": ch.precision_at_accept, "notes": ch.notes}
    calibrate.save(args.out, f["calibrator"],
                   {"drop_below": ch.drop_below, "accept_at": ch.accept_at}, meta)

    report = {
        "calibration": {**f["calibrator"].to_dict(),
                        "drop_below": ch.drop_below, "accept_at": ch.accept_at},
        "meta": meta, "cv": f["cv"],
        "train": {"zero_shot": evaluate(tr_rows, None, base, identifiers),
                  "calibrated": evaluate(tr_rows, f["calibrator"], th, identifiers)},
        "test": {"zero_shot": evaluate(te_rows, None, base, identifiers),
                 "calibrated": evaluate(te_rows, f["calibrator"], th, identifiers)},
    }
    text = json.dumps(report, indent=1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
