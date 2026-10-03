"""Fit a grouped calibrator for the release configuration from benchmark reports (P13).
Synthetic data only.

The P5 calibration was fitted on single-span judge rows, where the judge's saturated scores
looked calibrated. On whole notes they are not: an SHI call or a heading the judge calls a name
comes out at 0.99 too (ECE 0.143 on notes_v5, `docs/decisions/0010`). This refits the map on
what the release configuration actually judges, i.e. every candidate in ``eval/bench.py``
reports (their ``judged`` records), with one map per kind of call (``calibrate.Grouped``)::

    python eval/fit_bench_calibration.py \\
        --train results/cal_notes_v2.json results/cal_notes_v3.json \\
        --test results/cal_notes_v5.json --base models/calibration_p5.json \\
        --out models/calibration_p13.json

1. Rows are the model-judged candidates of the *train* reports (``bench.candidates``); rule
   fast-path spans skip the model and keep ``judge.RULE_CONFIDENCE``.
2. The kind (identity / temperature / isotonic, per group) is picked by out-of-fold NLL, with
   folds by note.
3. Decisions: by default (``--decide-on raw``) they keep the base thresholds, compared with the
   raw ``p_identifier``, so the decisions validated blind stay exactly as they were and only the
   reported ``confidence`` changes. ``--decide-on calibrated`` instead chooses new thresholds on
   the out-of-fold calibrated scores (``calibrate.choose_thresholds``).
4. The final map is fitted on all of train and saved. Every report is then replayed without the
   model: the new confidences, the decisions ``judge.decide`` makes with them, and the benchmark
   scores those decisions give. Replaying the *base* calibration first must reproduce each
   report's recorded decisions, or the replay is not trusted.

Nothing is tuned on test. Train only on sets with SHI gold (not sd20): with none, every SHI call
counts as wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent)]

import bench  # noqa: E402
import fit_calibration as F  # noqa: E402

from slmjev import calibrate  # noqa: E402
from slmjev import judge as J  # noqa: E402

SYSTEM = "jev+pf+ner.person"
_LABELS = [*bench.IDENTIFIERS, *bench.SHI]


def load_report(path: Path, system: str) -> tuple[str, list[dict], list[list[dict]], dict]:
    rep = json.loads(Path(path).read_text(encoding="utf-8"))
    if system not in rep.get("judged", {}):
        raise ValueError(f"{path}: no judged records for {system!r}")
    docs = bench.load_set(Path(rep["set"]))
    return Path(rep["set"]).stem, docs, rep["judged"][system], rep["results"][system]


def model_rows(name: str, docs: list[dict], judged: list[list[dict]]) -> list[dict]:
    return [{**c, "doc": f"{name}/{c['doc']}"} for c in bench.candidates(docs, judged)
            if c["call"] != "fast_path"]


def fit(kind: str, rows: list[dict], min_n: int) -> calibrate.Calibrator:
    if kind == "identity":
        return calibrate.Identity()
    return calibrate.Grouped.fit([r["p_identifier"] for r in rows], [r["correct"] for r in rows],
                                 [r["call"] for r in rows], kind=kind, min_n=min_n)


def apply(cal: calibrate.Calibrator, r: dict) -> float:
    if isinstance(cal, calibrate.Grouped):
        return cal(r["p_identifier"], r["call"])
    return cal(r["p_identifier"])


def _unrounded(cal: calibrate.Calibrator, call: str, p: float) -> list[float]:
    """The confidences an unrounded lookup (before P22) could give a raw p that the report
    rounded to ``p`` (6 places): an isotonic map's blocks at ``p`` and half a unit either side."""
    m = dict(cal.maps).get(call, cal.default) if isinstance(cal, calibrate.Grouped) else cal
    if not isinstance(m, calibrate.Isotonic):
        return []
    return [min(1 - m.floor, max(m.floor, m.ys[min(bisect_left(m.xs, q), len(m.ys) - 1)]))
            for q in (p - 5e-7, p, p + 5e-7)]


def out_of_fold(kind: str, rows: list[dict], k: int, min_n: int) -> list[float]:
    fold = F.folds(rows, k)
    out = [0.0] * len(rows)
    for f in sorted(set(fold)):
        cal = fit(kind, [r for r, g in zip(rows, fold, strict=True) if g != f], min_n)
        for i, g in enumerate(fold):
            if g == f:
                out[i] = apply(cal, rows[i])
    return out


