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
(``rule_certain``). A structured cell's column header joins the prefix (for a table pasted into
text too, ``table_cell``), and a whole cell that is ID-shaped, date-shaped outside a date column,
or a date far from the rest of its column (``slmjev.column``) is never dropped (``cell_review``).
A compact date after a date word or in a date column is asked as a date (``family_in_context``).
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

import http.client
import json
import math
import os
import re
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from slmjev import calibrate, rules
from slmjev.netguard import check_loopback_url, loopback_request

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
    re.compile(r"^\d{4}[ \t]?年[ \t]?\d{1,2}[ \t]?月[ \t]?\d{1,2}[ \t]?日$"),
]


def family_of(match: str) -> str:
    """The option family for a span, from its surface shape only (never from a gold label)."""
    s = match.strip()
    if any(p.match(s) for p in _DATE_SHAPES) or ocr_date(s):
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
# A URL without a scheme but with a path (``social.example.com/hafiz.jamal.1993``): a profile or a
# document, not just an organisation's domain (notes_v2).
_URL_NO_SCHEME = re.compile(r"(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                            r"\.(?:com|net|org|sg|io|me|co|info|biz|edu|gov|my|ph|in|cn)"
                            r"(?:\.[a-z]{2})?/[\w.%?=&#~+/-]*[\w/]")
# A handle after a messaging or social-media keyword (``WeChat ID linzq_1992sg``).
# "Line" and "Signal" are common words, so they count only with "ID" ("line immunoassay").
SOCIAL_LEAD = (r"(?i:\b(?:(?:wechat|whatsapp|telegram|instagram|ig|facebook|fb|tiktok|twitter|"
               r"skype|snapchat|linkedin)\b(?:\s*(?:id|handle|username|user|account))?"
               r"|(?:line|signal)\s*(?:id|handle|username)\b)\s*[:@]?\s*)")
_SOCIAL_LEAD_END = re.compile(SOCIAL_LEAD + "$")
HANDLE = r"@?[A-Za-z][A-Za-z0-9._]{1,30}[A-Za-z0-9_]"
# A pathology or radiology accession number: a 1-4 letter prefix with a 2-digit year, a hyphen
# and a 5-7 digit serial (``HS26-018455``, ``S24-0012345``). The model dropped these (notes_v2).
_ACCESSION = re.compile(r"[A-Z]{1,4}\d{2}-\d{5,7}")
# A masked NRIC/FIN (``G****262U``, ``SXXXX567D``): the prefix, 3-7 masked digits, the rest and the
# check letter, 9 characters in all. The unmasked part still narrows who it is (notes_v3).
MASKED_NRIC = r"[STFGM][*xX#]{3,7}\d{0,4}[A-Z]"
# A picture or scan file name (``IMG_20260923_1542.jpg``): it points to a photograph. Every one on
# the benchmark sets was a patient's photo; the model called ``IMG_4410.JPG`` ``none`` (notes_v9).
IMAGE_FILE = r"[\w-]+(?:\.[\w-]+)*\.(?i:jpe?g|png|gif|bmp|tiff?|heic|webp|dcm|dicom|mp4|mov|avi)"
# An ISBT 128 blood donation number: facility letter and 4 digits, year, 6-digit serial, maybe
# written in groups with a check character (``W0417 26 318857 K``). The model accepted these on
# notes_v4-v8 and called them ``none`` on notes_v9.
DONATION_NO = r"[A-Z]\d{4} ?\d{2} ?\d{6}(?: [A-Z0-9](?![\w-]))?"
# The tail of an NRIC/FIN given on its own (``NRIC ending 412D``, ``IC last 4: 567A``): the
# lead, then the tail as group 1. Shared with the proposer. The acronyms are case-sensitive;
# the gap before "ending" holds no digits or clause breaks (a full NRIC, "; bed").
NRIC_TAIL = (r"\b(?:NRIC|IC|I/C|I C|FIN)\b[^\n\d;,]{0,24}?\b(?i:ending(?:[ \t]+(?:in|with))?|"
             r"ends?[ \t]+(?:in|with)|last[ \t]+(?:4|four|3|three)(?:[ \t]+(?:digits?|"
             r"char(?:acter)?s?))?)(?i:[ \t]+of)?[ \t]*[:\-]?[ \t]*([A-Z]?\d{3,4}[A-Z])(?!\w)")
