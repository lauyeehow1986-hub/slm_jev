"""Pool several benchmark reports into one release-gate verdict, with 95% intervals (P22).
Synthetic data only.

One blind set of about 30 notes decides the gate by a span or two (notes_v14, notes_v15), so P22
fixes the verdict on the pooled counts of several sets by separate writers, decided before the
run (``docs/decisions/0019``)::

    python eval/pool.py results/bench_notes_v16_blind.json results/bench_notes_v17_blind.json \\
        results/bench_notes_v18_blind.json --out results/pooled_v16_v18.json

- **Direct recall**: direct-identifier gold (``DIRECT``) that no prediction overlaps, over all
  direct gold, summed over the sets.
- **Precision**: true-positive over all predicted spans (``tp``, ``fp`` of the identifier view),
  summed over the sets. **F1** from pooled recall (all identifiers) and precision.
- **ECE**: over every judged candidate of all the sets together (``bench.calibration``).
- **Intervals**: Wilson 95% for the two proportions; for ECE, a bootstrap over notes (seeded).
  They are reported, not gated: the gate compares the pooled point estimates with the thresholds,
  as each single set's row always did.
- **Latency**: mean seconds per 1k characters over all the sets, and each set's per-note p95.

A check with no data is INCOMPLETE, and so is the verdict; it is never PASS.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent)]

import bench  # noqa: E402

from slmjev import calibrate  # noqa: E402

SYSTEM = "jev+pf+ner.person"
DIRECT = ("name", "national_id", "mrn", "phone", "email", "case_visit", "fax", "address",
          "postal_code", "dob", "date_of_death")
GATE = {"direct_recall": 0.98, "precision": 0.90, "ece": 0.05, "f1": 0.889}
Z = 1.959964


def wilson(k: int, n: int, z: float = Z) -> tuple[float, float] | None:
    """The Wilson score interval for ``k`` successes in ``n``."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)


def ece_interval(notes: list[list[tuple[float, bool]]], reps: int = 2000, seed: int = 0
                 ) -> tuple[float, float] | None:
    """A 95% percentile interval for ECE, resampling whole notes (their candidates are not
    independent)."""
    notes = [n for n in notes if n]
    if not notes:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        rows = [r for _ in notes for r in rng.choice(notes)]
        out.append(calibrate.ece([p for p, _ in rows], [y for _, y in rows]))
    out.sort()
    return round(out[int(0.025 * reps)], 4), round(out[int(0.975 * reps) - 1], 4)


def one_set(rep: dict, system: str) -> dict:
    """The counts one report gives the pooled verdict."""
    res = rep["results"][system]["identifiers"]
    per = res["per_label"]
    gold_direct = sum(per[k]["n"] for k in DIRECT if k in per)
    missed_direct = sum(1 for m in res["misses"] if m["label"] in DIRECT and not m["overlapped"])
    missed = sum(1 for m in res["misses"] if not m["overlapped"])
    docs = bench.load_set(Path(rep["set"]))
    notes = [[(min(max(float(c["confidence"]), 0.0), 1.0), bool(c["correct"]))
              for c in bench.candidates([d], [js])]
             for d, js in zip(docs, rep["judged"][system], strict=True)]
    timing = rep.get("timing", {})
    return {"set": Path(rep["set"]).stem, "notes": rep.get("docs"), "chars": rep.get("chars"),
            "gold": res["gold"], "missed": missed, "gold_direct": gold_direct,
            "missed_direct": missed_direct, "tp": res["tp"], "fp": res["fp"],
            "fp_on_negative_notes": res.get("fp_on_negative_notes"),
            "secs": (timing.get(system) or {}).get("secs"),
            "p95_per_note": (timing.get(f"{system}:per_doc") or {}).get("p95"),
            "candidates": notes}


