"""Judge evaluation on a synthetic split (P3, P4). Synthetic data only.

Candidates come from an *oracle proposer*: every gold span plus every decoy the generator
planted. That measures the judge alone. The end-to-end system also depends on the proposer,
which is measured separately.

    set SLMJEV_LLAMA_SERVER=...\\llama-server.exe
    set SLMJEV_JUDGE_MODEL=...\\Qwen3-1.7B-Q4_K_M.gguf
    uv run python eval/judge_eval.py --notes 40 --seed 11

It starts its own llama-server (loopback, random key, CPU) unless --url is given, forbids all
non-loopback network access, and writes results/judge_eval_<stamp>.json.

``--prod`` runs the production configuration (4 rotations, Choice only, rule-certain fast path);
P4 fits calibration on a ``--split train`` run of it (``eval/fit_calibration.py``).

A structured cell is judged as part of a table column: its value plus clean neighbours
(``synth.column_peers``) go through ``slmjev.column.date_outliers``, as the R side will pass whole
columns in P6.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from slmjev import calibrate, column, netguard, server, sft, synth
from slmjev import judge as J
from slmjev.labels import load_labels

ROLE_GOLD = {"dob": "dob", "death": "date_of_death"}


# the oracle proposer, shared with the finetune data so the two cannot drift
candidates = sft.oracle_candidates


PEERS = 30  # clean neighbours of a cell in its synthetic table column


def column_outlier(doc: dict) -> bool:
    """Whether a cell's value stands out from its (synthetic) table column."""
    if doc.get("kind") != "cell" or not doc.get("column"):
        return False
    peers = synth.column_peers(doc["column"], PEERS, doc["id"])
    return bool(peers) and column.date_outliers([*peers, doc["text"]])[-1]


def judge_doc(j: J.Judge, doc: dict, max_cands: int | None = None) -> list[dict]:
    rows = []
    outlier = column_outlier(doc)
    for cand, gold in candidates(doc)[:max_cands]:
        n0 = len(getattr(j.backend, "calls", []))
        t0 = time.perf_counter()
        rec = j.judge(doc["text"], cand, doc.get("column"), outlier)
        secs = time.perf_counter() - t0
        calls = getattr(j.backend, "calls", [])[n0:]
        rows.append({"doc": doc["id"], "chars": len(doc["text"]), "gold": gold, "rec": rec,
                     "secs": secs, "calls": calls})
    return rows


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))]


def _argmax(d: dict) -> str:
    return max(d, key=d.__getitem__)


def _prob_metrics(p: list[float], y: list[bool]) -> dict:
    return {"auroc": calibrate.auroc(p, y), "ece": calibrate.ece(p, y) if p else None,
            "brier": calibrate.brier(p, y) if p else None,
            "nll": calibrate.nll(p, y) if p else None}


