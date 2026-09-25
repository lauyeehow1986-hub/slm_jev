"""The System One judge: typed questions answered with probabilities, never generated text.

For each candidate span, code builds one shared prefix, the candidate marked ``[[like this]]``
inside a context window, and asks several typed questions over it:

- **Choice**: which label, over a *family* of options that code picks from the span's surface
  shape (date / code / numeric / alnum / text). Every option always includes ``none``.
  ``p_identifier = 1 - P(none)``.
- **Noul**: an optional yes/no cross-check, "is this personal data about a person?". It only
  affects decisions when ``Thresholds.disagree_at`` is set.
- **Score**: optional sensitivity on an ordinal scale, read as a probability-weighted position.
- **Context**: the property kind of an address. The date role comes from the Choice itself. The
  policy's ``by`` rules resolve from these.

Probabilities are the option-letter mass of the first answer token, from the backend's logprobs.
Letter-order bias is large on small models (docs/decisions/0002), so every question is asked in
several option orders and averaged. Choice uses evenly spaced cyclic rotations (all of them when
``choice_rotations`` is None, so every option takes every position once); Noul and Score use
forward + reversed order.

Decisions are made here, in code, from those probabilities. The model only ever answers with a
letter for options code gave it. Fail closed: a failed or off-format answer, an uncertain
probability or a low-confidence category (and, when enabled, Choice/Noul disagreement) all set
``needs_review``.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from slmjev import rules
from slmjev.netguard import check_loopback_url

DETECTOR = "slm:jev"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWX"

SYSTEM = ("You classify one marked span in clinical or administrative text from Singapore. "
          "Reply with exactly one option letter and nothing else.")

# --- option catalogue ----------------------------------------------------------------------

# Short option texts: in the P3 wording study they beat one-line definitions on category accuracy
# (0.88 vs 0.66), separation and latency, since every option order re-evaluates the whole list.
OPTION_TEXT: dict[str, str] = {
    "name": "person's name",
    "national_id": "NRIC / FIN / passport number",
    "mrn": "medical record number",
    "case_visit": "case or visit number",
    "address": "street address",
    "postal_code": "postal code",
    "phone": "phone number",
    "fax": "fax number",
    "email": "email address",
    "dob": "date of birth",
    "date_of_death": "date of death",
    "device": "device serial number",
    "biometric": "biometric",
    "photo": "photo",
    "other_id": "other ID (account, card, vehicle, licence, URL, IP)",
    "hiv_sti": "HIV or STI",
    "mental_health": "mental illness",
    "substance_use": "drug or alcohol use",
    "genetic": "genetic condition",
    "reproductive_sexual": "pregnancy or sexual health",
    "other_sensitive": "other sensitive condition",
}

NONE_TEXT: dict[str, str] = {
    "date": "other date (admission, visit, procedure, test, letter)",
    "code": "not personal (lab value, dose, code, ward/bed, duration like 3/7)",
    "numeric": "not personal (other date, lab value, dose, measurement)",
    "text": "not personal (ordinary words)",
    "alnum": "not personal (drug, test, ward/bed, measurement)",
    "all": "not personal",
}
_DATES = ["dob", "date_of_death"]
_CODES = ["national_id", "mrn", "case_visit", "phone", "fax", "postal_code", "email", "device",
          "other_id"]
_SHI = ["hiv_sti", "mental_health", "substance_use", "genetic", "reproductive_sexual",
        "other_sensitive"]

FAMILIES: dict[str, list[str]] = {
    "date": [*_DATES, "none"],
    "code": [*_CODES, "none"],
    "text": ["name", "address", *_SHI, "none"],
    "numeric": [*_CODES, *_DATES, "none"],
    "alnum": ["name", *_CODES, "address", *_SHI, "none"],
    "all": [*OPTION_TEXT, "none"],
}

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_DATE_SHAPES = [
    re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$"),
    re.compile(r"^\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}$"),
    re.compile(rf"^\d{{1,2}}(?:st|nd|rd|th)?[ \-]{_MONTH}[ \-,]*\d{{2,4}}$", re.IGNORECASE),
    re.compile(rf"^{_MONTH}[ \-]\d{{1,2}}(?:st|nd|rd|th)?[ \-,]*\d{{2,4}}$", re.IGNORECASE),
]


def family_of(match: str) -> str:
    """The option family for a span, from its surface shape only (never from a gold label)."""
    s = match.strip()
    if any(p.match(s) for p in _DATE_SHAPES):
        return "date"
    if "@" in s or re.match(r"(?:https?://|www\.)", s, re.IGNORECASE):
        return "code"
    if re.fullmatch(r"\d{8}", s) and rules.parse_compact_date(s):
        return "numeric"  # 8 digits: a compact date, or a phone/MRN
    if not re.search(r"\d", s):
        return "text"
    if re.search(r"[A-Za-z]{3,}", s):
        return "alnum"  # "Blk 123 Ang Mo Kio Ave 3", "HIV-1", "BRCA1 carrier"
    return "numeric" if re.search(r"\s", s) else "code"


# Property kind of an address, for policies that treat landed and non-landed homes differently.
PROPERTY_OPTIONS: dict[str, str] = {
    "hdb": "a public housing (HDB) flat",
    "condo": "a private condominium or apartment",
    "landed": "a landed house (terrace, semi-detached or bungalow)",
    "unknown": "cannot tell from the text",
}
PROPERTY_TYPE = {"hdb": "non_landed", "condo": "non_landed", "landed": "landed"}

SCORE_OPTIONS = ["none", "low", "moderate", "high"]

# Stems name the span (`{span}`); naming it beat relying on the [[ ]] marker alone.
CHOICE_STEM = 'In this text, what is "{span}"?'
NOUL_STEM = ('In this text, is "{span}" personal data about a specific person (a name, ID or '
             "record number, contact detail, address, birth or death date, or a sensitive health "
             "condition)?")
SCORE_STEM = 'In this text, how much harm could disclosing "{span}" do to the person it is about?'
PROPERTY_STEM = "What kind of home is the address in this text?"


# --- backend -------------------------------------------------------------------------------


class JudgeError(RuntimeError):
    """The backend failed or answered off-format; the judgment must go to review."""


class Backend(Protocol):
    def first_token(self, prefix: str, question: str) -> Mapping[str, float]:
        """Top probabilities of the first answer token for ``prefix + question``."""
        ...


@dataclass
class LlamaServer:
    """A llama-server on loopback, via its OpenAI-compatible chat endpoint (stdlib only)."""

    url: str
    key: str | None = None
    model: str | None = None
    timeout: float = 120
    top_logprobs: int = 20
    cache_prompt: bool = True
    calls: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        check_loopback_url(self.url)
        self.url = self.url.rstrip("/")

    def first_token(self, prefix: str, question: str) -> dict[str, float]:
        body = {"messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prefix + question}],
                "max_tokens": 1, "temperature": 0, "logprobs": True,
                "top_logprobs": self.top_logprobs, "stream": False,
                "cache_prompt": self.cache_prompt,
                # Qwen3: no <think> block, so the first generated token is the answer.
                "chat_template_kwargs": {"enable_thinking": False}}
        if self.model:
            body["model"] = self.model
        req = urllib.request.Request(self.url + "/v1/chat/completions", method="POST",
                                     data=json.dumps(body).encode("utf-8"))
        req.add_header("Content-Type", "application/json")
        if self.key:
            req.add_header("Authorization", f"Bearer {self.key}")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            raise JudgeError(f"backend call failed: {type(e).__name__}: {e}") from e
        secs = time.perf_counter() - t0
        t = resp.get("timings") or {}
        self.calls.append({"secs": secs, "prompt_n": t.get("prompt_n"),
                           "cache_n": t.get("cache_n")})
        try:
            top = resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        except (KeyError, IndexError, TypeError) as e:
            raise JudgeError("backend returned no first-token logprobs") from e
        return {t["token"]: math.exp(t["logprob"]) for t in top}


# --- probability readout -------------------------------------------------------------------


def letter_mass(dist: Mapping[str, float], letters: str) -> dict[str, float]:
    """Raw probability mass on each option letter, merging variants like ' A' and 'A)'."""
    mass = dict.fromkeys(letters, 0.0)
    for tok, p in dist.items():
        t = tok.strip().rstrip(").:").upper()
        if t in mass:
            mass[t] += p
    return mass


def rotations(n: int, k: int | None = None) -> list[list[int]]:
    """``k`` cyclic rotations of ``range(n)`` at an even stride (all ``n`` when ``k`` is None)."""
    k = n if k is None else max(1, min(k, n))
    return [[(i + s) % n for i in range(n)] for s in (round(j * n / k) for j in range(k))]


def forward_reverse(n: int) -> list[list[int]]:
    return [list(range(n)), list(range(n))[::-1]]


@dataclass
class Answer:
    """One typed question, averaged over option orders."""

    probs: dict[str, float]
    spread: float  # largest per-option move across orders
    min_mass: float  # smallest letter mass seen (low = the model did not answer with a letter)
    per_order: list[dict[str, float]]

    @property
    def top(self) -> tuple[str, float]:
        k = max(self.probs, key=self.probs.__getitem__)
        return k, self.probs[k]


def ask(backend: Backend, prefix: str, stem: str, options: Mapping[str, str],
        orders: Iterable[Sequence[int]], min_mass: float = 0.5) -> Answer:
    keys = list(options)
    per_order, masses = [], []
    for order in orders:
        shown = [keys[i] for i in order]
        lines = "\n".join(f"{LETTERS[j]}) {options[k]}" for j, k in enumerate(shown))
        dist = backend.first_token(prefix, f"{stem}\n{lines}\nAnswer:")
        mass = letter_mass(dist, LETTERS[:len(shown)])
        total = sum(mass.values())
        masses.append(total)
        if total < min_mass:
            raise JudgeError(f"answer off-format: letter mass {total:.2f} < {min_mass}")
        per_order.append({k: mass[LETTERS[j]] / total for j, k in enumerate(shown)})
    if not per_order:
        raise JudgeError("no option orders to ask")
    probs = {k: statistics.fmean(r[k] for r in per_order) for k in keys}
    spread = max(max(r[k] for r in per_order) - min(r[k] for r in per_order) for k in keys)
    return Answer(probs, spread, min(masses), per_order)


# --- the judge -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A proposed span: 1-based ``start``, inclusive ``end`` (the span contract)."""

    start: int
    end: int
    type: str | None = None
    detector: str | None = None
    family: str | None = None


