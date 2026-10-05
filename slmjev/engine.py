"""The ``slm:jev`` detector backend (P6): JSON on stdin, spans on stdout, one process per batch.

The transport matches structured_deidentification's ``app/python/run_engine.py``, so the R side
can call it the same way as its other Python detectors::

    echo {"texts": ["Pt Tan Ah Kow, NRIC S1234567D ..."]} | python -m slmjev.engine

Request (every field but ``texts`` is optional)::

    {"texts": [...],               one string per row (null/empty rows are skipped)
     "kind": "text" | "cells",     "cells": each text is a whole structured cell of ``column``
     "column": "diagnosis",        the column header (shown to the judge for cells only)
     "candidates": [[{start, end, type, detector}], ...],   per row: extra spans to judge
                                    (e.g. Privacy Filter's), 1-based, end inclusive
     "include_dropped": false,     also return spans the judge dropped
     "token_sweep": false}         also propose rare capitalised words (off by default)

Response: a JSON list of span records (``schemas/span.v1.json``), each with the 1-based ``row``.

Per row: ``propose.propose`` proposes candidates (rules, shapes, lexicon and the request's); the
calibrated judge (``Judge.calibrated``) decides each one. Spans inside one the rules already
accepted are not asked. Fail closed: a judgment that fails or is unsure is returned with
``needs_review`` true, never dropped, and so is a rule-found ID the judge would drop. A review
span whose top category is ``none`` still gets the most likely identifier or SHI label, so the
caller has an action to apply.

The token sweep (P25, decision 0022) also proposes each capitalised word the judge model's
vocabulary does not hold in lower case. It is off unless the request sets ``token_sweep`` or the
environment sets ``SLMJEV_TOKEN_SWEEP=1``: on the notes_v25–v27 blind run the judge was badly
calibrated on swept words (ECE 0.29) and they cost the precision gate.

The engine starts its own llama-server on loopback (``slmjev.server``), or uses a running one
(``SLMJEV_LLM_URL`` + ``SLMJEV_LLM_KEY``), and forbids every non-loopback connection first.
Settings come from the request (``llama_server``, ``model``, ``calibration``, ``threads``) or
else the environment: ``SLMJEV_LLAMA_SERVER``, ``SLMJEV_JUDGE_MODEL``, ``SLMJEV_CALIBRATION``
(default ``models/calibration.json``) and ``SLMJEV_THREADS``. ``--probe`` reports whether those
files exist, without starting anything. Any failure exits 2 with ``{"error": ...}`` on stderr
and nothing on stdout: a failed scan must never read as "no spans".

Other Python code (structured_deidentification's ``run_engine.py``) calls :func:`scan`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path

from slmjev import column as col
from slmjev import judge as J
from slmjev import netguard, propose, server
from slmjev.labels import load_labels
from slmjev.vocab import model_words

ENV_CALIBRATION = "SLMJEV_CALIBRATION"
ENV_URL = "SLMJEV_LLM_URL"
ENV_KEY = "SLMJEV_LLM_KEY"
ENV_THREADS = "SLMJEV_THREADS"
ENV_TOKEN_SWEEP = "SLMJEV_TOKEN_SWEEP"
DEFAULT_CALIBRATION = Path("models/calibration.json")
PROD = {"choice_rotations": 4, "ask_noul": False, "ask_score": False, "fast_path": True}

_RANK = {"identifier": 2, "review": 1, "not_identifier": 0}
_FIELDS = ("start", "end", "match", "type", "identifier", "detector", "confidence",
           "p_identifier", "category", "category_probs", "sensitivity", "needs_review")
# a proposer's type hint -> identifier, for a review span the judge could not score; an unsure
# date is treated as the stricter of the two date identifiers
_TYPES = {**load_labels()["types"], "date": "dob"}


def _labels() -> list[str]:
    lab = load_labels()
    return [*lab["identifiers"], *lab["shi"]]


def record(rec: dict, row: int, sources: Sequence[str], labels: Iterable[str]) -> dict:
    """The output record for one judged span (``schemas/span.v1.json``)."""
    out = {k: rec.get(k) for k in _FIELDS}
    if rec["decision"] == "review" and out["identifier"] is None:
        # fail closed: an unsure "none" still names the most likely label to act on; with no
        # probabilities (the judge failed), the proposer's type hint
        probs = rec.get("category_probs") or {}
        best = max((k for k in labels if k in probs), key=lambda k: probs[k], default=None)
        out["identifier"] = best or _TYPES.get(rec.get("type") or "", rec.get("type"))
    out.update(row=row, decision=rec["decision"],
               reasons=list(rec.get("judge", {}).get("reasons", [])),
               errors=list(rec.get("judge", {}).get("errors", [])),
               fast_path=rec.get("judge", {}).get("fast_path"),
               sources=list(sources), context=rec.get("context") or {})
    return out


def resolve(recs: list[dict]) -> list[dict]:
    """Drop a span that sits inside a kept span of equal or higher rank (an identifier covers
    what is inside it; a review span covers inner review spans). Partial overlaps are kept."""
    order = sorted(recs, key=lambda r: (-_RANK[r["decision"]], -(r["end"] - r["start"]),
                                        r["start"]))
    kept: list[dict] = []
    for r in order:
        if any(k["start"] <= r["start"] and r["end"] <= k["end"]
               and _RANK[k["decision"]] >= _RANK[r["decision"]] for k in kept):
            continue
        kept.append(r)
    return sorted(kept, key=lambda r: (r["start"], r["end"]))


def _extra(cands: Iterable[dict] | None, text: str) -> list[J.Candidate]:
    out = []
    for c in cands or ():
        s, e = int(c["start"]), int(c["end"])
        if 1 <= s <= e <= len(text):
            out.append(J.Candidate(s, e, type=c.get("type"), detector=c.get("detector")))
    return out


def model_vocab(model: str | None) -> frozenset[str]:
    """The judge model's lower-case single-token words, for the proposer's token sweep; empty
    (so the sweep proposes more, never less) if the model file cannot be read."""
    return model_words(model) or frozenset()


def scan_text(judge: J.Judge, text: str, *, row: int = 1, column: str | None = None,
              column_outlier: bool = False, extra: Iterable[J.Candidate] = (),
              include_dropped: bool = False, labels: Sequence[str] | None = None,
              vocab: Collection[str] | None = None) -> list[dict]:
    """Propose and judge the spans of one text; ``column`` marks it as a structured cell.
    ``vocab`` (:func:`model_vocab`) turns on the proposer's token sweep."""
    labels = labels if labels is not None else _labels()
    props = propose.propose(text, extra=extra, vocab=vocab)
    # rule-certain spans first: a candidate inside one of them need not be asked
    sure = [p for p in props if J.rule_certain(text, p.candidate())]
    rest = [p for p in props if p not in sure]
    recs, accepted = [], []
    for p in sure + rest:
        if p not in sure and any(propose.contained(p, a) for a in accepted):
            continue
        rec = judge.judge(text, p.candidate(), column, column_outlier)
        if rec["judge"].get("fast_path"):
            accepted.append(p)
        recs.append(record(rec, row, p.sources, labels))
    shown = [r for r in recs if r["decision"] != "not_identifier"]
    dropped = [r for r in recs if r["decision"] == "not_identifier"] if include_dropped else []
    return sorted(resolve(shown) + dropped, key=lambda r: (r["start"], r["end"]))


