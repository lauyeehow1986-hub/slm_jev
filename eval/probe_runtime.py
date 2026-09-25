"""Probe a local OpenAI-compatible GGUF server for what the judge needs (P3 prerequisite).

    set SLMJEV_LLM_URL=http://127.0.0.1:8888    (loopback only; anything else is refused)
    set SLMJEV_LLM_KEY=sk-...                   (never commit it)
    uv run python eval/probe_runtime.py [--model ID] [--n 12] [--out results/probe.json]

Answers, on synthetic text only:
1. logprobs: does /v1/chat/completions return top_logprobs for the first answer token? Does the
   legacy /v1/completions or llama-server's native /completion (n_probs)?
2. pre-sampling: are those probabilities independent of temperature/top_k? The judge needs the
   raw distribution, not the one after the sampler chain truncated it.
3. order bias: a 4-way Choice asked in all 4 cyclic rotations; how far do the per-label
   probabilities move, and how often does the argmax flip?
4. prefix reuse + latency: three questions over one shared prefix; how many prompt tokens are
   re-evaluated (llama-server `timings.prompt_n` / `cache_n`), and wall time per request.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import random
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from slmjev import synth

SYSTEM = ("You label spans in synthetic clinical text. Reply with exactly one option letter "
          "and nothing else.")
LETTERS = "ABCD"


# --- transport (loopback only) ---------------------------------------------------------------


def _check_loopback(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise SystemExit(f"refusing non-loopback endpoint {url!r}: the judge must stay on this machine")


class Client:
    def __init__(self, base: str, key: str | None, model: str | None):
        _check_loopback(base)
        self.base, self.key, self.model = base.rstrip("/"), key, model

    def call(self, path: str, body: dict | None = None, timeout: float = 300) -> tuple[dict, float]:
        req = urllib.request.Request(self.base + path, method="POST" if body else "GET",
                                     data=json.dumps(body).encode() if body else None)
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data, time.perf_counter() - t0

    def chat(self, user: str, **extra) -> tuple[dict, float]:
        body = {"messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": user}],
                "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
                "stream": False,
                # Qwen3: no <think> block, so the first generated token is the answer.
                "chat_template_kwargs": {"enable_thinking": False}} | extra
        if self.model:
            body["model"] = self.model
        return self.call("/v1/chat/completions", body)


def first_token_dist(resp: dict) -> dict[str, float] | None:
    """{token: prob} for the first generated token, or None if the server sent no logprobs."""
    try:
        top = resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError):
        return None
    return {t["token"]: math.exp(t["logprob"]) for t in top}


def option_probs(dist: dict[str, float], letters: str) -> dict[str, float]:
    """Mass on each option letter (merging ' A', 'A' variants), renormalised over the options."""
    mass = {c: 0.0 for c in letters}
    for tok, p in dist.items():
        t = tok.strip().upper()
        if t in mass:
            mass[t] += p
    total = sum(mass.values())
    return {c: (m / total if total else float("nan")) for c, m in mass.items()}


# --- prompts ---------------------------------------------------------------------------------


def _context(doc: dict, span: dict, width: int = 160) -> str:
    s, e = span["start"] - 1, span["end"]
    left, right = doc["text"][max(0, s - width):s], doc["text"][e:e + width]
    return f"{left}[[{doc['text'][s:e]}]]{right}"


def noul_prompt(ctx: str) -> str:
    return (f"Text: {ctx}\n\nDoes the span in [[ ]] identify, or help identify, a person?\n"
            "A) yes\nB) no\nAnswer:")


CHOICE_LABELS = ["national identity number", "person's name", "date",
                 "not personal information"]


def choice_prompt(ctx: str, order: list[str]) -> str:
    opts = "\n".join(f"{LETTERS[i]}) {lab}" for i, lab in enumerate(order))
    return f"Text: {ctx}\n\nWhat kind of item is the span in [[ ]]?\n{opts}\nAnswer:"


def candidates(n: int, seed: int) -> list[tuple[dict, dict, bool]]:
    """(doc, span, is_pii) from the synthetic dev split: half gold spans, half decoys."""
    docs = synth.generate("dev", n_notes=60, n_cells=0, seed=seed)
    gold = [(d, s, True) for d in docs for s in d["spans"]
            if s["type"] in ("nric", "fin", "name", "dob")]
    decoy = [(d, s, False) for d in docs for s in d["decoys"]]
    rng = random.Random(seed)
    return rng.sample(gold, n // 2) + rng.sample(decoy, n - n // 2)


# --- checks ----------------------------------------------------------------------------------


def check_logprobs(c: Client, ctx: str) -> dict:
    out = {}
    try:
        resp, _ = c.chat(noul_prompt(ctx))
        dist = first_token_dist(resp)
        out["chat"] = {"ok": dist is not None,
                       "first_token": resp["choices"][0]["message"].get("content"),
                       "top": sorted(dist.items(), key=lambda kv: -kv[1])[:5] if dist else None}
    except urllib.error.HTTPError as e:
        out["chat"] = {"ok": False, "error": f"HTTP {e.code}: {e.read()[:300]!r}"}
    for path, body in (("/v1/completions", {"prompt": noul_prompt(ctx), "max_tokens": 1,
                                             "logprobs": 20, "temperature": 0}),
                       ("/completion", {"prompt": noul_prompt(ctx), "n_predict": 1,
                                        "n_probs": 20, "temperature": 0})):
        try:
            resp, _ = c.call(path, body | ({"model": c.model} if c.model else {}), timeout=30)
            keys = ("logprobs" in json.dumps(resp)) or ("completion_probabilities" in resp)
            out[path] = {"ok": bool(keys)}
        except urllib.error.HTTPError as e:
            out[path] = {"ok": False, "error": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            out[path] = {"ok": False, "error": str(e.reason)}
        except (TimeoutError, json.JSONDecodeError) as e:
            out[path] = {"ok": False, "error": type(e).__name__}
    return out


def check_pre_sampling(c: Client, ctx: str) -> dict:
    variants = {"t0": {"temperature": 0}, "t1_k40": {"temperature": 1.0, "top_k": 40},
                "t1_k1": {"temperature": 1.0, "top_k": 1}}
    probs = {}
    for name, extra in variants.items():
        dist = first_token_dist(c.chat(noul_prompt(ctx), **extra)[0])
        probs[name] = option_probs(dist, "AB") if dist else None
    vals = [p["A"] for p in probs.values() if p]
    spread = max(vals) - min(vals) if len(vals) == len(variants) else None
    return {"p_yes": probs, "max_spread": spread,
            "pre_sampling": spread is not None and spread < 0.02}


def check_order_bias(c: Client, cands: list) -> dict:
    moves, flips = [], 0
    for doc, span, _ in cands:
        ctx = _context(doc, span)
        runs = []
        for r in range(4):
            order = CHOICE_LABELS[r:] + CHOICE_LABELS[:r]
            dist = first_token_dist(c.chat(choice_prompt(ctx, order))[0]) or {}
            p = option_probs(dist, LETTERS)
            runs.append({order[i]: p[LETTERS[i]] for i in range(4)})
        per_label = [max(r[lab] for r in runs) - min(r[lab] for r in runs)
                     for lab in CHOICE_LABELS]
        moves.append(max(per_label))
        argmaxes = {max(r, key=r.get) for r in runs}
        flips += len(argmaxes) > 1
    return {"n": len(cands), "mean_max_prob_shift": statistics.mean(moves),
            "argmax_flip_rate": flips / len(cands)}


def check_prefix_reuse(c: Client, cands: list) -> dict:
    rows = []
    for doc, span, _ in cands[:4]:
        ctx = _context(doc, span)
        for q in (noul_prompt(ctx), choice_prompt(ctx, CHOICE_LABELS), noul_prompt(ctx)):
            resp, secs = c.chat(q)
            t = resp.get("timings", {})
            u = resp.get("usage", {})
            rows.append({"secs": round(secs, 3), "prompt_tokens": u.get("prompt_tokens"),
                         "prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"),
                         "cached_tokens": (u.get("prompt_tokens_details") or {}).get(
                             "cached_tokens")})
    secs = sorted(r["secs"] for r in rows)
    return {"requests": rows, "p50_secs": statistics.median(secs),
            "p95_secs": secs[min(len(secs) - 1, math.ceil(0.95 * len(secs)) - 1)]}


def check_zero_shot(c: Client, cands: list) -> dict:
    """A first glimpse only: permutation-averaged p(yes) vs gold on the Noul question."""
    rows = []
    for doc, span, is_pii in cands:
        ctx = _context(doc, span)
        p_fwd = option_probs(first_token_dist(c.chat(noul_prompt(ctx))[0]) or {}, "AB")["A"]
        swapped = noul_prompt(ctx).replace("A) yes\nB) no", "A) no\nB) yes")
        p_rev = option_probs(first_token_dist(c.chat(swapped)[0]) or {}, "AB")["B"]
        rows.append({"match": span["match"], "gold": is_pii, "p_fwd": round(p_fwd, 3),
                     "p_rev": round(p_rev, 3), "p_yes": round((p_fwd + p_rev) / 2, 3)})
    acc = statistics.mean((r["p_yes"] >= 0.5) == r["gold"] for r in rows)
    return {"accuracy_at_0.5": acc, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="probe a local GGUF server for judge readiness")
    ap.add_argument("--url", default=os.environ.get("SLMJEV_LLM_URL", "http://127.0.0.1:8888"))
    ap.add_argument("--model", default=os.environ.get("SLMJEV_LLM_MODEL"))
    ap.add_argument("--n", type=int, default=12, help="candidates for bias / zero-shot checks")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", type=Path, default=Path("results") / "probe_runtime.json")
    args = ap.parse_args(argv)

    c = Client(args.url, os.environ.get("SLMJEV_LLM_KEY"), args.model)
    report: dict = {"url": args.url, "model": args.model}
    try:
        report["models"] = [m.get("id") for m in c.call("/v1/models")[0].get("data", [])]
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach {args.url}: {e}") from e
    cands = candidates(args.n, args.seed)
    ctx0 = _context(*cands[0][:2])

    report["logprobs"] = check_logprobs(c, ctx0)
    print("logprobs:", json.dumps({k: v.get("ok") for k, v in report["logprobs"].items()}))
    if not report["logprobs"]["chat"]["ok"]:
        print("chat endpoint returned no logprobs; stopping (the judge cannot run on it).")
    else:
        for name, fn, arg in (("pre_sampling", check_pre_sampling, ctx0),
                              ("order_bias", check_order_bias, cands),
                              ("prefix_reuse", check_prefix_reuse, cands),
                              ("zero_shot", check_zero_shot, cands)):
            report[name] = fn(c, arg)
            summary = {k: v for k, v in report[name].items() if k not in ("rows", "requests")}
            print(f"{name}:", json.dumps(summary))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