@dataclass(frozen=True)
class Thresholds:
    """Zero-shot defaults; P4 replaces them with calibrated ones."""

    drop_below: float = 0.05  # p_identifier below this: not an identifier, no review
    accept_at: float = 0.80  # at or above this (with a confident category): accept
    category_at: float = 0.60  # the top category must reach this to accept
    # |p_noul - p_identifier| at or above this: review. None: Noul is advisory only (the P3
    # default, since zero-shot Noul on Qwen3-1.7B was near chance, AUROC 0.51-0.69).
    disagree_at: float | None = None
    min_mass: float = 0.50  # letter mass below this: off-format, review


def context_window(text: str, start: int, end: int, width: int = 160) -> str:
    """The span marked ``[[...]]`` with up to ``width`` characters either side."""
    s, e = start - 1, end

    def clean(x: str) -> str:
        return x.replace("[[", "[ [").replace("]]", "] ]")

    left, right = text[max(0, s - width):s], text[e:e + width]
    lead = "..." if s - width > 0 else ""
    tail = "..." if e + width < len(text) else ""
    return f"{lead}{clean(left)}[[{clean(text[s:e])}]]{clean(right)}{tail}"


def prefix_for(text: str, cand: Candidate, width: int = 160) -> str:
    return f"Text:\n{context_window(text, cand.start, cand.end, width)}\n\n"