_NRIC_TAIL_RX = re.compile(NRIC_TAIL)
# A number spelled out digit by digit, as dictation software writes it (``nine one seven seven
# zero four two six``, notes_v10): 7 or more digit words, ``double``/``triple`` allowed.
_DIGIT_WORD = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DW = r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine)"
SPOKEN_DIGITS = (rf"(?i:(?:plus[ \t]+)?(?:(?:double|triple)[ \t]+)?{_DW}"
                 rf"(?:[ \t,-]+(?:(?:double|triple)[ \t]+)?{_DW}){{4,}})")
# A reference dictated with a slash or a dash between its digit groups (``two six slash four
# four one two nine zero``, notes_v12)
SPOKEN_REF = (rf"(?i:{_DW}(?:[ \t]+{_DW})*[ \t]+(?:slash|dash|stroke|hyphen)[ \t]+{_DW}"
              rf"(?:[ \t]+{_DW})*)")
# An NRIC/FIN read out with its letters: ``S six seven three two nine six nine D`` (notes_v14)
SPOKEN_NRIC = rf"[STFGM][ \t]+(?i:{_DW}(?:[ \t,-]+{_DW}){{6}})[ \t]+[A-Z]"
# A date right after a word saying someone died (``d. 1998``, ``died Mar 2011``, ``DOD:``); a
# year alone or a month and year too, as a pedigree writes them. The judge called both of those
# ``none`` (notes_v14).
_DEATH_LEAD = re.compile(r"(?:\bd\.|\b(?i:died|deceased|passed[ \t]+away|date[ \t]+of[ \t]+death)"
                         r"|\bDOD|†)[ \t]*(?:(?i:in|on)[ \t]+)?[:\-]?[ \t]*$")
_MON_NAME = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]{0,6}\.?"
DEATH_DATE = (rf"(?:(?:\d{{1,2}}[ \t]+)?{_MON_NAME}[ \t]+(?:19|20)\d{{2}}|(?:19|20)\d{{2}}"
              r"|\d{1,2}[/.-]\d{1,2}[/.-](?:\d{2}|\d{4})|(?:19|20)\d{2}-\d{2}-\d{2})")


# HL7 v2 fields that hold a person (notes_v11: surnames before ``^`` were never proposed). XPN:
# family^given^middle^...; XCN: id^family^given^middle^... Keyed by (segment, field number).
_HL7_XPN = {("PID", 5), ("PID", 6), ("PID", 9), ("NK1", 2), ("NK1", 30), ("GT1", 3),
            ("IN1", 16), ("PRD", 2)}
_HL7_XCN = {("PV1", 7), ("PV1", 8), ("PV1", 9), ("PV1", 17), ("PV1", 52), ("ORC", 10),
            ("ORC", 11), ("ORC", 12), ("OBR", 16), ("OBR", 28), ("EVN", 5), ("PD1", 4),
            ("TXA", 5), ("TXA", 9), ("TXA", 10), ("TXA", 11), ("TXA", 22), ("ROL", 4)}
_HL7_SEGMENT = re.compile(r"(?m)^([A-Z][A-Z0-9]{2})\|")
_HL7_NAME = re.compile(r"[A-Za-z][A-Za-z '’.-]*[A-Za-z.]|[A-Za-z]")
_HL7_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9./-]*")


def hl7_people(text: str) -> Iterable[tuple[int, int, str]]:
    """``(start, end, identifier)`` (1-based, inclusive) for each person component of an HL7 v2
    segment pasted into text: the family, given and middle names of a name field (``name``), and
    the ID before them in a clinician field (``other_id``, when it has a digit)."""
    for seg in _HL7_SEGMENT.finditer(text):
        name, s0 = seg.group(1), seg.start()
        e0 = text.find("\n", s0)
        line = text[s0:len(text) if e0 < 0 else e0].rstrip("\r")
        if name == "MSH":
            continue
        pos = 0
        for num, fld in enumerate(line.split("|")):
            kind = "xpn" if (name, num) in _HL7_XPN else "xcn" if (name, num) in _HL7_XCN else None
            if kind:
                rpos = pos
                for rep in fld.split("~"):
                    cpos = rpos
                    for k, comp in enumerate(rep.split("^")):
                        s, e = s0 + cpos, s0 + cpos + len(comp)
                        if kind == "xcn" and k == 0:
                            if _HL7_ID.fullmatch(comp) and re.search(r"\d", comp):
                                yield s + 1, e, "other_id"
                        elif (k < 3 if kind == "xpn" else 1 <= k <= 3) and \
                                _HL7_NAME.fullmatch(comp):
                            yield s + 1, e, "name"
                        cpos += len(comp) + 1
                    rpos += len(rep) + 1
            pos += len(fld) + 1