def summarize(rows: list[dict], identifiers: list[str]) -> dict:
    fast = [r for r in rows if r["rec"]["judge"].get("fast_path")]
    ok = [r for r in rows if r["rec"]["p_identifier"] is not None
          and not r["rec"]["judge"].get("fast_path")]  # model-judged: calibration applies
    gold = [r for r in rows if r["gold"]["is_pii"]]
    decoy = [r for r in rows if not r["gold"]["is_pii"]]
    dec = Counter((r["gold"]["is_pii"], r["rec"]["decision"]) for r in rows)

    def rate(n, d):
        return round(n / d, 4) if d else None

    flagged_gold = dec[(True, "identifier")] + dec[(True, "review")]
    accepted = dec[(True, "identifier")] + dec[(False, "identifier")]
    per_label = defaultdict(lambda: [0, 0, 0])  # n, flagged, category correct
    for r in gold:
        c = per_label[r["gold"]["label"]]
        c[0] += 1
        c[1] += r["rec"]["decision"] != "not_identifier"
        c[2] += r["rec"]["category"] == r["gold"]["label"]
    direct = [r for r in gold if r["gold"]["label"] in identifiers]

    p = [r["rec"]["p_identifier"] for r in ok]
    y = [r["gold"]["is_pii"] for r in ok]
    noul = [(r["rec"]["judge"]["p_noul"], r["gold"]["is_pii"]) for r in ok
            if "p_noul" in r["rec"]["judge"]]

    # order bias, from the traced per-order Choice answers
    flip1 = flip4 = 0
    d_p1, d_p4, spreads = [], [], []
    for r in ok:
        orders = r["rec"]["judge"].get("choice_orders")
        if not orders:
            continue
        full = r["rec"]["category_probs"]
        spreads.append(r["rec"]["judge"]["choice_spread"])
        sub = [orders[i[0]] for i in J.rotations(len(orders), 4)]
        sub4 = {k: statistics.fmean(o[k] for o in sub) for k in full}
        flip1 += _argmax(orders[0]) != _argmax(full)
        flip4 += _argmax(sub4) != _argmax(full)
        d_p1.append(abs(orders[0]["none"] - full["none"]))
        d_p4.append(abs(sub4["none"] - full["none"]))

    # context for policy `by` rules
    role_n = role_ok = 0
    for r in ok:
        g, ctx = r["gold"], r["rec"]["context"]
        want = ROLE_GOLD.get(g.get("role")) if g["is_pii"] else (
            "other" if g["type"] == "date" else None)
        if want and "date_role" in ctx:
            role_n += 1
            role_ok += ctx["date_role"] == want
    prop_n = prop_ok = type_ok = type_unknown = 0
    for r in rows:
        g, ctx = r["gold"], r["rec"]["context"]
        if g["label"] in ("address", "postal_code") and g.get("property"):
            prop_n += 1
            prop_ok += ctx.get("property_kind") == g["property"]
            got = ctx.get("property_type", "unknown")
            type_unknown += got == "unknown"
            type_ok += got == J.PROPERTY_TYPE.get(g["property"])

    # latency
    call_secs = [c["secs"] for r in rows for c in r["calls"]]
    prompt_n = sum(c["prompt_n"] or 0 for r in rows for c in r["calls"])
    cache_n = sum(c["cache_n"] or 0 for r in rows for c in r["calls"])
    per_doc = defaultdict(lambda: [0.0, 0])
    for r in rows:
        per_doc[r["doc"]][0] += r["secs"]
        per_doc[r["doc"]][1] = r["chars"]
    per_1k = [s / c * 1000 for s, c in per_doc.values() if c]

    return {
        "n": len(rows), "n_gold": len(gold), "n_decoy": len(decoy),
        "n_failed": sum(r["rec"]["p_identifier"] is None for r in rows),
        "decisions": {f"{'gold' if k[0] else 'decoy'}:{k[1]}": v for k, v in sorted(dec.items())},
        "recall_flagged": rate(flagged_gold, len(gold)),
        "recall_flagged_direct": rate(sum(r["rec"]["decision"] != "not_identifier"
                                          for r in direct), len(direct)),
        "recall_auto": rate(dec[(True, "identifier")], len(gold)),
        "precision_auto": rate(dec[(True, "identifier")], accepted),
        "precision_flagged": rate(flagged_gold, flagged_gold + dec[(False, "identifier")]
                                  + dec[(False, "review")]),
        "decoy_dropped": rate(dec[(False, "not_identifier")], len(decoy)),
        "review_rate": rate(dec[(True, "review")] + dec[(False, "review")], len(rows)),
        "category_acc_gold": rate(sum(c[2] for c in per_label.values()), len(gold)),
        "per_label": {k: {"n": v[0], "flagged": v[1], "category_ok": v[2]}
                      for k, v in sorted(per_label.items())},
        "p_identifier": {"auroc": calibrate.auroc(p, y), "ece": calibrate.ece(p, y) if p else None,
                         "brier": calibrate.brier(p, y) if p else None,
                         "acc_at_0.5": rate(sum((a >= 0.5) == b for a, b in zip(p, y,
                                                                                strict=True)),
                                            len(p))},
        "confidence": _prob_metrics([r["rec"]["confidence"] for r in ok], y),
        "p_noul": {"auroc": calibrate.auroc([a for a, _ in noul], [b for _, b in noul]),
                   "ece": calibrate.ece([a for a, _ in noul], [b for _, b in noul])
                   if noul else None,
                   "acc_at_0.5": rate(sum((a >= 0.5) == b for a, b in noul), len(noul))},
        "order_bias": {"n": len(spreads),
                       "mean_choice_spread": round(statistics.fmean(spreads), 4)
                       if spreads else None,
                       "argmax_flip_single_order": rate(flip1, len(spreads)),
                       "argmax_flip_4_rotations": rate(flip4, len(spreads)),
                       "mean_abs_dp_none_single": round(statistics.fmean(d_p1), 4)
                       if d_p1 else None,
                       "mean_abs_dp_none_4_rot": round(statistics.fmean(d_p4), 4)
                       if d_p4 else None},
        "date_role_acc": rate(role_ok, role_n), "date_role_n": role_n,
        "property_kind_acc": rate(prop_ok, prop_n), "property_n": prop_n,
        "property_type_acc": rate(type_ok, prop_n),
        "property_type_unknown": rate(type_unknown, prop_n),
        "property_type_wrong": rate(prop_n - type_ok - type_unknown, prop_n),
        "review_reasons": {f"{'gold' if k[0] else 'decoy'}:{k[1]}": v for k, v in sorted(
            Counter((r["gold"]["is_pii"], "+".join(r["rec"]["judge"].get("reasons") or ["error"]))
                    for r in rows if r["rec"]["decision"] == "review").items())},
        "fast_path": {"n": len(fast), "gold": sum(r["gold"]["is_pii"] for r in fast),
                      "by_reason": dict(Counter(r["rec"]["judge"]["fast_path"] for r in fast))},
        "latency": {"calls": len(call_secs), "call_p50": _pct(call_secs, 0.5),
                    "call_p95": _pct(call_secs, 0.95),
                    "cand_p50": _pct([r["secs"] for r in rows], 0.5),
                    "cand_p95": _pct([r["secs"] for r in rows], 0.95),
                    "calls_per_cand": rate(len(call_secs), len(rows)),
                    "cache_share": rate(cache_n, cache_n + prompt_n),
                    "doc_secs_per_1k_chars_p50": _pct(per_1k, 0.5),
                    "doc_secs_per_1k_chars_p95": _pct(per_1k, 0.95)},
    }