def check(request: dict) -> None:
    """Raise ValueError for a malformed request, before any model is started."""
    texts = request.get("texts")
    if not isinstance(texts, list):
        raise ValueError("request needs a 'texts' list")
    if request.get("kind", "text") not in ("text", "cells"):
        raise ValueError(f"kind must be 'text' or 'cells', not {request.get('kind')!r}")
    if request.get("candidates") and len(request["candidates"]) != len(texts):
        raise ValueError("'candidates' must have one entry per text")


def run(request: dict, judge: J.Judge, vocab: Collection[str] | None = None) -> list[dict]:
    """All span records for a request (see the module docstring)."""
    check(request)
    texts = request["texts"]
    cands = request.get("candidates") or [None] * len(texts)
    cells = request.get("kind", "text") == "cells"
    column = request.get("column") if cells else None
    outliers = col.date_outliers(texts) if cells else [False] * len(texts)
    labels = _labels()
    out = []
    for i, text in enumerate(texts, 1):
        if not isinstance(text, str) or not text.strip():
            continue
        out += scan_text(judge, text, row=i, column=column, column_outlier=outliers[i - 1],
                         extra=_extra(cands[i - 1], text),
                         include_dropped=bool(request.get("include_dropped")), labels=labels,
                         vocab=vocab)
    return out