def hl7_component(text: str, start: int, end: int) -> str | None:
    """The identifier of the HL7 person component the span is exactly, else None."""
    if "|" not in text:
        return None
    return next((ident for s, e, ident in hl7_people(text) if (s, e) == (start, end)), None)


# OCR look-alikes in a date (``l4.O2.l95l``, notes_v11): l/I for 1 and O/o for 0
_OCR = str.maketrans("lIOo", "1100")
OCR_DATE = r"[0-9lIOo]{1,2}[./-][0-9lIOo]{1,2}[./-](?:[0-9lIOo]{4}|[0-9lIOo]{2})"


def ocr_date(s: str) -> str | None:
    """``s`` with look-alike letters read as digits, when it is a date with at least one
    look-alike and three real digits; else None."""
    if not re.fullmatch(OCR_DATE, s) or not re.search(r"[lIOo]", s) or \
            sum(c.isdigit() for c in s) < 3:
        return None
    d = s.translate(_OCR)
    parts = [int(x) for x in re.split(r"[./-]", d)]
    return d if 1 <= parts[0] <= 31 and 1 <= parts[1] <= 12 else None


def spoken_digits(s: str) -> str:
    """The digits a spelled-out number stands for (``plus six five ...`` -> ``+65...``)."""
    out, times = [], 1
    for w in re.findall(r"[a-z]+", s.lower()):
        if w == "plus":
            out.append("+")
        elif w in ("double", "triple"):
            times = 2 if w == "double" else 3
        elif w in _DIGIT_WORD:
            out.append(_DIGIT_WORD[w] * times)
            times = 1
    return "".join(out)
_POSTAL_LEAD = re.compile(r"(?:\bSingapore\s*|\bS\(?)$")
# The same lead merged into the candidate: the proposer joins ``S276963`` or
# ``Singapore 482263`` into one span, which the model dropped or called an address (0007).
_POSTAL_WITH_LEAD = re.compile(r"(?:Singapore\s*|S\(?)\d{6}\)?")
# The end of a street address: a block, a #floor-unit, or a street-type word, then only
# address-like tokens (numbers, units, Title-case building words) before the postal code. A
# lower-case or all-caps word ("Block 4 clinic, platelets 245000", "PLT") breaks the address.
_STREET_TYPES = (r"Blk|Block|Avenue|Ave|Road|Rd|Street|St|Drive|Crescent|Cres|Lorong|Lor|Jalan|Jln|"
                 r"Lane|Walk|Close|Place|Terrace|Rise|Link|Way|Grove|View|Heights|Green|Gardens?|"
                 r"Circle|Loop|Boulevard|Quay|Vale|Hill|Park|Plain")
_ADDRESS_TOKEN = r"(?:\d{1,4}[A-Z]?|#\s?\d{1,3}-\d{1,5}|[A-Z][a-z][A-Za-z'&-]*)"
_ADDRESS_END = re.compile(rf"(?:\b(?i:{_STREET_TYPES})\b\.?|#\s?\d{{1,3}}-\d{{1,5}})"
                          rf"(?:[ ,]+{_ADDRESS_TOKEN}){{0,6}}[ ,]+$")