def _label(c: dict) -> str | None:
    """The span's ``identifier`` as ``engine.record`` sets it: an unsure "none" still names the
    most likely label (fail closed)."""
    if c.get("category") not in (None, "none"):
        return c["category"]
    if c["decision"] != "review":
        return None
    probs = c.get("category_probs") or {}
    return max((k for k in _LABELS if k in probs), key=lambda k: probs[k], default=None)


def replay(docs: list[dict], judged: list[list[dict]], cal, th: J.Thresholds
           ) -> tuple[list[list[dict]], list[list[dict]]]:
    """``(judged, preds)`` as ``bench.run_jev`` would have returned them under ``cal`` and
    ``th``: new confidences and decisions for the model-judged candidates, the rest as
    recorded. Exact while the decisions do not change; when they do, ``engine.resolve`` might
    have kept or dropped a nested span differently, which this does not redo."""
    new_judged, preds = [], []
    for js in judged:
        out = []
        for c in js:
            c = dict(c)
            if not c.get("fast_path") and c.get("p_identifier") is not None:
                call = calibrate.group_of(c.get("category"), bench.SHI, c.get("sources") or ())
                conf = apply(cal, {"p_identifier": c["p_identifier"], "call": call})
                c["confidence"] = round(conf, 4)
                # in raw space the calibrator cannot move a decision, so keep the recorded one:
                # it also had the judge's reasons (a cell review, a Noul disagreement), which
                # the report does not keep
                if th.space != "raw":
                    c["decision"] = J.decide(conf, max(c["category_probs"].values()), th, [])
            out.append(c)
        new_judged.append(out)
        preds.append([{**c, "label": _label(c)} for c in out
                      if c["decision"] != "not_identifier"])
    return new_judged, preds