def cache_ab(url: str, key: str, docs: list[dict], n: int) -> dict:
    """The same candidates with and without KV prefix reuse (llama-server ``cache_prompt``),
    with the production Judge defaults."""
    out = {}
    for cache in (True, False):
        b = J.LlamaServer(url, key, cache_prompt=cache)
        j = J.Judge(b)
        done = 0
        for d in docs:
            for cand, _ in candidates(d):
                if done >= n:
                    break
                j.judge(d["text"], cand, d.get("column"))
                done += 1
        secs = [c["secs"] for c in b.calls]
        out["cached" if cache else "uncached"] = {
            "calls": len(secs), "mean_call_secs": round(statistics.fmean(secs), 4),
            "prompt_n_mean": round(statistics.fmean(c["prompt_n"] or 0 for c in b.calls), 1)}
    return out


def run_parallel(url: str, key: str, docs: list[dict], workers: int,
                 max_cands: int | None) -> dict:
    """Wall time to judge ``docs`` with ``workers`` concurrent clients (server started -np N),
    with the production Judge defaults."""

    def one(d):
        j = J.Judge(J.LlamaServer(url, key))
        return len(judge_doc(j, d, max_cands))

    t0 = time.perf_counter()
    with ThreadPoolExecutor(workers) as ex:
        n = sum(ex.map(one, docs))
    wall = time.perf_counter() - t0
    return {"workers": workers, "candidates": n, "wall_secs": round(wall, 2),
            "cands_per_sec": round(n / wall, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="zero-shot judge eval on synthetic dev data")
    ap.add_argument("--split", choices=synth.SPLITS, default="dev")
    ap.add_argument("--prod", action="store_true",
                    help="production config: 4 rotations, Choice only, fast path")
    ap.add_argument("--no-fast-path", action="store_true")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="a calibrate.save() file: calibrated confidence and thresholds")
    ap.add_argument("--notes", type=int, default=40)
    ap.add_argument("--cells", type=int, default=0)
    ap.add_argument("--hard", type=int, default=0,
                    help="hard-decoy notes instead (synth.generate_hard): fast-path precision")
    ap.add_argument("--labs", type=int, default=0,
                    help="lab/bill decoy notes instead (synth.generate_labs; test: unseen words)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--max-cands", type=int, default=None, help="per document")
    ap.add_argument("--rotations", type=int, default=None, help="Choice rotations (default all)")
    ap.add_argument("--choice-only", action="store_true",
                    help="skip the advisory Noul and Score questions")
    ap.add_argument("--cache-ab", type=int, default=0, help="candidates for the cache A/B")
    ap.add_argument("--parallel", type=int, default=0, help="also time N concurrent clients")
    ap.add_argument("--url", default=None, help="use a running server (key in SLMJEV_LLM_KEY)")
    ap.add_argument("--port", type=int, default=None, help="default: a free port")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    netguard.forbid_network()
    labels = load_labels()
    if args.prod:
        args.rotations, args.choice_only = args.rotations or 4, True
    if args.hard and args.labs:
        ap.error("--hard and --labs are separate sets; pick one")
    docs = (synth.generate_hard(args.split, args.hard, seed=args.seed) if args.hard else
            synth.generate_labs(args.split, args.labs, seed=args.seed) if args.labs else
            synth.generate(args.split, n_notes=args.notes, n_cells=args.cells, seed=args.seed))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("results") / f"judge_eval_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    srv = None
    if args.url:
        url, key = args.url, os.environ.get("SLMJEV_LLM_KEY")
    else:
        srv = server.start(port=args.port, parallel=max(1, args.parallel), threads=args.threads,
                           log=out.with_suffix(".server.log"))
        url, key = srv.url, srv.key
    try:
        backend = J.LlamaServer(url, key)
        # measure everything: all rotations by default, plus the advisory Noul and Score
        kw = dict(choice_rotations=args.rotations, ask_noul=not args.choice_only,
                  ask_score=not args.choice_only, fast_path=not args.no_fast_path, trace=True)
        model = None if args.url else os.environ.get(server.ENV_MODEL)
        j = (J.Judge.calibrated(backend, args.calibration, model=model, **kw)
             if args.calibration else J.Judge(backend, **kw))
        rows = []
        t0 = time.perf_counter()
        for i, d in enumerate(docs, 1):
            rows += judge_doc(j, d, args.max_cands)
            if i % 5 == 0 or i == len(docs):
                print(f"{i}/{len(docs)} docs, {len(rows)} candidates, "
                      f"{time.perf_counter() - t0:.0f}s", flush=True)
        report = {"model": os.environ.get(server.ENV_MODEL, "(external)"), "split": args.split,
                  "prod": args.prod, "fast_path": not args.no_fast_path,
                  "calibration": str(args.calibration) if args.calibration else None,
                  "seed": args.seed, "notes": args.notes, "cells": args.cells,
                  "rotations": args.rotations or "all", "generator": synth.GENERATOR_VERSION,
                  "prompt": J.PROMPT_VERSION, "hard": args.hard, "labs": args.labs,
                  "thresholds": j.thresholds.__dict__, "choice_only": args.choice_only,
                  "summary": summarize(rows, labels["identifiers"])}
        if args.cache_ab:
            report["cache_ab"] = cache_ab(url, key, docs, args.cache_ab)
        if args.parallel > 1:
            report["parallel"] = [run_parallel(url, key, docs[:8], w, args.max_cands)
                                  for w in (1, args.parallel)]
        report["rows"] = rows
    finally:
        if srv:
            srv.stop()
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