_SG_SECTORS = {f"{i:02d}" for i in range(1, 83)}
# An ID keyword right before the span. Acronyms are case-sensitive ("IC", not "ic").
_ID_TAIL = r"\.?\s*(?:[Nn]o\.?|[Nn]umber|#)?\s*[:(]?\s*$"
_ID_LEADS = [
    (re.compile(r"(?:\bNRIC|\bFIN|\b[Tt]emp(?:orary)? IC|\bIC|\b[Pp]assport|"
                rf"\b[Bb]irth [Cc]ert(?:ificate)?){_ID_TAIL}"), "national_id"),
    (re.compile(rf"\bMRN{_ID_TAIL}"), "mrn"),
    (re.compile(rf"\bMCR{_ID_TAIL}"), "other_id"),
]
_ID_SHAPE = re.compile(r"[A-Z]{0,2}\d{5,}[A-Z]?")
# A record reference after a reference keyword ("Lab No", "lab ref", "Accession", "Specimen",
# "Reg. No.", "claim ref", "policy no."): any letters-digits-hyphens-slashes token with 4+
# digits. The model dropped these as "none" (P7, notes_v2). "case no." is a case_visit, which
# the model gets right; a bare "ref" or "policy" is too loose ("ref range", "IPC policy
# IC-04-017"); both stay with the model.
_REF_LEAD = re.compile(r"(?i:\blab(?:oratory)?\s*(?:no|number|ref(?:erence)?|id)\b|\baccession"
                       r"|\bspecimen|\bsample\s*(?:no|number|id)\b"
                       r"|\b(?:order|request|report|record)\s*(?:no|number|id)\b"
                       r"|\breg(?:istration)?\.?\s*(?:no|number)\b"
                       r"|\b(?:claim|policy)\s*(?:ref(?:erence)?|no|number|id|#))"
                       + _ID_TAIL)