def scores(docs, judged, preds) -> dict:
    ident = bench.score(docs, preds, bench.VIEWS["identifiers"])
    shi = bench.score_shi(docs, preds)
    cal = bench.calibration(docs, judged)
    decisions = defaultdict(int)
    for js in judged:
        for c in js:
            decisions[c.get("decision")] += 1
    return {"recall": ident["recall"], "covered": ident["covered"],
            "auto_covered": ident["auto_covered"], "precision": ident["precision"],
            "f1": ident["f1"], "fp": ident["fp"], "silent_misses": sum(
                not m["overlapped"] for m in ident["misses"]),
            "shi_recall": shi["recall"], "shi_precision": shi["precision"],
            "ece": cal["ece"], "ece_by_call": {g: v["ece"] for g, v in cal["by_call"].items()},
            "decisions": dict(sorted(decisions.items()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--train", nargs="+", required=True, type=Path)
    ap.add_argument("--test", nargs="*", default=[], type=Path)
    ap.add_argument("--system", default=SYSTEM)
    ap.add_argument("--base", required=True, type=Path, help="the calibration the reports ran")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--report", type=Path, help="write the before/after numbers here (JSON)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--min-n", type=int, default=30, help="smallest group with its own map")
    ap.add_argument("--kinds", default="identity,temperature,isotonic")
    ap.add_argument("--decide-on", choices=("raw", "calibrated"), default="raw",
                    help="raw: keep the base thresholds on p_identifier, so only the reported "
                         "confidence changes; calibrated: refit thresholds on the new scores")
    args = ap.parse_args(argv)

    base_cal, base_th, base_meta = calibrate.load(args.base)
    base = replace(J.Thresholds(), drop_below=base_th["drop_below"],
                   accept_at=base_th["accept_at"], space=base_th.get("space", "calibrated"))
    reports = {p: load_report(p, args.system) for p in [*args.train, *args.test]}

    # the replay must reproduce what the judge decided, or nothing below means anything
    for p, (_, docs, judged, results) in reports.items():
        again, preds = replay(docs, judged, base_cal, base)
        got = bench.score(docs, preds, bench.VIEWS["identifiers"])
        want = results.get("identifiers", {})
        if want and (got["recall"], got["precision"]) != (want["recall"], want["precision"]):
            raise SystemExit(f"{p}: replay scores {got['recall']}/{got['precision']}, the report "
                             f"{want['recall']}/{want['precision']}")
        # confidence to 1e-4: the report keeps p_identifier to 6 places, so re-rounding can move
        # the 4th place
        # A report run before P22 looked p up unrounded (``calibrate.Isotonic``), so a p next to
        # a block edge may carry the next block's confidence. Only the confidence; never the
        # decision.
        diff = legacy = 0
        for x, y in zip(judged, again, strict=True):
            for a, b in zip(x, y, strict=True):
                if a["decision"] != b["decision"]:
                    diff += 1
                elif (a["confidence"] is not None
                      and abs(a["confidence"] - b["confidence"]) > 1.01e-4):
                    call = calibrate.group_of(a.get("category"), bench.SHI, a.get("sources") or ())
                    if any(abs(a["confidence"] - c) <= 1.01e-4
                           for c in _unrounded(base_cal, call, a["p_identifier"])):
                        legacy += 1
                    else:
                        diff += 1
        if diff:
            raise SystemExit(f"{p}: replaying the base calibration changed {diff} candidates")
        if legacy:
            print(f"{p}: {legacy} confidences at a block edge, recorded with the pre-P22 lookup")

    rows = [r for p in args.train for r in model_rows(*reports[p][:3])]
    ys = [r["correct"] for r in rows]
    cv = {}
    oof = {}
    for kind in args.kinds.split(","):
        oof[kind] = out_of_fold(kind, rows, args.folds, args.min_n)
        cv[kind] = {"nll": round(calibrate.nll(oof[kind], ys), 4),
                    "ece": round(calibrate.ece(oof[kind], ys), 4)}
    kind = F.choose_kind({k: v["nll"] for k, v in cv.items()})
    choice = calibrate.choose_thresholds(oof[kind], ys)
    if args.decide_on == "raw":
        # an identity base's thresholds are raw already; a raw-space base (P13) keeps its own
        if base.space != "raw" and not isinstance(base_cal, calibrate.Identity):
            raise SystemExit("--decide-on raw needs an identity or a raw-space base "
                             "calibration, whose thresholds are already in raw space")
        th = replace(base, space="raw")
    else:
        th = replace(J.Thresholds(), drop_below=choice.drop_below, accept_at=choice.accept_at)
    cal = fit(kind, rows, args.min_n)

    meta = {"prompt": J.PROMPT_VERSION, "model": base_meta.get("model"), "kind": kind,
            "grouped_by": "calibrate.group_of (identifier / none / shi_lexicon / shi_other); "
                          "fast path unchanged",
            "system": args.system, "fitted_on": [str(p) for p in args.train],
            "n": len(rows), "positives": sum(ys), "min_n": args.min_n, "cv": cv,
            "threshold_notes": choice.notes, "recall_at_drop": choice.recall_at_drop,
            "precision_at_accept": choice.precision_at_accept,
            "synthetic_only": True}
    thresholds = {"drop_below": th.drop_below, "accept_at": th.accept_at, "space": th.space}
    meta["thresholds_from"] = (f"{args.base} (raw space: decisions unchanged)"
                               if th.space == "raw" else "out-of-fold calibrated scores")
    calibrate.save(args.out, cal, thresholds, meta)

    out = {"calibration": str(args.out), "kind": kind, "cv": cv,
           "thresholds": thresholds,
           "refit_thresholds_would_be": {"drop_below": choice.drop_below,
                                         "accept_at": choice.accept_at, "notes": choice.notes},
           "sets": {}}
    print(f"n={len(rows)} positives={sum(ys)} kind={kind} cv={cv}")
    print(f"thresholds {thresholds}; a refit on calibrated scores would give "
          f"drop_below={choice.drop_below} accept_at={choice.accept_at} {choice.notes}")
    print("| set | split | calibration | recall | covered | precision | F1 | silent | "
          "SHI R | SHI P | ECE | ECE by call | decisions |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p, (name, docs, judged, _) in reports.items():
        split = "train" if p in args.train else "test"
        row = {}
        for label, (c, t) in (("base", (base_cal, base)), ("new", (cal, th))):
            j2, preds = replay(docs, judged, c, t)
            s = row[label] = scores(docs, j2, preds)
            print(f"| {name} | {split} | {label} | {s['recall']} | {s['covered']} | "
                  f"{s['precision']} | {s['f1']} | {s['silent_misses']} | {s['shi_recall']} | "
                  f"{s['shi_precision']} | {s['ece']} | {s['ece_by_call']} | "
                  f"{s['decisions']} |")
        out["sets"][name] = {"split": split, **row}
    if args.report:
        args.report.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