@dataclass
class Judge:
    backend: Backend
    width: int = 160
    # Evenly spaced cyclic rotations of the Choice options; None asks all of them. P3 measured
    # 4 against all rotations on the dev split (docs/decisions/0003).
    choice_rotations: int | None = 4
    thresholds: Thresholds = field(default_factory=Thresholds)
    calibrator: Callable[[float], float] | None = None
    ask_noul: bool = False  # advisory unless thresholds.disagree_at is set
    ask_score: bool = False  # advisory sensitivity (docs/decisions/0003)
    ask_context: bool = True
    trace: bool = False

    def judge(self, text: str, cand: Candidate) -> dict:
        match = text[cand.start - 1:cand.end]
        family = cand.family or family_of(match)
        rec: dict = {"start": cand.start, "end": cand.end, "match": match, "type": cand.type,
                     "identifier": None, "detector": DETECTOR, "confidence": None,
                     "p_identifier": None, "category": None, "category_probs": {},
                     "sensitivity": None, "needs_review": True, "decision": "review",
                     "context": {}, "judge": {"family": family, "errors": []}}
        info = rec["judge"]
        prefix = prefix_for(text, cand, self.width)
        span = match.replace('"', "'").replace("\n", " ")
        th = self.thresholds
        try:
            opts = {k: (NONE_TEXT[family] if k == "none" else OPTION_TEXT[k])
                    for k in FAMILIES[family]}
            choice = ask(self.backend, prefix, CHOICE_STEM.format(span=span), opts,
                         rotations(len(opts), self.choice_rotations), th.min_mass)
        except JudgeError as e:
            info["errors"].append(f"choice: {e}")
            return rec
        cat, cat_p = choice.top
        p_id = 1.0 - choice.probs["none"]
        rec.update(category=cat, category_probs=_round(choice.probs), p_identifier=round(p_id, 4),
                   identifier=None if cat == "none" else cat,
                   type=cand.type or (None if cat == "none" else cat))
        conf = self.calibrator(p_id) if self.calibrator else p_id
        rec["confidence"] = round(conf, 4)
        info.update(choice_spread=round(choice.spread, 4), choice_mass=round(choice.min_mass, 4),
                    n_orders=len(choice.per_order))
        if self.trace:
            info["choice_orders"] = [_round(r) for r in choice.per_order]

        reasons = []
        p_noul = None
        if self.ask_noul:
            try:
                noul = ask(self.backend, prefix, NOUL_STEM.format(span=span),
                           {"yes": "yes", "no": "no"}, forward_reverse(2), th.min_mass)
                p_noul = noul.probs["yes"]
                info["p_noul"] = round(p_noul, 4)
                if th.disagree_at is not None and abs(p_noul - p_id) >= th.disagree_at:
                    reasons.append("choice_noul_disagree")
            except JudgeError as e:
                info["errors"].append(f"noul: {e}")
                if th.disagree_at is not None:
                    reasons.append("noul_failed")

        if self.ask_score:
            try:
                sc = ask(self.backend, prefix, SCORE_STEM.format(span=span),
                         {k: k for k in SCORE_OPTIONS}, forward_reverse(len(SCORE_OPTIONS)),
                         th.min_mass)
                pos = sum(i * sc.probs[k] for i, k in enumerate(SCORE_OPTIONS))
                rec["sensitivity"] = round(pos / (len(SCORE_OPTIONS) - 1), 4)
            except JudgeError as e:
                info["errors"].append(f"score: {e}")  # sensitivity is advisory; no review

        ctx = rec["context"]
        if cat in _DATES or (family == "date" and cat == "none"):
            role = cat if cat in _DATES else "other"
            ctx["date_role"] = role if cat_p >= th.category_at else "unknown"
        if self.ask_context and (choice.probs.get("address", 0) + choice.probs.get(
                "postal_code", 0)) >= 0.2:
            try:
                pk = ask(self.backend, prefix, PROPERTY_STEM, PROPERTY_OPTIONS,
                         rotations(len(PROPERTY_OPTIONS)), th.min_mass)
                kind, kind_p = pk.top
                kind = kind if kind_p >= th.category_at else "unknown"
                ctx["property_kind"] = kind
                ctx["property_type"] = PROPERTY_TYPE.get(kind, "unknown")
            except JudgeError as e:
                info["errors"].append(f"property: {e}")
                ctx["property_kind"] = ctx["property_type"] = "unknown"

        if p_id < th.drop_below and not reasons:
            decision = "not_identifier"
        elif p_id >= th.accept_at and cat_p >= th.category_at and not reasons:
            decision = "identifier"
        else:
            decision = "review"
            if th.drop_below <= p_id < th.accept_at:
                reasons.append("uncertain")
            if cat_p < th.category_at:
                reasons.append("category_uncertain")
        rec["decision"] = decision
        rec["needs_review"] = decision == "review"
        info["reasons"] = reasons
        return rec

    def judge_many(self, text: str, cands: Iterable[Candidate]) -> list[dict]:
        return [self.judge(text, c) for c in cands]


def _round(d: Mapping[str, float], nd: int = 4) -> dict[str, float]:
    return {k: round(v, nd) for k, v in d.items()}
