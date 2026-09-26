"""End-to-end evaluation (P6): the heuristic proposer + the calibrated judge, exactly as
``slmjev.engine`` runs them. Synthetic data only.

Unlike ``eval/judge_eval.py`` (an oracle proposer: gold spans and planted decoys), candidates
here come from ``slmjev.propose``, so misses by the proposer count, and so does every extra span
it proposes. Scoring is by overlap, since a proposed span need not match gold exactly:

- A gold span is **covered** by a returned span when all of its letters and digits fall inside
  that one span. ``recall_flagged`` counts identifier + review spans; ``recall_auto`` counts
  identifier spans only; ``label_ok`` also needs the returned identifier to equal the gold label.
- A returned span is a **false positive** when it overlaps no gold span. ``precision_*`` is the
  share of returned spans that overlap gold.

    set SLMJEV_LLAMA_SERVER=...\\llama-server.exe
    set SLMJEV_JUDGE_MODEL=...\\slmjev-judge-p5-qwen3-1.7b-Q4_K_M.gguf
    python eval/e2e_eval.py --split test --seed 31 --notes 40 --cells 60 \\
        --calibration models/calibration_p5.json
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

from slmjev import engine, netguard, server, synth
from slmjev import judge as J
from slmjev.labels import load_labels

try:  # run as a script (eval/ on sys.path) or imported by tests (repo root on sys.path)
    from judge_eval import _pct, column_outlier
except ImportError:  # pragma: no cover
    from eval.judge_eval import _pct, column_outlier


def _alnum(text: str, s: int, e: int) -> set[int]:
    return {i for i in range(s, e + 1) if text[i - 1].isalnum()} or set(range(s, e + 1))


def covers(rec: dict, text: str, gold: dict) -> bool:
    need = _alnum(text, gold["start"], gold["end"])
    return rec["start"] <= min(need) and max(need) <= rec["end"]


def overlaps(a: dict, b: dict) -> bool:
    return a["start"] <= b["end"] and b["start"] <= a["end"]


def score_doc(doc: dict, recs: list[dict]) -> dict:
    """Per-gold and per-returned-span outcomes for one document."""
    text = doc["text"]
    shown = [r for r in recs if r["decision"] != "not_identifier"]
    gold = []
    for g in doc["spans"]:
        cov = [r for r in shown if covers(r, text, g)]
        auto = [r for r in cov if r["decision"] == "identifier"]
        part = [r for r in shown if overlaps(r, g)]
        gold.append({"label": g["label"], "match": g["match"], "flagged": bool(cov),
                     "auto": bool(auto), "partial": bool(part) and not cov,
                     "label_ok": any(r["identifier"] == g["label"] for r in cov),
                     "got": [(r["match"], r["decision"], r["identifier"]) for r in part]})
    ret = [{"decision": r["decision"], "match": r["match"], "identifier": r["identifier"],
            "sources": r["sources"], "reasons": r["reasons"], "confidence": r["confidence"],
            "gold": any(overlaps(r, g) for g in doc["spans"])} for r in shown]
    return {"gold": gold, "returned": ret, "n_dropped": len(recs) - len(shown)}


def summarize(docs: list[dict], scored: list[dict], secs: list[float],
              identifiers: list[str]) -> dict:
    def rate(n, d):
        return round(n / d, 4) if d else None

    gold = [g for s in scored for g in s["gold"]]
    ret = [r for s in scored for r in s["returned"]]
    direct = [g for g in gold if g["label"] in identifiers]
    auto = [r for r in ret if r["decision"] == "identifier"]
    review = [r for r in ret if r["decision"] == "review"]
    chars = sum(len(d["text"]) for d in docs)
    per_label = defaultdict(Counter)
    for g in gold:
        c = per_label[g["label"]]
        c["n"] += 1
        c["flagged"] += g["flagged"]
        c["auto"] += g["auto"]
        c["label_ok"] += g["label_ok"]
    p_flag = rate(sum(r["gold"] for r in ret), len(ret))
    r_flag = rate(sum(g["flagged"] for g in gold), len(gold))
    per_k = [1000 * s / max(1, len(d["text"])) for d, s in zip(docs, secs, strict=True)]
    return {
        "docs": len(docs), "chars": chars, "gold": len(gold), "returned": len(ret),
        "recall_flagged": r_flag,
        "recall_flagged_direct": rate(sum(g["flagged"] for g in direct), len(direct)),
        "recall_auto": rate(sum(g["auto"] for g in gold), len(gold)),
        "label_accuracy_flagged": rate(sum(g["label_ok"] for g in gold if g["flagged"]),
                                       sum(g["flagged"] for g in gold)),
        "partial": sum(g["partial"] for g in gold),
        "precision_flagged": p_flag,
        "precision_auto": rate(sum(r["gold"] for r in auto), len(auto)),
        "f1_flagged": (round(2 * p_flag * r_flag / (p_flag + r_flag), 4)
                       if p_flag and r_flag else None),
        "false_accepts": sum(not r["gold"] for r in auto),
        "review_rate": rate(len(review), len(ret)),
        "review_per_1k_chars": round(1000 * len(review) / chars, 2) if chars else None,
        "false_review": sum(not r["gold"] for r in review),
        "dropped": sum(s["n_dropped"] for s in scored),
        "secs_per_1k_chars": {"p50": _pct(per_k, 0.5), "p95": _pct(per_k, 0.95)},
        "per_label": {k: {"n": c["n"], "recall_flagged": rate(c["flagged"], c["n"]),
                          "recall_auto": rate(c["auto"], c["n"]),
                          "label_ok": rate(c["label_ok"], c["n"])}
                      for k, c in sorted(per_label.items())},
        "misses": [g for g in gold if not g["flagged"]],
        "false_positives": Counter(
            f"{r['decision']}:{r['identifier']}:{'+'.join(r['sources'])}"
            for r in ret if not r["gold"]).most_common(25),
        "false_accept_examples": [r for r in auto if not r["gold"]][:25],
    }


def scan_doc(judge: J.Judge, doc: dict) -> list[dict]:
    cell = doc.get("kind") == "cell"
    return engine.scan_text(judge, doc["text"], column=doc.get("column") if cell else None,
                            column_outlier=column_outlier(doc), include_dropped=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="proposer + judge, end to end, on synthetic data")
    ap.add_argument("--split", default="dev", choices=["train", "dev", "test"])
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--notes", type=int, default=40)
    ap.add_argument("--cells", type=int, default=60)
    ap.add_argument("--hard", type=int, default=0)
    ap.add_argument("--labs", type=int, default=0)
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--url", default=None, help="use a running server (key in SLMJEV_LLM_KEY)")
    ap.add_argument("--port", type=int, default=None, help="default: a free port")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    netguard.forbid_network()
    docs = (synth.generate_hard(args.split, args.hard, seed=args.seed) if args.hard else
            synth.generate_labs(args.split, args.labs, seed=args.seed) if args.labs else
            synth.generate(args.split, n_notes=args.notes, n_cells=args.cells, seed=args.seed))
    out = args.out or Path("results") / f"e2e_eval_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    srv = None
    if args.url:
        url, key, model = args.url, os.environ.get("SLMJEV_LLM_KEY"), None
    else:
        srv = server.start(port=args.port, threads=args.threads,
                           log=out.with_suffix(".server.log"))
        url, key, model = srv.url, srv.key, os.environ.get(server.ENV_MODEL)
    try:
        judge = J.Judge.calibrated(J.LlamaServer(url, key), args.calibration, model=model,
                                   **engine.PROD)
        scored, secs, t0 = [], [], time.perf_counter()
        for i, d in enumerate(docs, 1):
            t = time.perf_counter()
            recs = scan_doc(judge, d)
            secs.append(time.perf_counter() - t)
            scored.append({"doc": d["id"], **score_doc(d, recs)})
            if i % 5 == 0 or i == len(docs):
                print(f"{i}/{len(docs)} docs, {time.perf_counter() - t0:.0f}s", flush=True)
    finally:
        if srv:
            srv.stop()
    summary = summarize(docs, scored, secs, load_labels()["identifiers"])
    report = {"model": os.environ.get(server.ENV_MODEL, "(external)"), "split": args.split,
              "seed": args.seed, "notes": args.notes, "cells": args.cells, "hard": args.hard,
              "labs": args.labs, "calibration": str(args.calibration),
              "generator": synth.GENERATOR_VERSION, "prompt": J.PROMPT_VERSION,
              "thresholds": judge.thresholds.__dict__, "summary": summary, "docs": scored}
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("false_accept_examples",)}, indent=1, ensure_ascii=False))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
