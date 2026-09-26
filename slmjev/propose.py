"""Candidate spans for the judge (P6). Pure code, no model.

The judge can only pick spans that somebody proposed, so this layer aims at **recall**; the judge
removes what it over-proposes. Every proposer is a surface-shape rule, never a list of known
values from the synthetic generator:

- ``rules``: the ported SG detectors (``rules.scan_text``, with the bare 6-digit postal code on).
- ``date``: numeric and month-name date shapes, including forms the rules skip (``16.03.1942``,
  ``4 Oct 1962``, ``28-Oct-1947``).
- ``number``: phone shapes with ``+65`` / ``(65)``, ID-like tokens with 5+ digits
  (``SN-518862``, ``85-2466-43``, ``M07699H``) and vehicle plates (``FWL4331H``).
- ``name``: runs of 2-6 capitalised words (Title case, UPPER case or initials), bridged by the
  connectors of Singapore names (``bin``, ``binte``, ``d/o``, ``s/o``, ``a/l``, ...); a surname
  particle with one word (``de Souza``); a single word only after an honorific. Stop words
  (sentence starters, clinical words, acronyms) are trimmed off both ends of a run.
- ``address``: street addresses ending in a street-type word or starting with ``Jalan`` /
  ``Lorong``, with an optional block, building name and ``#floor-unit``.
- ``shi``: a lexicon of sensitive-health terms (general clinical vocabulary for the provisional
  SHI labels, not taken from any policy document), each extended over following head nouns
  (``infection``, ``status``, ``use``, ...).

Candidates from the caller (``extra``: e.g. Privacy Filter spans) are merged in. Each interval is
kept once, with every source that proposed it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from slmjev import rules
from slmjev.judge import _STREET_TYPES, Candidate

# --- dates ---------------------------------------------------------------------------------

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]{0,6}\.?"
_NUM_END = r"(?![\d/\-]|\.\d)"  # a sentence's full stop may follow; another digit group may not
_DATE_RX = [
    re.compile(rf"(?<![\d/.\-])\d{{1,2}}[/.\-]\d{{1,2}}[/.\-](?:19|20)?\d{{2}}{_NUM_END}"),
    re.compile(rf"(?<![\d/.\-])(?:19|20)\d{{2}}[/.\-]\d{{1,2}}[/.\-]\d{{1,2}}{_NUM_END}"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[ \-]{_MON}[ \-,]*(?:19|20)\d{{2}}\b", re.IGNORECASE),
    re.compile(rf"\b{_MON}[ \-]\d{{1,2}}(?:st|nd|rd|th)?,?[ \-](?:19|20)\d{{2}}\b", re.IGNORECASE),
    re.compile(r"(?<![\d])(?:19|20)\d{6}(?![\d])"),
]

# --- numbers and IDs -----------------------------------------------------------------------

_PHONE_RX = [
    re.compile(r"(?:\+65|\(\+?65\))[ -]?\d{4}[ -]?\d{4}(?!\d)"),
    re.compile(r"(?<![\d-])\d{4}-\d{4}(?![\d-])"),
    re.compile(r"(?<!\d)[3689]\d{3} \d{4}(?!\d)"),
]
# A token of letters, digits and inner hyphens with at least 5 digits in it (checked in code).
_IDLIKE = re.compile(r"(?<![\w-])[A-Za-z]{0,4}-?\d[\dA-Za-z-]*\d[A-Za-z]{0,2}(?![\w-])")
# A vehicle plate: 1-3 letters, 1-4 digits, a check letter (``FWL4331H``, ``SBA123A``).
_PLATE = re.compile(r"\b[A-Z]{1,3}\d{1,4}[A-Z]\b")

# --- names ---------------------------------------------------------------------------------

_HONORIFIC = r"(?:Mr|Mrs|Ms|Mdm|Madam|Miss|Dr|Prof|Mstr|Master)\.?"
# Title case (``Tan``, ``D'Cruz``, ``Ah-Kow``), upper case (``TAN``, ``D'CRUZ``) or initials
# (``G.C.``)
_WORD = (r"(?:[A-Z]'?[A-Z]?[a-z]+(?:['\-][A-Z]?[a-z]+)*|[A-Z]'?[A-Z]+(?:['\-][A-Z]+)*"
         r"|[A-Z]\.(?:[A-Z]\.)+)")
_PARTICLE = r"(?:van|von|de|da|dos|del|la|le)"
_CONNECTOR = rf"(?i:bin|binti|binte|bte|b\.|d/o|s/o|a/l|a/p|@|{_PARTICLE})"
_NAME_RUN = re.compile(rf"(?<![\w']){_WORD}(?:(?:\s+{_CONNECTOR})?\s+{_WORD}){{1,5}}(?![\w'])")
# ``de Souza``: a lower-case surname particle and one capitalised word
_PARTICLE_NAME = re.compile(rf"(?<![\w']){_PARTICLE}\s+{_WORD}(?![\w'])")
_AFTER_HONORIFIC = re.compile(rf"\b{_HONORIFIC}\s+({_WORD})(?![\w'])")

# Capitalised words that start sentences or name things other than people. A run is trimmed of
# these at both ends; a run made only of them is not proposed. Common surnames that are also
# clinical abbreviations (``Ho``, ``Mo``) and month names that are also given names (``May``,
# ``June``, ``April``, ``August``) are deliberately absent.
_STOP_WORDS = """
    a an the and or but of for to in on at by with from as is was were be been has had have
    he she his her him they their it its this that these those who which patient pt pts
    admitted admission discharged discharge seen reviewed referred referral transferred
    presented presents presenting complains noted known history hx impression diagnosis
    dx plan assessment review follow followup up tcu fu rx meds medication medications
    home ward bed clinic hospital centre center polyclinic department dept unit icu hd ed
    a&e emergency general medicine surgery surgical medical nursing nurse doctor dr team
    reg consultant registrar staff case note notes summary report letter memo form
    date dob nric fin ic mrn passport tel hp phone mobile fax email contact address
    postal blk block level road street avenue ave rd st drive singapore sg
    male female mr mrs ms mdm madam miss prof son daughter wife husband mother father
    brother sister spouse nok next kin caregiver friend
    called spoke spoken update updated given informed told denies denied
    passed away died death deceased cause remarks remark comment comments free text
    jan feb mar apr jun jul aug sep sept oct nov dec january february march
    september october november december mon tue wed thu fri sat sun
    monday tuesday wednesday thursday friday saturday sunday am pm hrs om bd tds qid
    on off no yes nil na n/a nkda allergy allergies bp hr rr spo2 temp gcs hb wbc plt
    hiv hbv hcv std sti ptsd ocd adhd ivf ecg ct mri xr cxr us usg ogd lft rft fbc
    covid tb uti copd dm htn hld ckd ihd af chf cva tia
    insurer insurance member policy account bill billing payment amount total balance
    serial device model make sn id no number ref reference visit appt appointment
    social work worker msw family meeting
    """
_STOP = {w.lower() for w in _STOP_WORDS.split()}


def _is_stop(word: str) -> bool:
    return word.lower().strip(".,") in _STOP


# --- addresses -----------------------------------------------------------------------------

_TITLE = r"[A-Z][A-Za-z'&\-]+"
_UNIT_RX = r"#\s?\d{1,3}-\d{1,5}[A-Z]?"
_BUILDING = rf"{_TITLE}(?: {_TITLE}){{0,3}}"
# a building name after the unit, never the city
_BUILDING_AFTER = rf"(?: (?!Singapore\b){_TITLE}(?: (?!Singapore\b){_TITLE}){{0,3}})?"
_ADDRESS_RX = [
    # [Building, ][Blk ]123[A] Word Word Street-type [12][,] [#01-23 [Building]]
    re.compile(rf"(?:{_BUILDING}, )?(?:(?:Blk|Block) )?\d{{1,4}}[A-Z]? "
               rf"(?:{_TITLE} ){{0,4}}(?:{_STREET_TYPES})\b\.?(?: \d{{1,3}}[A-Z]?)?"
               rf"(?:,? {_UNIT_RX}{_BUILDING_AFTER})?"),
    # [Building, ][Blk ][12 ]Jalan Word [Word][, #01-23]; Lorong 3 Geylang
    re.compile(rf"(?:{_BUILDING}, )?(?:(?:Blk|Block) )?(?:\d{{1,4}}[A-Z]? )?"
               rf"(?:Jalan|Jln|Lorong|Lor)(?: \d{{1,3}})?"
               rf"(?: {_TITLE}){{1,3}}(?:,? {_UNIT_RX}{_BUILDING_AFTER})?"),
    # [Blk ]123[A] Word Word[,] #01-23: street types are an open class, the unit anchors it
    re.compile(rf"(?:(?:Blk|Block) )?\d{{1,4}}[A-Z]?(?: {_TITLE}){{1,4}},? {_UNIT_RX}"
               rf"{_BUILDING_AFTER}"),
]

# --- sensitive health information ---------------------------------------------------------

# General clinical vocabulary, grouped by the provisional SHI labels. Matching is case-insensitive
# on word boundaries; each hit takes leading qualifiers and following head nouns with it.
_SHI_TERMS = {
    "hiv_sti": ["hiv", "aids", "human immunodeficiency virus", "syphilis", "gonorrhoea",
                "gonorrhea", "chlamydia", "genital herpes", "herpes simplex", "genital warts",
                "trichomonas", "trichomoniasis", "hepatitis b", "hepatitis c", "hpv",
                "sexually transmitted", "std", "sti", "prep", "antiretroviral"],
    "mental_health": ["depression", "depressive", "schizophrenia", "schizoaffective", "bipolar",
                      "psychosis", "psychotic", "anxiety", "panic disorder", "ptsd",
                      "post-traumatic stress", "ocd", "obsessive-compulsive", "suicide",
                      "suicidal", "self-harm", "self harm", "overdose", "anorexia nervosa",
                      "bulimia", "eating disorder", "personality disorder", "dementia", "adhd",
                      "autism", "psychiatric"],
    "substance_use": ["alcohol use", "alcohol dependence", "alcoholism", "alcohol abuse",
                      "cannabis", "marijuana", "heroin", "methamphetamine", "ice use", "cocaine",
                      "opioid", "opiate", "methadone", "buprenorphine", "ketamine",
                      "substance use", "substance abuse", "drug abuse", "drug use", "inhalant",
                      "benzodiazepine dependence", "iv drug"],
    "genetic": ["brca1", "brca2", "brca", "lynch syndrome", "huntington", "cystic fibrosis",
                "thalassaemia trait", "thalassemia trait", "genetic testing", "gene positive",
                "hypercholesterolaemia", "hypercholesterolemia", "carrier status",
                "mutation carrier", "down syndrome", "trisomy", "genetic"],
    "reproductive_sexual": ["termination of pregnancy", "abortion", "miscarriage", "ivf",
                            "in vitro fertilisation", "in vitro fertilization", "infertility",
                            "erectile dysfunction", "contraception", "gender-affirming",
                            "gender affirming", "gender dysphoria", "transgender",
                            "sexual orientation", "ectopic pregnancy"],
    "other_sensitive": ["sexual assault", "rape", "domestic violence", "family violence",
                        "elder abuse", "child abuse", "child protection", "abuse", "neglect",
                        "incarceration", "prison", "criminal record"],
}
_SHI_LEADS = (r"previous|past|known|recurrent|chronic|active|suspected|confirmed|"
              r"genetically\s+confirmed|generali[sz]ed|severe|major|familial")
_SHI_HEADS = (r"infection|status|disorder|disease|use|dependence|abuse|carrier|attempt|"
              r"syndrome|positive|negative|treatment|therapy|workup|work-up|concerns?|"
              r"referral|maintenance|mutation|trait|history|ideation|episode|gene|hormones?|"
              r"hormonal|medication|clinic|counsell?ing")
_SHI_RX = [
    (label, re.compile(
        rf"(?<![\w-])(?:(?:{_SHI_LEADS})\s+)*"
        rf"(?:{'|'.join(re.escape(t) for t in sorted(terms, key=len, reverse=True))})"
        rf"(?:[\s-]+(?:{_SHI_HEADS}))*(?![\w-])", re.IGNORECASE))
    for label, terms in _SHI_TERMS.items()
]


@dataclass
class Proposal:
    """A candidate interval (1-based ``start``, inclusive ``end``) and who proposed it."""

    start: int
    end: int
    sources: list[str] = field(default_factory=list)
    type: str | None = None  # the first source's type hint, if any

    def candidate(self) -> Candidate:
        return Candidate(self.start, self.end, type=self.type,
                         detector="+".join(self.sources) or None)


def _spans(rx: re.Pattern[str], text: str, group: int = 0) -> Iterable[tuple[int, int]]:
    for m in rx.finditer(text):
        if m.group(group):
            yield m.start(group) + 1, m.end(group)


def _after_honorific(text: str, start: int) -> bool:
    return bool(re.search(rf"\b{_HONORIFIC}\s+$", text[max(0, start - 1 - 12):start - 1]))


def _trim_name(text: str, s: int, e: int) -> tuple[int, int] | None:
    """Trim stop words off both ends of the name run ``text[s-1:e]``; None if too little is left
    (one word is kept only after an honorific)."""
    words = [(m.start() + s, m.end() + s - 1, m.group(0))
             for m in re.finditer(r"\S+", text[s - 1:e])]

    def drop(w: str) -> bool:
        return _is_stop(w) or bool(re.fullmatch(_CONNECTOR, w))

    while words and drop(words[0][2]):
        words.pop(0)
    while words and drop(words[-1][2]):
        words.pop()
    capital = [w for w in words if not re.fullmatch(_CONNECTOR, w[2])]
    if not capital or all(_is_stop(w[2]) for w in capital):
        return None
    if len(capital) < 2 and not _after_honorific(text, words[0][0]):
        return None
    return words[0][0], words[-1][1]


def propose(text: str, *, extra: Iterable[Candidate] = (), postal6: bool = True,
            detectors: dict | None = None) -> list[Proposal]:
    """Candidate spans in ``text``, sorted by position; each interval once."""
    found: dict[tuple[int, int], Proposal] = {}

    def add(s: int, e: int, source: str, type_: str | None = None) -> None:
        # trim whitespace and punctuation a pattern may have taken at either end
        while s <= e and text[s - 1] in " \t\r\n,;:(":
            s += 1
        while e >= s and text[e - 1] in " \t\r\n,;:)":
            if text[e - 1] == ")" and "(" in text[s - 1:e]:
                break
            e -= 1
        if e < s:
            return
        p = found.setdefault((s, e), Proposal(s, e, type=type_))
        if source not in p.sources:
            p.sources.append(source)
        if p.type is None:
            p.type = type_

    if not text:
        return []
    dets = detectors if detectors is not None else rules.detectors(postal6=postal6)
    for sp in rules.scan_text(text, dets):
        add(sp["start"], sp["end"], sp["detector"], sp["type"])
    for rx in _DATE_RX:
        for s, e in _spans(rx, text):
            add(s, e, "shape:date", "date")
    for rx in _PHONE_RX:
        for s, e in _spans(rx, text):
            add(s, e, "shape:phone", "phone")
    for s, e in _spans(_IDLIKE, text):
        if sum(c.isdigit() for c in text[s - 1:e]) >= 5:
            add(s, e, "shape:id")
    for s, e in _spans(_PLATE, text):
        if e - s + 1 >= 5:
            add(s, e, "shape:plate")
    for s, e in _spans(_NAME_RUN, text):
        if (t := _trim_name(text, s, e)) is not None:
            add(*t, "shape:name", "name")
    for s, e in _spans(_PARTICLE_NAME, text):
        add(s, e, "shape:name", "name")
    for s, e in _spans(_AFTER_HONORIFIC, text, group=1):
        if not _is_stop(text[s - 1:e]):
            add(s, e, "shape:name", "name")
    for rx in _ADDRESS_RX:
        for s, e in _spans(rx, text):
            add(s, e, "shape:address", "address")
    for label, rx in _SHI_RX:
        for s, e in _spans(rx, text):
            add(s, e, f"lexicon:{label}", label)
    for c in extra:
        add(c.start, c.end, c.detector or "extra", c.type)
    return sorted(found.values(), key=lambda p: (p.start, -p.end))


def contained(inner: Proposal, outer: Proposal) -> bool:
    """Whether ``inner`` lies within ``outer`` (and is not the same interval)."""
    return (outer.start <= inner.start and inner.end <= outer.end
            and (inner.start, inner.end) != (outer.start, outer.end))