def summary(sets: list[dict], reps: int = 2000) -> dict:
    """Recall, direct recall, precision, F1 and ECE of ``sets`` taken together."""
    g, m = sum(s["gold"] for s in sets), sum(s["missed"] for s in sets)
    gd, md = sum(s["gold_direct"] for s in sets), sum(s["missed_direct"] for s in sets)
    tp, fp = sum(s["tp"] for s in sets), sum(s["fp"] for s in sets)
    recall = (g - m) / g if g else None
    precision = tp / (tp + fp) if tp + fp else None
    f1 = (2 * recall * precision / (recall + precision)
          if recall is not None and precision and recall + precision else None)
    notes = [n for s in sets for n in s["candidates"]]
    rows = [r for n in notes for r in n]
    ece = calibrate.ece([p for p, _ in rows], [y for _, y in rows]) if rows else None
    secs = [s["secs"] for s in sets]
    chars = [s["chars"] for s in sets]
    latency = (round(sum(secs) / sum(chars) * 1000, 2)
               if all(x is not None for x in secs + chars) and sum(chars) else None)
    r4 = bench._r
    return {"recall": r4(recall), "recall_ci": wilson(g - m, g),
            "direct_recall": r4((gd - md) / gd if gd else None),
            "direct_hits": gd - md, "direct_gold": gd, "direct_ci": wilson(gd - md, gd),
            "precision": r4(precision), "tp": tp, "fp": fp, "precision_ci": wilson(tp, tp + fp),
            "f1": r4(f1), "ece": r4(ece), "candidates": len(rows),
            "ece_ci": ece_interval(notes, reps), "secs_per_1k_chars": latency,
            "p95_per_note": [s["p95_per_note"] for s in sets]}


def verdict(pooled: dict) -> dict:
    """PASS / FAIL per check and overall; a check with no data is INCOMPLETE."""
    checks = {
        "direct_recall": (pooled["direct_recall"], lambda v: v >= GATE["direct_recall"]),
        "precision": (pooled["precision"], lambda v: v >= GATE["precision"]),
        "ece": (pooled["ece"], lambda v: v <= GATE["ece"]),
        "f1": (pooled["f1"], lambda v: v > GATE["f1"]),
        "latency": (pooled["secs_per_1k_chars"], lambda v: True),
    }
    out = {k: "INCOMPLETE" if v is None else ("PASS" if ok(v) else "FAIL")
           for k, (v, ok) in checks.items()}
    if any(p is None for p in pooled["p95_per_note"]):
        out["latency"] = "INCOMPLETE"
    vals = set(out.values())
    out["overall"] = "FAIL" if "FAIL" in vals else "INCOMPLETE" if "INCOMPLETE" in vals else "PASS"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("reports", nargs="+", type=Path)
    ap.add_argument("--system", default=SYSTEM)
    ap.add_argument("--reps", type=int, default=2000, help="bootstrap resamples for ECE")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    sets = [one_set(json.loads(p.read_text(encoding="utf-8")), args.system)
            for p in args.reports]
    per_set = {s["set"]: summary([s], args.reps) for s in sets}
    pooled = summary(sets, args.reps)
    out = {"system": args.system, "gate": GATE, "reports": [str(p) for p in args.reports],
           "per_set": per_set, "pooled": pooled, "verdict": verdict(pooled)}

    print("| set | recall | direct recall (95% CI) | precision (95% CI) | F1 | ECE (95% CI) |")
    print("|---|---|---|---|---|---|")
    for name, s in [*per_set.items(), ("**pooled**", pooled)]:
        print(f"| {name} | {s['recall']} | {s['direct_recall']} ({s['direct_hits']} of "
              f"{s['direct_gold']}; {s['direct_ci']}) | {s['precision']} ({s['tp']} of "
              f"{s['tp'] + s['fp']}; {s['precision_ci']}) | {s['f1']} | {s['ece']} "
              f"({s['candidates']} candidates; {s['ece_ci']}) |")
    print("verdict:", out["verdict"])
    if args.out:
        args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
