"""P7 benchmark: slm:jev against structured_deidentification's detectors on the same notes.

Systems (``--systems``, comma-separated):

- ``rules``: slm_jev's port of SD's ``detect_r.R`` rules (R-parity tested), run in process.
- ``ner`` (Presidio + spaCy), ``pf`` (Privacy Filter), ``mediphi`` and ``qwen`` (llama.cpp
  LLM pass): SD's own engines, run the way SD runs them, i.e. its bundled Python on
  ``app/python/run_engine.py <mode>`` with JSON on stdin. Those modes forbid the network.
- ``jev``: slm_jev's engine (proposer + calibrated judge), in process, one loopback server.

SD always runs its rules next to an engine, so each SD engine is also scored as
``rules+<engine>`` (the union of both span sets).

``jev+<engine>[+<engine>]`` runs slm:jev with those engines' spans as extra candidates: the
judge decides on them like on its own proposals (P11, ``docs/decisions/0009``). ``<engine>.<type>``
feeds only that engine's spans of that type (``jev+pf+ner.person``). The engines must be run
first in the same call or reused with ``--reuse``; their time is added to jev's.

Scoring is by span overlap, type-agnostic, as in SD's LLM A/B (so its "MediPhi F1 0.889" is
comparable), plus a stricter redaction view:

- **recall** (overlap): a gold span counts when any predicted span overlaps it.
- **covered**: a gold span counts when the union of predicted spans holds all its letters and
  digits, i.e. nothing of it would be left after redaction.
- **precision**: predicted spans that overlap in-view gold, over those plus the spans that
  overlap no gold at all. A span that only overlaps out-of-view gold (a generic date in the
  ``identifiers`` view, an SHI mention) counts neither way.
- Views: ``identifiers`` (the 15 identifiers) and ``with_dates`` (plus ``date_other``, SD's
  scoring). SHI is scored separately, from spans labelled with an SHI category; only slm:jev
  predicts those.
- slm:jev's review spans count as predictions (a reviewer sees them); ``auto`` recall uses its
  identifier spans only.

Synthetic data only. Example::

    set SE_PYTHON=C:\\sds_build\\sds_pf\\bin\\python\\python.exe
    set SLMJEV_SD_ROOT=...\\structured_deidentification\\.claude\\worktrees\\slm-jev-backend
    python eval/bench.py --set eval/bench/sd20.json --systems rules,ner,pf,mediphi,qwen,jev \\
        --pf-model C:\\sds_build\\sds_pf\\models\\pf --llama-cli ...\\llama-cli.exe \\
        --mediphi ...\\mediphi-instruct-q4_k_m.gguf --qwen ...\\qwen2.5-3b-instruct-q4_k_m.gguf \\
        --calibration models/calibration_p5.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from slmjev import calibrate, engine, netguard, rules, server
from slmjev import judge as J
from slmjev.labels import load_labels

_LABELS = load_labels()
IDENTIFIERS = list(_LABELS["identifiers"])
SHI = list(_LABELS["shi"])
DATE_OTHER = "date_other"
VIEWS = {"identifiers": set(IDENTIFIERS), "with_dates": set(IDENTIFIERS) | {DATE_OTHER}}
SD_MODES = {"ner": "ner", "pf": "pf", "mediphi": "llm", "qwen": "llm"}

_MARK = re.compile(r"\{\{([a-z_]+)\|([^{}]+)\}\}")


# --- benchmark sets --------------------------------------------------------------------------

def parse_markup(src: str) -> list[dict]:
    """Notes in the ``notes_v1.txt`` format -> ``[{id, kind, text, spans}]`` with 1-based,
    end-inclusive gold offsets into the unmarked text."""
    known = set(IDENTIFIERS) | set(SHI) | {DATE_OTHER}
    docs, cur = [], None
    for line in src.splitlines():
        # comments: any "#" line before the first note; inside a note only "# ..." (a unit
        # number such as "#05-432" may start a note line)
        if (line.startswith("#") and cur is None) or line == "#" or line.startswith("# "):
            continue
        if m := re.match(r"=== (\S+) \| (.+) ===$", line):
            cur = {"id": m.group(1), "kind": m.group(2), "lines": []}
            docs.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    out = []
    for d in docs:
        raw = "\n".join(d["lines"]).strip("\n")
        text, spans, pos = [], [], 0
        for m in _MARK.finditer(raw):
            text.append(raw[pos:m.start()])
            start = sum(map(len, text)) + 1
            label, val = m.group(1), m.group(2)
            if label not in known:
                raise ValueError(f"{d['id']}: unknown label {label!r}")
            text.append(val)
            spans.append({"start": start, "end": start + len(val) - 1, "match": val,
                          "label": label})
            pos = m.end()
        text.append(raw[pos:])
        body = "".join(text)
        if "{{" in body or "}}" in body:
            raise ValueError(f"{d['id']}: unbalanced markup")
        out.append({"id": d["id"], "kind": d["kind"], "text": body, "spans": spans})
    return out


def load_set(path: Path) -> list[dict]:
    if path.suffix == ".json":
        docs = json.loads(path.read_text(encoding="utf-8"))["docs"]
    else:
        docs = parse_markup(path.read_text(encoding="utf-8"))
    for d in docs:
        for g in d["spans"]:
            if d["text"][g["start"] - 1:g["end"]] != g["match"]:
                raise ValueError(f"{d['id']}: gold offsets do not match {g['match']!r}")
    return docs


# --- systems ---------------------------------------------------------------------------------

def run_rules(docs: list[dict]) -> tuple[list[list[dict]], float]:
    t0 = time.perf_counter()
    preds = [[{**s, "label": s["identifier"]} for s in rules.scan_text(d["text"])] for d in docs]
    return preds, time.perf_counter() - t0


def run_sd(name: str, docs: list[dict], args) -> tuple[list[list[dict]], float]:
    """One SD engine over all notes, exactly one ``run_engine.py`` process (as SD calls it)."""
    req: dict = {"texts": [d["text"] for d in docs]}
    need = [args.sd_python, args.ner_python if name == "ner" else None]
    if name == "pf":
        req["model_dir"] = args.pf_model
        need.append(args.pf_model)
    elif name in ("mediphi", "qwen"):
        req.update(backend="llamacpp", llama_bin=args.llama_cli,
                   model_path=getattr(args, name), n_predict=args.n_predict, ctx=args.ctx,
                   batch=args.batch)
        need += [args.llama_cli, getattr(args, name)]
    # SD's engines swallow errors and return [], which would score as a silent zero
    missing = [str(x) for x in need if x is not None and not Path(x).exists()]
    if missing or (name in ("pf", "mediphi", "qwen") and None in need[2:]):
        raise SystemExit(f"{name}: missing path(s) {missing or 'unset'}")
    script = Path(args.sd_root) / "app" / "python" / "run_engine.py"
    t0 = time.perf_counter()
    python = (args.ner_python or args.sd_python) if name == "ner" else args.sd_python
    p = subprocess.run([python, str(script), SD_MODES[name]], input=json.dumps(req),
                       capture_output=True, text=True, encoding="utf-8", timeout=args.timeout,
                       env=os.environ | {"PYTHONIOENCODING": "utf-8",
                                         "PYTHONDONTWRITEBYTECODE": "1"}, check=False)
    secs = time.perf_counter() - t0
    out = json.loads(p.stdout or "[]")
    if not isinstance(out, list):
        raise RuntimeError(f"{name}: {out}")
    preds: list[list[dict]] = [[] for _ in docs]
    for s in out:
        preds[int(s["row"]) - 1].append({**s, "label": s.get("identifier") or s.get("type")})
    return preds, secs


# what the report keeps of every judged candidate, for the calibration check and for fitting
_JUDGED = ("start", "end", "match", "p_identifier", "confidence", "category", "category_probs",
           "decision", "fast_path", "sources")


def run_jev(docs: list[dict], args, extra: list[list[dict]] | None = None
            ) -> tuple[list[list[dict]], float, list[float], list[list[dict]]]:
    """slm:jev over all notes; ``extra`` holds per-note spans from other systems, which the
    engine judges as additional candidates (``jev+pf+ner``). Also returns every judged
    candidate, dropped ones included, for the calibration check."""
    cfg = engine.settings({"calibration": args.calibration,
                           "token_sweep": getattr(args, "token_sweep", False)})
    srv = server.start(cfg["llama_server"], cfg["model"], threads=cfg["threads"])
    try:
        judge = J.Judge.calibrated(J.LlamaServer(srv.url, srv.key), cfg["calibration"],
                                   model=cfg["model"], **engine.PROD)
        preds, secs, judged = [], [], []
        words = engine.sweep_vocab(cfg)
        for i, d in enumerate(docs, 1):
            t0 = time.perf_counter()
            ex = engine._extra(extra[i - 1], d["text"]) if extra else ()
            recs = engine.scan_text(judge, d["text"], extra=ex, include_dropped=True,
                                    vocab=words)
            secs.append(time.perf_counter() - t0)
            judged.append([{k: r.get(k) for k in _JUDGED} for r in recs])
            preds.append([{**r, "label": r["identifier"]} for r in recs
                          if r.get("decision", "identifier") != "not_identifier"])
            print(f"  jev {i}/{len(docs)} {secs[-1]:.0f}s", file=sys.stderr, flush=True)
        return preds, sum(secs), secs, judged
    finally:
        srv.stop()


# --- scoring ---------------------------------------------------------------------------------

def _overlaps(a: dict, b: dict) -> bool:
    return a["start"] <= b["end"] and b["start"] <= a["end"]


def _covered(text: str, gold: dict, preds: list[dict]) -> bool:
    need = [i for i in range(gold["start"], gold["end"] + 1) if text[i - 1].isalnum()]
    return all(any(p["start"] <= i <= p["end"] for p in preds) for i in need)


def score(docs: list[dict], preds: list[list[dict]], view: set[str]) -> dict:
    """Overlap recall/precision/F1, redaction coverage, per-label recall and false positives
    for one system under one view (see the module docstring)."""
    n_gold = hit = cov = auto = tp = fp = fp_neg = 0
    per = defaultdict(Counter)
    misses, fps = [], []
    for d, ps in zip(docs, preds, strict=True):
        text = d["text"]
        pii_ps = [p for p in ps if p.get("label") not in SHI]
        gold = [g for g in d["spans"] if g["label"] in view]
        for g in gold:
            h = any(_overlaps(p, g) for p in pii_ps)
            c = _covered(text, g, pii_ps)
            a = _covered(text, g, [p for p in pii_ps if p.get("decision", "identifier")
                                   == "identifier"])
            n_gold += 1
            hit += h
            cov += c
            auto += a
            per[g["label"]].update(n=1, recall=h, covered=c)
            if not c:
                misses.append({"doc": d["id"], "label": g["label"], "match": g["match"],
                               "overlapped": h})
        for p in pii_ps:
            if any(_overlaps(p, g) for g in gold):
                tp += 1
            elif not any(_overlaps(p, g) for g in d["spans"]):
                fp += 1
                fp_neg += not d["spans"]
                fps.append({"doc": d["id"], "match": p["match"], "label": p.get("label"),
                            "detector": p.get("detector")})
    rec = hit / n_gold if n_gold else None
    prec = tp / (tp + fp) if tp + fp else None
    f1 = 2 * prec * rec / (prec + rec) if rec and prec else 0.0
    return {"gold": n_gold, "recall": _r(rec), "covered": _r(cov / n_gold if n_gold else None),
            "auto_covered": _r(auto / n_gold if n_gold else None), "precision": _r(prec),
            "f1": _r(f1), "tp": tp, "fp": fp, "fp_on_negative_notes": fp_neg,
            "per_label": {k: {"n": c["n"], "recall": _r(c["recall"] / c["n"]),
                              "covered": _r(c["covered"] / c["n"])}
                          for k, c in sorted(per.items())},
            "misses": misses, "false_positives": fps}


def score_shi(docs: list[dict], preds: list[list[dict]]) -> dict:
    n = hit = right = tp = fp = 0
    for d, ps in zip(docs, preds, strict=True):
        shi_ps = [p for p in ps if p.get("label") in SHI]
        gold = [g for g in d["spans"] if g["label"] in SHI]
        for g in gold:
            got = [p for p in shi_ps if _overlaps(p, g)]
            n += 1
            hit += bool(got)
            right += any(p["label"] == g["label"] for p in got)
        for p in shi_ps:
            if any(_overlaps(p, g) for g in gold):
                tp += 1
            elif not any(_overlaps(p, g) for g in d["spans"]):
                fp += 1
    return {"gold": n, "recall": _r(hit / n if n else None),
            "category_ok": _r(right / n if n else None),
            "precision": _r(tp / (tp + fp) if tp + fp else None), "fp": fp}


def candidates(docs: list[dict], judged: list[list[dict]]) -> list[dict]:
    """Every judged candidate that calibration applies to, with ``correct`` (it overlaps an
    identifier or SHI span) and ``call`` (``calibrate.group_of``, or ``fast_path``). A candidate
    that overlaps only an other date is left out, and so is a failed judgment (no probability)."""
    pos = set(IDENTIFIERS) | set(SHI)
    out = []
    for d, js in zip(docs, judged, strict=True):
        for c in js:
            if c.get("confidence") is None:
                continue
            hit = [g for g in d["spans"] if _overlaps(c, g)]
            if hit and not any(g["label"] in pos for g in hit):
                continue
            call = ("fast_path" if c.get("fast_path")
                    else calibrate.group_of(c.get("category"), SHI, c.get("sources") or ()))
            out.append({**c, "doc": d["id"], "correct": bool(hit), "call": call})
    return out


def calibration(docs: list[dict], judged: list[list[dict]]) -> dict:
    """ECE of ``confidence`` (the calibrated probability that the span is not ``none``) over
    every judged candidate, dropped ones included (see ``candidates``). ``by_call`` splits it by
    what the judge called the span."""
    groups: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for c in candidates(docs, judged):
        for g in ("all", c["call"]):
            groups[g][0].append(min(max(float(c["confidence"]), 0.0), 1.0))
            groups[g][1].append(c["correct"])

    def one(ps, ys):
        return {"n": len(ps), "positives": sum(ys), "ece": _r(calibrate.ece(ps, ys))}
    out = one(*groups.pop("all")) if "all" in groups else {"n": 0, "positives": 0, "ece": None}
    out["by_call"] = {g: one(*v) for g, v in sorted(groups.items())}
    return out


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def union(a: list[list[dict]], b: list[list[dict]]) -> list[list[dict]]:
    return [x + y for x, y in zip(a, b, strict=True)]


# --- main ------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    env = os.environ.get
    ap = argparse.ArgumentParser(description="P7: slm:jev vs SD's detectors (synthetic only)")
    ap.add_argument("--set", type=Path, required=True)
    ap.add_argument("--systems", default="rules,ner,pf,mediphi,qwen,jev")
    ap.add_argument("--sd-root", default=env("SLMJEV_SD_ROOT"))
    ap.add_argument("--sd-python", default=env("SE_PYTHON"))
    ap.add_argument("--ner-python", default=None,
                    help="interpreter for ner when --sd-python lacks a spaCy model")
    ap.add_argument("--pf-model", default=None)
    ap.add_argument("--llama-cli", default=None)
    ap.add_argument("--mediphi", default=None)
    ap.add_argument("--qwen", default=None)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--n-predict", type=int, default=1024)
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--calibration", default=None)
    ap.add_argument("--token-sweep", action="store_true",
                    help="propose rare capitalised words too (P25; off by default)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--reuse", type=Path, default=None,
                    help="earlier report on the same set: keep its systems not re-run here")
    args = ap.parse_args(argv)

    netguard.forbid_network()
    docs = load_set(args.set)
    chars = sum(len(d["text"]) for d in docs)
    systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    preds: dict[str, list[list[dict]]] = {}
    timing: dict[str, dict] = {}
    judged: dict[str, list[list[dict]]] = {}
    if args.reuse:
        old = json.loads(args.reuse.read_text(encoding="utf-8"))
        if old["docs"] != len(docs) or old["chars"] != chars:
            raise SystemExit(f"--reuse {args.reuse} was run on a different set")
        for name, p in old["predictions"].items():
            if name in systems or name.startswith("rules+"):
                continue
            preds[name] = p
            timing.update({k: v for k, v in old["timing"].items()
                           if k.split(":")[0] == name})
    for name in systems:
        print(f"{name} ...", file=sys.stderr, flush=True)
        if name == "rules":
            p, secs = run_rules(docs)
        elif name == "jev" or name.startswith("jev+"):
            # jev+pf+ner: those systems' spans (run or reused above) become extra candidates;
            # ``ner.person`` feeds only that system's spans of that type
            feeds = [(f.split(".")[0], set(f.split(".")[1:])) for f in name.split("+")[1:]]
            if any(f not in preds for f, _ in feeds):
                raise SystemExit(f"{name}: run or --reuse {[f for f, _ in feeds]} first")
            extra = ([[{**s, "detector": f} for f, types in feeds for s in preds[f][i]
                       if not types or s.get("type") in types]
                      for i in range(len(docs))] if feeds else None)
            p, secs, per_doc, judged[name] = run_jev(docs, args, extra)
            # the feeding systems' time counts too
            secs += sum(timing[f]["secs"] for f, _ in feeds)
            timing[name + ":per_doc"] = {"p50": _pct(per_doc, docs, 0.5),
                                         "p95": _pct(per_doc, docs, 0.95)}
        elif name in SD_MODES:
            p, secs = run_sd(name, docs, args)
        else:
            raise SystemExit(f"unknown system {name!r}")
        preds[name] = p
        timing[name] = {"secs": round(secs, 1), "secs_per_1k_chars": round(1000 * secs / chars, 2)}
    if "rules" not in preds:
        preds["rules"] = run_rules(docs)[0]
    for name in [s for s in list(preds) if s in SD_MODES]:
        preds[f"rules+{name}"] = union(preds["rules"], preds[name])

    report = {"set": str(args.set), "docs": len(docs), "chars": chars,
              "negative_notes": sum(not d["spans"] for d in docs),
              "gold_by_label": dict(Counter(g["label"] for d in docs for g in d["spans"])),
              "llm": {"batch": args.batch, "n_predict": args.n_predict, "ctx": args.ctx},
              "timing": timing,
              "calibration": {name: calibration(docs, j) for name, j in judged.items()},
              "results": {name: {**{v: score(docs, p, labels) for v, labels in VIEWS.items()},
                                 "shi": score_shi(docs, p)}
                          for name, p in preds.items()},
              "predictions": {name: p for name, p in preds.items()},
              "judged": judged}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("results") / f"bench_{args.set.stem}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(table(report))
    print(f"wrote {out}")
    return 0


def _pct(secs: list[float], docs: list[dict], q: float) -> float:
    per_k = sorted(1000 * s / max(1, len(d["text"])) for s, d in zip(secs, docs, strict=True))
    return round(per_k[min(len(per_k) - 1, int(q * len(per_k)))], 1)


def table(report: dict) -> str:
    rows = ["| system | view | recall | covered | precision | F1 | FP (neg) | SHI recall |",
            "|---|---|---|---|---|---|---|---|"]
    for name, res in report["results"].items():
        for v in VIEWS:
            s = res[v]
            rows.append(f"| {name} | {v} | {s['recall']} | {s['covered']} | {s['precision']} | "
                        f"{s['f1']} | {s['fp']} ({s['fp_on_negative_notes']}) | "
                        f"{res['shi']['recall']} |")
    return "\n".join(rows)


if __name__ == "__main__":
    raise SystemExit(main())