def _flag(v) -> bool:
    return v is True or str(v).strip().lower() in {"1", "true", "yes", "on"}


def sweep_vocab(cfg: dict) -> frozenset[str] | None:
    """The vocabulary that turns on the token sweep, or None (the default) to leave it off."""
    return model_vocab(cfg["model"]) if cfg.get("token_sweep") else None


def settings(request: dict | None = None) -> dict:
    """Paths and options: the request's ``llama_server`` / ``model`` / ``calibration`` /
    ``threads`` / ``token_sweep``, else the environment, else the defaults."""
    request = request or {}
    threads = request.get("threads") or os.environ.get(ENV_THREADS)
    return {"llama_server": request.get("llama_server") or os.environ.get(server.ENV_BIN),
            "model": request.get("model") or os.environ.get(server.ENV_MODEL),
            "calibration": str(request.get("calibration") or os.environ.get(ENV_CALIBRATION)
                               or DEFAULT_CALIBRATION),
            "threads": int(threads) if threads else None,
            "token_sweep": _flag(request.get("token_sweep", os.environ.get(ENV_TOKEN_SWEEP))),
            "url": os.environ.get(ENV_URL)}


def probe(request: dict | None = None) -> dict:
    """Whether the files the engine needs exist; starts nothing."""
    cfg = settings(request)
    files = {k: cfg[k] for k in ("llama_server", "model", "calibration")}
    have = {k: bool(v) and Path(v).is_file() for k, v in files.items()}
    if cfg["url"]:
        have["llama_server"] = have["model"] = True  # a running server is used instead
    return {"ok": all(have.values()), "detector": J.DETECTOR, "prompt": J.PROMPT_VERSION,
            "files": files, "found": have}


def scan(request: dict) -> list[dict]:
    """Run a request end to end: forbid the network, start (or reach) the loopback judge,
    judge, stop. Raises on any failure; the caller must not treat that as "no spans"."""
    netguard.forbid_network()
    check(request)
    cfg = settings(request)
    srv = None
    try:
        if cfg["url"]:
            url, key, model = cfg["url"], os.environ.get(ENV_KEY), None
        else:
            srv = server.start(cfg["llama_server"], cfg["model"], threads=cfg["threads"])
            url, key, model = srv.url, srv.key, cfg["model"]
        judge = J.Judge.calibrated(J.LlamaServer(url, key), cfg["calibration"], model=model,
                                   **PROD)
        return run(request, judge, sweep_vocab(cfg))
    finally:
        if srv:
            srv.stop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    netguard.forbid_network()
    if argv[:1] == ["--probe"]:
        print(json.dumps(probe()))
        return 0
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
        spans = scan(request)
    except (OSError, RuntimeError, ValueError, TimeoutError) as e:  # JSONDecodeError too
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), file=sys.stderr)
        return 2
    sys.stdout.buffer.write(json.dumps(spans, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