_REF_SHAPE = re.compile(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*")
# A bare "sample" before a code (``Group & screen sample GS-26-091837``). It also comes before
# dates and counts, so the code must hold a letter as well as 4+ digits.
_SAMPLE_LEAD = re.compile(rf"\b[Ss]ample{_ID_TAIL}")
# A bank or billing account number after an account keyword: 8+ digits, optionally grouped by
# hyphens or spaces (``250-03851-0``). The model called these ``none`` with certainty (0005).
_ACCOUNT_LEAD = re.compile(rf"(?:\b[Aa]ccount|\b[Aa]/[Cc]|\b[Aa]cct){_ID_TAIL}")
_ACCOUNT_SHAPE = re.compile(r"\d+(?:[- ]\d+){0,4}")


def rule_certain(text: str, cand: Candidate) -> tuple[str, str] | None:
    """``(identifier, reason)`` when code alone is sure the span identifies someone, else None.

    Only shapes a rule can confirm qualify: a valid NRIC/FIN checksum or a masked NRIC/FIN
    (``G****262U``), also spoken with its letters (``S one two ... seven D``); a date or year right
    after a death word (``d. 1998``, ``died on 3 Mar 2020``); a lower-case login in a fixed-width
    table's ``USER``/``BY`` column; a blood donation number (``W0417 26 318857 K``); a picture file
    name (``IMG_4410.JPG``); a Singapore phone number spelled out in digit words; a whole email or
    URL (with a scheme, or without one but with a path); an accession-number shape
    (``HS26-018455``); a handle with a digit, ``_`` or ``.`` after a messaging keyword
    (``WeChat ID``); a 6-digit postal code right after ``Singapore``/``S(`` (or with that lead
    inside the span), or ending a street address with a valid sector; an ID-shaped token right
    after an ID keyword (``NRIC``, ``temp IC``, ``MRN``, ...), a person component of an HL7 name
    or clinician field (``TAN`` in ``PID|...|TAN^MEI LING``), a record reference right after
    ``Lab No`` / ``Reg. No.`` / ``Accession`` / ``claim ref`` / ``policy`` (or a code with a
    letter after a bare ``sample``), an account number right after ``account`` / ``a/c``, or an
    NRIC/FIN tail after ``NRIC ending`` / ``IC last 4``.
    They skip the model; everything else is judged. Accepting is the fail-closed direction, since
    it only ever removes more."""
    m = text[cand.start - 1:cand.end].strip()
    left = text[max(0, cand.start - 1 - 80):cand.start - 1]
    if hl7 := hl7_component(text, cand.start, cand.end):
        return hl7, "hl7_person"
    if rules.nric_valid(m):
        return "national_id", "nric_checksum"
    if re.fullmatch(SPOKEN_NRIC, m):
        spoken = m[0] + spoken_digits(m[1:-1]) + m[-1]
        if len(spoken) == 9 and rules.nric_valid(spoken):
            return "national_id", "spoken_nric"
    if re.fullmatch(DEATH_DATE, m) and _DEATH_LEAD.search(left[-32:]):
        return "date_of_death", "death_keyword"
    if re.fullmatch(LOGIN, m) and _LOGIN_COLUMN.search(fixed_width_column(text, cand.start) or ""):
        return "other_id", "login_column"
    if len(m) == 9 and re.fullmatch(MASKED_NRIC, m):
        return "national_id", "masked_nric"
    if _EMAIL.fullmatch(m):
        return "email", "email_shape"
    if _URL.fullmatch(m) or _URL_NO_SCHEME.fullmatch(m):
        return "other_id", "url_shape"
    if _ACCESSION.fullmatch(m):
        return "other_id", "accession_shape"
    if (re.fullmatch(HANDLE, m) and re.search(r"[\d_.]", m)
            and _SOCIAL_LEAD_END.search(left[-32:])):
        return "other_id", "handle_keyword"
    if re.fullmatch(r"\d{6}", m):
        if _POSTAL_LEAD.search(left[-12:]):
            return "postal_code", "postal_keyword"
        if m[:2] in _SG_SECTORS and _ADDRESS_END.search(left):
            return "postal_code", "postal_after_address"
    if _POSTAL_WITH_LEAD.fullmatch(m):
        return "postal_code", "postal_keyword"
    if _ID_SHAPE.fullmatch(m):
        for lead, ident in _ID_LEADS:
            if lead.search(left[-24:]):
                return ident, "id_keyword"
    if (_REF_SHAPE.fullmatch(m) and sum(c.isdigit() for c in m) >= 4
            and _REF_LEAD.search(left[-24:])):
        return "other_id", "ref_keyword"
    if (_REF_SHAPE.fullmatch(m) and sum(c.isdigit() for c in m) >= 4
            and re.search(r"[A-Za-z]", m) and _SAMPLE_LEAD.search(left[-24:])):
        return "other_id", "ref_keyword"
    tail = _NRIC_TAIL_RX.search(text, max(0, cand.start - 1 - 48), cand.end + 1)
    if tail and (tail.start(1), tail.end(1)) == (cand.start - 1, cand.end):
        return "national_id", "nric_tail"
    if (_ACCOUNT_SHAPE.fullmatch(m) and sum(c.isdigit() for c in m) >= 8
            and _ACCOUNT_LEAD.search(left[-24:])):
        return "other_id", "account_keyword"
    if re.fullmatch(DONATION_NO, m):
        return "other_id", "donation_shape"
    if re.fullmatch(IMAGE_FILE, m):
        return "photo", "image_file"
    if re.fullmatch(SPOKEN_DIGITS, m) and re.fullmatch(r"(?:\+65)?[3689]\d{7}", spoken_digits(m)):
        return "phone", "spoken_phone"
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
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        t0 = time.perf_counter()
        try:
            status, data = loopback_request(self.url + "/v1/chat/completions", method="POST",
                                            body=json.dumps(body).encode("utf-8"),
                                            headers=headers, timeout=self.timeout)
            if status != 200:
                raise JudgeError(f"backend call failed: HTTP {status}")
            resp = json.loads(data.decode("utf-8"))
        except (http.client.HTTPException, TimeoutError, json.JSONDecodeError, OSError) as e:
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
    # what drop_below / accept_at compare: "calibrated" (the confidence) or "raw" (p_identifier).
    # "raw" keeps decisions on the thresholds they were validated with while the reported
    # confidence is recalibrated (docs/decisions/0010)
    space: str = "calibrated"

    def __post_init__(self):
        if self.space not in ("calibrated", "raw"):
            raise ValueError(f"threshold space must be 'calibrated' or 'raw', not {self.space!r}")


def decide(conf: float, cat_p: float, th: Thresholds, reasons: list[str]) -> str:
    """``identifier``, ``not_identifier`` or ``review`` from ``conf``: the calibrated probability,
    or the raw one when ``th.space`` is ``"raw"``, whichever the thresholds were chosen in. Any
    prior ``reasons`` force review; review reasons found here are appended to ``reasons``."""
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


_TABLE_DELIMS = ("\t", "|", ",", ";")
# A header cell: a short label, not a sentence (no colon or full stop), no long numbers.
_HEADER_CELL = re.compile(r"[A-Za-z][\w /#()&'-]{0,29}")


def _cells(line: str, delim: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` offsets of the cells of a delimited line; double quotes protect a
    delimiter, except in a pipe table."""
    out, s, quoted = [], 0, False
    for i, ch in enumerate(line):
        if ch == '"' and delim != "|":
            quoted = not quoted
        elif ch == delim and not quoted:
            out.append((s, i))
            s = i + 1
    out.append((s, len(line)))
    return out


def _header_cells(line: str, cells: list[tuple[int, int]]) -> list[str] | None:
    vals = [line[a:b].strip().strip('"').strip() for a, b in cells]
    filled = [v for v in vals if v]
    if len(filled) >= 3 and all(_HEADER_CELL.fullmatch(v) and not re.search(r"\d{3}", v)
                                for v in filled):
        return vals
    return None


def table_column(text: str, start: int, end: int, max_rows: int = 60) -> str | None:
    """The header of the table column a span sits in, for a table pasted into free text (CSV,
    tab- or pipe-separated): the span is inside one cell of a row with 3+ cells, and a header row
    with the same number of cells is above it, through rows of that shape. None otherwise, and
    for a span on the header row itself. In a pasted CSV export the judge accepted an MRN in row 1,
    with the header in its window, and rejected the same column further down (notes_v10)."""
    return table_cell(text, start, end, max_rows)[0]


def table_cell(text: str, start: int, end: int, max_rows: int = 60) -> tuple[str | None, bool]:
    """``(header, whole)``: :func:`table_column`'s header, and whether the span is the whole
    (unquoted, stripped) value of its cell."""
    s0 = text.rfind("\n", 0, start - 1) + 1
    e0 = text.find("\n", end)
    line = text[s0:len(text) if e0 < 0 else e0]
    a, b = start - 1 - s0, end - s0
    for delim in _TABLE_DELIMS:
        cells = _cells(line, delim)
        if len(cells) < 3:
            continue
        idx = next((k for k, (x, y) in enumerate(cells) if x <= a and b <= y), None)
        if idx is None or _header_cells(line, cells):
            return None, False
        x, y = cells[idx]
        whole = line[x:y].strip().strip('"').strip() == line[a:b].strip()
        pos = s0
        for _ in range(max_rows):
            if pos == 0:
                break
            p0 = text.rfind("\n", 0, pos - 1) + 1
            prev = text[p0:pos - 1]
            pc = _cells(prev, delim)
            if len(pc) != len(cells):
                break
            head = _header_cells(prev, pc)
            if head:
                return head[idx] or None, whole and bool(head[idx])
            pos = p0
        return None, False
    return None, False


# A fixed-width table, its columns lined up with spaces (an EMR audit trail, an order-entry log;
# notes_v14): a header line of 3+ upper-case labels two or more spaces apart, and rows below it
# whose cells start under a label.
_FIXED_CELL = re.compile(r"\S+(?: \S+)*")
_FIXED_HEAD = re.compile(r"[A-Z][A-Z0-9/#()&'.-]*(?: [A-Z][A-Z0-9/#()&'.-]*)*")
_RULE_LINE = re.compile(r"[ \t]*[-=_*]{3,}[ \t]*")


def fixed_width_column(text: str, start: int, max_rows: int = 40) -> str | None:
    """The label over the column a span starting at ``start`` sits in, for a fixed-width table
    pasted into the text; None if there is none above it (a blank line ends the search)."""
    s0 = text.rfind("\n", 0, start - 1) + 1
    col, pos = start - 1 - s0, s0
    for _ in range(max_rows):
        if pos == 0:
            return None
        p0 = text.rfind("\n", 0, pos - 1) + 1
        prev = text[p0:pos - 1]
        if not prev.strip():
            return None
        cells = [(m.start(), m.group()) for m in _FIXED_CELL.finditer(prev)]
        if (len(cells) >= 3 and not _RULE_LINE.fullmatch(prev)
                and all(_FIXED_HEAD.fullmatch(c) for _, c in cells)):
            under = [c for a, c in cells if a <= col]
            return under[-1] if under else None
        pos = p0
    return None


# A log-in name (``x_lowsm``, ``lowjm``) under a user or ``BY`` column of such a table: the judge
# called one an HIV test and never saw the other (notes_v14)
LOGIN = r"[a-z][a-z0-9_.]{2,19}[a-z0-9]"
_LOGIN_COLUMN = re.compile(r"\b(?:USER|USER-?ID|USERNAME|LOGIN|BY)\b")


def prefix_for(text: str, cand: Candidate, width: int = 160, column: str | None = None) -> str:
    """The shared prompt prefix; a structured cell's ``column`` header comes first."""
    head = f"Column: {' '.join(column.split())[:60]}\n" if column else ""
    return f"{head}Text:\n{context_window(text, cand.start, cand.end, width)}\n\n"


# A structured-cell column whose values are dates; a whole-cell date elsewhere is misplaced.
_DATE_COLUMN = re.compile(r"date|dob|birth|death|died|_dt$|^dt_|time", re.IGNORECASE)
# A column of birth or death dates: a date cell in it is that identifier (notes_v11: the judge
# answered ``none``, p 1e-6, for a DOB cell with ``Column: DOB`` in its prefix).
_DOB_COLUMN = re.compile(r"(?:^|[^a-z])(?:dob|d\.o\.b|birth|born)(?:[^a-z]|$)", re.IGNORECASE)
_DOD_COLUMN = re.compile(r"(?:^|[^a-z])(?:dod|death|died|deceased)(?:[^a-z]|$)", re.IGNORECASE)


def date_column_identifier(column: str) -> str | None:
    """``dob`` or ``date_of_death`` for a column of birth or death dates, else None."""
    col = re.sub(r"([a-z])([A-Z])", r"\1 \2", column)  # DateOfBirth
    if _DOD_COLUMN.search(col):
        return "date_of_death"
    return "dob" if _DOB_COLUMN.search(col) else None
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
                column_outlier: bool = False, whole: bool | None = None) -> str | None:
    """A review reason for a whole structured cell the model may not drop, else None.

    A bare cell has no context but its header, and the model trusts the header: P4 saw case
    numbers in a ``ward`` column answered ``none`` with certainty. So a whole-cell value that is
    date-shaped (outside a date column) or ID-shaped (5+ digits), or that the caller found to be
    an outlier in its column (``slmjev.column.date_outliers``: a DOB typed into a procedure-date
    column), is never dropped: where the judge would drop it, it goes to review instead.
    ``whole`` says the span is a whole cell of a table pasted into text (``table_cell``); by
    default a span is whole when it is all of ``text``. With its header in the prefix, the judge
    still dropped MRNs and mobile numbers in rows 2-4 of a pasted CSV export (notes_v10)."""
    m = text[cand.start - 1:cand.end].strip()
    if whole is None:
        whole = m == text.strip()
    if not column or not whole:
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
            base, drop_below=th["drop_below"], accept_at=th["accept_at"],
            space=th.get("space", "calibrated")), **kw)

    def _calibrate(self, p_id: float, category: str, cand: Candidate) -> float:
        """``p_id`` in calibrated space. A ``calibrate.Grouped`` calibrator also gets the call
        (``calibrate.group_of``), since how overconfident the model is depends on it."""
        if self.calibrator is None:
            return p_id
        if isinstance(self.calibrator, calibrate.Grouped):
            sources = (cand.detector or "").split("+")
            return self.calibrator(p_id, calibrate.group_of(category, _SHI, sources))
        return self.calibrator(p_id)

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
        # a table pasted into free text: the span's column header, and whether it is a whole cell
        tcol, twhole = (None, False) if column else table_cell(text, cand.start, cand.end)
        col, whole = (column, match.strip() == text.strip()) if column else (tcol, twhole)
        sure = rule_certain(text, cand) if self.fast_path else None
        if (self.fast_path and not sure and col and whole and family == "date"
                and (ident := date_column_identifier(col))):
            sure = ident, "date_column"
        if sure:
            ident, reason = sure
            rec.update(identifier=ident, category=ident, category_probs={ident: 1.0},
                       type=cand.type or ident, p_identifier=RULE_CONFIDENCE,
                       confidence=RULE_CONFIDENCE, decision="identifier", needs_review=False)
            if ident == "postal_code":
                rec["context"] = address_context(text, cand, ident)
            info.update(fast_path=reason, reasons=[])
            return rec
        # a span in a table pasted into free text is asked with its column header, as a cell is
        prefix = prefix_for(text, cand, self.width, col)
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
        conf = self._calibrate(p_id, cat, cand)
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

        decision = decide(p_id if th.space == "raw" else conf, cat_p, th, reasons)
        # a context-free cell may be accepted, never dropped
        if decision == "not_identifier" and (cell_reason := (
                cell_review(text, cand, family, column, column_outlier) if column
                else cell_review(text, cand, family, tcol, whole=twhole))):
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
