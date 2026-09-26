"""The System One judge: typed questions answered with probabilities, never generated text.

For each candidate span, code builds one shared prefix, the candidate marked ``[[like this]]``
inside a context window, and asks several typed questions over it:

- **Choice**: which label, over a *family* of options that code picks from the span's surface
  shape (date / code / numeric / alnum / text). Every option always includes ``none``.
  ``p_identifier = 1 - P(none)``.
- **Noul**: an optional yes/no cross-check, "is this personal data about a person?". It only
  affects decisions when ``Thresholds.disagree_at`` is set.
- **Score**: optional sensitivity on an ordinal scale, read as a probability-weighted position.
- **Context** for the policy's ``by`` rules: the date role comes from the Choice itself, and an
  address's property kind/type from address rules (optionally the model when rules cannot tell).

Rule-certain spans (a valid NRIC/FIN checksum, an email, a URL, a postal code after
``Singapore`` or a street address, an ID after an ID keyword) skip the model entirely
(``rule_certain``). A structured cell's column header joins the prefix, and a whole cell that is
ID-shaped, date-shaped outside a date column, or a date far from the rest of its column
(``slmjev.column``) is never dropped (``cell_review``). A compact date after a date word or in a
date column is asked as a date (``family_in_context``).
Decisions compare the *calibrated* probability (``Judge.calibrator``, fitted in P4 by
``slmjev.calibrate``) with the thresholds.

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
import os
import re
import statistics
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from slmjev import calibrate, rules
from slmjev.netguard import check_loopback_url

DETECTOR = "slm:jev"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWX"
RULE_CONFIDENCE = 0.99  # a rule-certain span, as rules.scan_text scores a validated hit

# Bump on any change to the prompts, options or family routing: a calibration fitted for one
# version is refused by another (``Judge.calibrated``).
#   1: P3/P4.  2: "Answer with one letter:" cue, ``fraction`` family, compact dates in context.
PROMPT_VERSION = 2
# The answer cue. A bare "Answer:" was echoed ("Answer") instead of a letter when ``none`` sat at
# letter E, the off-format answers of P4; the explicit cue gave full letter mass on dev.
ANSWER_CUE = "Answer with one letter:"

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
    "fraction": "not personal (duration like 3/7, BP like 120/80, ratio or score)",
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
    # n/m: a duration (3/7), a BP or a ratio. No ID has this shape, and offering the ID options
    # let the model call a duration a case number (P4).
    "fraction": [*_DATES, "none"],
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
    if re.fullmatch(r"\d{1,3}/\d{1,3}", s):
        return "fraction"
    if "@" in s or re.match(r"(?:https?://|www\.)", s, re.IGNORECASE):
        return "code"
    if re.fullmatch(r"\d{8}", s) and rules.parse_compact_date(s):
        return "numeric"  # 8 digits: a compact date, or a phone/MRN
    if not re.search(r"\d", s):
        return "text"
    if re.search(r"[A-Za-z]{3,}", s):
        return "alnum"  # "Blk 123 Ang Mo Kio Ave 3", "HIV-1", "BRCA1 carrier"
    return "numeric" if re.search(r"\s", s) else "code"


# --- rule-certain spans and address context -------------------------------------------------

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_POSTAL_LEAD = re.compile(r"(?:\bSingapore\s*|\bS\(?)$")
# The end of a street address: a block, a #floor-unit, or a street-type word, then only
# address-like tokens (numbers, units, Title-case building words) before the postal code. A
# lower-case or all-caps word ("Block 4 clinic, platelets 245000", "PLT") breaks the address.
_STREET_TYPES = (r"Blk|Block|Avenue|Ave|Road|Rd|Street|Drive|Crescent|Cres|Lorong|Lor|Jalan|Jln|"
                 r"Lane|Walk|Close|Place|Terrace|Rise|Link|Way|Grove|View|Heights|Green|Gardens?|"
                 r"Circle|Loop|Boulevard|Quay|Vale|Hill|Park")
_ADDRESS_TOKEN = r"(?:\d{1,4}[A-Z]?|#\s?\d{1,3}-\d{1,5}|[A-Z][a-z][A-Za-z'&-]*)"
_ADDRESS_END = re.compile(rf"(?:\b(?i:{_STREET_TYPES})\b\.?|#\s?\d{{1,3}}-\d{{1,5}})"
                          rf"(?:[ ,]+{_ADDRESS_TOKEN}){{0,6}}[ ,]+$")
_SG_SECTORS = {f"{i:02d}" for i in range(1, 83)}
# An ID keyword right before the span. Acronyms are case-sensitive ("IC", not "ic").
_ID_TAIL = r"\s*(?:[Nn]o\.?|[Nn]umber|#)?\s*[:(]?\s*$"
_ID_LEADS = [
    (re.compile(r"(?:\bNRIC|\bFIN|\b[Tt]emp(?:orary)? IC|\bIC|\b[Pp]assport|"
                rf"\b[Bb]irth [Cc]ert(?:ificate)?){_ID_TAIL}"), "national_id"),
    (re.compile(rf"\bMRN{_ID_TAIL}"), "mrn"),
    (re.compile(rf"\bMCR{_ID_TAIL}"), "other_id"),
]
_ID_SHAPE = re.compile(r"[A-Z]{0,2}\d{5,}[A-Z]?")
# A bank or billing account number after an account keyword: 8+ digits, optionally grouped by
# hyphens or spaces (``250-03851-0``). The model called these ``none`` with certainty (0005).
_ACCOUNT_LEAD = re.compile(rf"(?:\b[Aa]ccount|\b[Aa]/[Cc]|\b[Aa]cct){_ID_TAIL}")
_ACCOUNT_SHAPE = re.compile(r"\d+(?:[- ]\d+){0,4}")


def rule_certain(text: str, cand: Candidate) -> tuple[str, str] | None:
    """``(identifier, reason)`` when code alone is sure the span identifies someone, else None.

    Only shapes a rule can confirm qualify: a valid NRIC/FIN checksum; a whole email or URL; a
    6-digit postal code right after ``Singapore``/``S(``, or ending a street address with a valid
    sector; an ID-shaped token right after an ID keyword (``NRIC``, ``temp IC``, ``MRN``, ...),
    or an account number right after ``account`` / ``a/c``.
    They skip the model; everything else is judged. Accepting is the fail-closed direction, since
    it only ever removes more."""
    m = text[cand.start - 1:cand.end].strip()
    left = text[max(0, cand.start - 1 - 80):cand.start - 1]
    if rules.nric_valid(m):
        return "national_id", "nric_checksum"
    if _EMAIL.fullmatch(m):
        return "email", "email_shape"
    if _URL.fullmatch(m):
        return "other_id", "url_shape"
    if re.fullmatch(r"\d{6}", m):
        if _POSTAL_LEAD.search(left[-12:]):
            return "postal_code", "postal_keyword"
        if m[:2] in _SG_SECTORS and _ADDRESS_END.search(left):
            return "postal_code", "postal_after_address"
    if _ID_SHAPE.fullmatch(m):
        for lead, ident in _ID_LEADS:
            if lead.search(left[-24:]):
                return ident, "id_keyword"
    if (_ACCOUNT_SHAPE.fullmatch(m) and sum(c.isdigit() for c in m) >= 8
            and _ACCOUNT_LEAD.search(left[-24:])):
        return "other_id", "account_keyword"
    return None


_BLOCK = re.compile(r"\b(?:Blk|Block)\s*\d+[A-Za-z]?\b", re.IGNORECASE)
_UNIT = re.compile(r"#\s?\d{1,3}-\d{1,5}\b")


def address_context(text: str, cand: Candidate, category: str) -> dict[str, str]:
    """Property context from the address text itself; ``unknown`` whenever rules cannot tell.

    ``Blk``/``Block`` marks public housing and a ``#NN-NN`` unit marks a non-landed home. Nothing
    in an address proves a home is landed, so rules never say ``landed``."""
    if category == "address":
        s = text[cand.start - 1:cand.end]
    else:  # a postal code: the address it follows, on the same line
        left = text[max(0, cand.start - 1 - 120):cand.start - 1]
        s = left.rsplit("\n", 1)[-1]
    if _BLOCK.search(s):
        return {"property_kind": "hdb", "property_type": "non_landed"}
    if _UNIT.search(s):
        return {"property_kind": "unknown", "property_type": "non_landed"}
    return {"property_kind": "unknown", "property_type": "unknown"}


# Property kind of an address, for policies that treat landed and non-landed homes differently.
# Asked of the model only when rules cannot tell and ``Judge.ask_property`` is on: zero-shot it
# answered 0/12 correctly in P3 (docs/decisions/0003).
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


def question(stem: str, options: Mapping[str, str], shown: Sequence[str]) -> str:
    """The question for one option order: stem, lettered options, answer cue. The finetune data
    (``slmjev.sft``) is built with this too, so training and inference prompts cannot drift."""
    lines = "\n".join(f"{LETTERS[j]}) {options[k]}" for j, k in enumerate(shown))
    return f"{stem}\n{lines}\n{ANSWER_CUE}"


def choice_options(family: str) -> dict[str, str]:
    """The Choice options offered to a span of ``family``, in catalogue order."""
    return {k: (NONE_TEXT[family] if k == "none" else OPTION_TEXT[k]) for k in FAMILIES[family]}


def span_text(match: str) -> str:
    """The span as quoted inside a question stem."""
    return match.replace('"', "'").replace("\n", " ")


def ask(backend: Backend, prefix: str, stem: str, options: Mapping[str, str],
        orders: Iterable[Sequence[int]], min_mass: float = 0.5) -> Answer:
    keys = list(options)
    per_order, masses = [], []
    for order in orders:
        shown = [keys[i] for i in order]
        dist = backend.first_token(prefix, question(stem, options, shown))
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


def decide(conf: float, cat_p: float, th: Thresholds, reasons: list[str]) -> str:
    """``identifier``, ``not_identifier`` or ``review`` from the *calibrated* probability (the
    thresholds live in that space). Any prior ``reasons`` force review; review reasons found here
    are appended to ``reasons``."""
    if conf < th.drop_below and not reasons:
        return "not_identifier"
    if conf >= th.accept_at and cat_p >= th.category_at and not reasons:
        return "identifier"
    if th.drop_below <= conf < th.accept_at:
        reasons.append("uncertain")
    if cat_p < th.category_at:
        reasons.append("category_uncertain")
    return "review"


def context_window(text: str, start: int, end: int, width: int = 160) -> str:
    """The span marked ``[[...]]`` with up to ``width`` characters either side."""
    s, e = start - 1, end

    def clean(x: str) -> str:
        return x.replace("[[", "[ [").replace("]]", "] ]")

    left, right = text[max(0, s - width):s], text[e:e + width]
    lead = "..." if s - width > 0 else ""
    tail = "..." if e + width < len(text) else ""
    return f"{lead}{clean(left)}[[{clean(text[s:e])}]]{clean(right)}{tail}"


def prefix_for(text: str, cand: Candidate, width: int = 160, column: str | None = None) -> str:
    """The shared prompt prefix; a structured cell's ``column`` header comes first."""
    head = f"Column: {' '.join(column.split())[:60]}\n" if column else ""
    return f"{head}Text:\n{context_window(text, cand.start, cand.end, width)}\n\n"


# A structured-cell column whose values are dates; a whole-cell date elsewhere is misplaced.
_DATE_COLUMN = re.compile(r"date|dob|birth|death|died|_dt$|^dt_|time", re.IGNORECASE)
# A word that introduces a date, right before the span ("Admitted on", "DOB:", "dated").
_DATE_LEAD = re.compile(r"(?:\b(?:on|dated?|since|until|till|DOB|born|birth|death|died)|"
                        r"\bD\.O\.B\.?)\s*:?\s*$", re.IGNORECASE)


def family_in_context(text: str, cand: Candidate, column: str | None = None) -> str:
    """:func:`family_of`, refined by context code can check. An 8-digit compact date after a date
    word, or as a whole cell of a date column, is asked as a date: offered the ID options as
    well, the model called admission dates case numbers (P4)."""
    m = text[cand.start - 1:cand.end].strip()
    fam = family_of(m)
    if fam == "numeric" and re.fullmatch(r"\d{8}", m):
        whole_cell = column is not None and m == text.strip()
        if (whole_cell and _DATE_COLUMN.search(column)) or _DATE_LEAD.search(
                text[max(0, cand.start - 1 - 24):cand.start - 1]):
            return "date"
    return fam


def cell_review(text: str, cand: Candidate, family: str, column: str | None,
                column_outlier: bool = False) -> str | None:
    """A review reason for a whole structured cell the model may not drop, else None.

    A bare cell has no context but its header, and the model trusts the header: P4 saw case
    numbers in a ``ward`` column answered ``none`` with certainty. So a whole-cell value that is
    date-shaped (outside a date column) or ID-shaped (5+ digits), or that the caller found to be
    an outlier in its column (``slmjev.column.date_outliers``: a DOB typed into a procedure-date
    column), is never dropped: where the judge would drop it, it goes to review instead."""
    m = text[cand.start - 1:cand.end].strip()
    if not column or m != text.strip():
        return None
    if column_outlier:
        return "column_outlier"
    if family == "date" or rules.parse_compact_date(m) is not None:
        return None if _DATE_COLUMN.search(column) else "misplaced_date"
    return "cell_id_shape" if sum(c.isdigit() for c in m) >= 5 else None


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
    ask_property: bool = False  # ask the model when address rules cannot tell
    fast_path: bool = True  # rule-certain spans skip the model (see rule_certain)
    trace: bool = False

    @classmethod
    def calibrated(cls, backend: Backend, path: str | os.PathLike, *,
                   model: str | os.PathLike | None = None, **kw) -> Judge:
        """A judge with the calibrator and thresholds fitted in ``path`` (``calibrate.save``).

        A calibration only holds for the model and prompts it was fitted on, so it is refused
        when its ``meta`` names another :data:`PROMPT_VERSION` or, when ``model`` is given,
        another model file."""
        cal, th, meta = calibrate.load(path)
        if meta.get("prompt") != PROMPT_VERSION:
            raise ValueError(f"{path}: fitted for prompt version {meta.get('prompt')!r}, "
                             f"this judge is {PROMPT_VERSION}; refit it")
        if model is not None and os.path.basename(str(meta.get("model") or "")) != \
                os.path.basename(str(model)):
            raise ValueError(f"{path}: fitted for model {meta.get('model')!r}, not {model!r}")
        base = kw.pop("thresholds", Thresholds())
        return cls(backend, calibrator=cal, thresholds=replace(
            base, drop_below=th["drop_below"], accept_at=th["accept_at"]), **kw)

    def judge(self, text: str, cand: Candidate, column: str | None = None,
              column_outlier: bool = False) -> dict:
        """One span record. ``column`` is the header when ``text`` is a structured cell;
        ``column_outlier`` says the cell's value stands out from the rest of its column."""
        match = text[cand.start - 1:cand.end]
        family = cand.family or family_in_context(text, cand, column)
        rec: dict = {"start": cand.start, "end": cand.end, "match": match, "type": cand.type,
                     "identifier": None, "detector": DETECTOR, "confidence": None,
                     "p_identifier": None, "category": None, "category_probs": {},
                     "sensitivity": None, "needs_review": True, "decision": "review",
                     "context": {}, "judge": {"family": family, "errors": []}}
        info = rec["judge"]
        if self.fast_path and (sure := rule_certain(text, cand)):
            ident, reason = sure
            rec.update(identifier=ident, category=ident, category_probs={ident: 1.0},
                       type=cand.type or ident, p_identifier=RULE_CONFIDENCE,
                       confidence=RULE_CONFIDENCE, decision="identifier", needs_review=False)
            if ident == "postal_code":
                rec["context"] = address_context(text, cand, ident)
            info.update(fast_path=reason, reasons=[])
            return rec
        prefix = prefix_for(text, cand, self.width, column)
        span = span_text(match)
        th = self.thresholds
        try:
            opts = choice_options(family)
            choice = ask(self.backend, prefix, CHOICE_STEM.format(span=span), opts,
                         rotations(len(opts), self.choice_rotations), th.min_mass)
        except JudgeError as e:
            info["errors"].append(f"choice: {e}")
            return rec
        cat, cat_p = choice.top
        p_id = 1.0 - choice.probs["none"]
        rec.update(category=cat, category_probs=_round(choice.probs), p_identifier=round(p_id, 6),
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
        p_addr = choice.probs.get("address", 0) + choice.probs.get("postal_code", 0)
        if p_addr >= 0.2:
            addr_cat = "postal_code" if choice.probs.get("postal_code", 0) > choice.probs.get(
                "address", 0) else "address"
            ctx.update(address_context(text, cand, addr_cat))
            if self.ask_property and ctx["property_kind"] == "unknown":
                try:
                    pk = ask(self.backend, prefix, PROPERTY_STEM, PROPERTY_OPTIONS,
                             rotations(len(PROPERTY_OPTIONS)), th.min_mass)
                    kind, kind_p = pk.top
                    if kind_p >= th.category_at and kind != "unknown":
                        model_type = PROPERTY_TYPE[kind]
                        # rules outrank the model: keep a rule-set type unless the model agrees
                        if ctx["property_type"] in ("unknown", model_type):
                            ctx.update(property_kind=kind, property_type=model_type)
                except JudgeError as e:
                    info["errors"].append(f"property: {e}")

        decision = decide(conf, cat_p, th, reasons)
        # a context-free cell may be accepted, never dropped
        if decision == "not_identifier" and (cell_reason := cell_review(
                text, cand, family, column, column_outlier)):
            decision = "review"
            reasons.append(cell_reason)
        rec["decision"] = decision
        rec["needs_review"] = decision == "review"
        info["reasons"] = reasons
        return rec

    def judge_many(self, text: str, cands: Iterable[Candidate], column: str | None = None,
                   column_outlier: bool = False) -> list[dict]:
        return [self.judge(text, c, column, column_outlier) for c in cands]


def _round(d: Mapping[str, float], nd: int = 4) -> dict[str, float]:
    return {k: round(v, nd) for k, v in d.items()}
