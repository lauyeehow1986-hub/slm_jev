"""Synthetic Singapore clinical corpus with gold PII/SHI spans (P2).

Everything here is invented: names are drawn from common-name pools, every number is random, and
emails use reserved ``example.*`` domains. Nothing is real patient data.

Each document is a free-text ``note`` or a structured ``cell`` (a single table value, sometimes
holding PII that belongs in another column). Records::

    {"id", "split", "kind": "note" | "cell", "template", "column", "text", "meta",
     "spans":  [{"start", "end", "match", "label", "type", "attrs"}],   # gold PII / SHI
     "decoys": [{"start", "end", "match", "type", "attrs"}]}            # proposable non-PII

Offsets follow the span contract: ``start`` is 1-based, ``end`` inclusive. Labels and types come
from ``schemas/labels.v1.json``.

Annotation conventions:
- Name spans exclude titles (``Mr``, ``Mdm``, ``Dr``). Short forms (``Mdm Tan`` -> ``Tan``) are
  still names.
- ``address`` covers block/house number, street, unit and building, but never ``Singapore`` or
  the postal code. ``postal_code`` is the 6 digits alone. Both carry ``attrs.property``.
- Dates of birth and death are gold; all other dates are decoys. Every date has ``attrs.role``.
- SHI spans cover the condition/test/treatment phrase, negated or not (``attrs.negated``).

Split hygiene: name, street and building pools are partitioned, so ``test`` never reuses a
name component, street or building seen in ``train``/``dev``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import string
from collections.abc import Callable
from pathlib import Path

from slmjev import rules
from slmjev.labels import load_labels

GENERATOR_VERSION = "synth.v1"
SPLITS = ("train", "dev", "test")

__all__ = ["GENERATOR_VERSION", "SPLITS", "column_peers", "generate", "generate_hard",
           "load_labels", "validate_doc", "write_jsonl"]

_TYPES: dict[str, str] = load_labels()["types"]

# --- vocabulary pools (partitioned by split, see _Pools) --------------------------------------

_POOLS = {
    "cn_surname": ["Tan", "Lim", "Lee", "Ng", "Ong", "Wong", "Goh", "Chua", "Chan", "Koh", "Teo",
                   "Ang", "Yeo", "Tay", "Ho", "Low", "Toh", "Sim", "Chong", "Chia", "Seah",
                   "Leong", "Foo", "Heng", "Quek", "Soh", "Loh", "Kwek", "Lau", "Yap"],
    "cn_given": ["Wei Ming", "Mei Ling", "Jun Jie", "Xin Yi", "Kai Wen", "Hui Min", "Zhi Hao",
                 "Li Ting", "Jia Hui", "Yong Sheng", "Siew Lan", "Ah Kow", "Boon Keng",
                 "Chee Hong", "Pei Shan", "Shu Fen", "Wen Jie", "Kok Leong", "Geok Choo",
                 "Swee Lian"],
    "en_given": ["Jane", "David", "Grace", "Ryan", "Michelle", "Kelvin", "Rachel", "Jonathan",
                 "Vivian", "Marcus", "Sharon", "Benjamin", "Joanne", "Terence", "Cheryl",
                 "Desmond", "Samantha", "Adrian", "Felicia", "Nicholas"],
    "ms_given_m": ["Muhammad Hafiz", "Ahmad Faizal", "Mohamed Rizal", "Syed Ismail", "Hairul",
                   "Azman", "Firdaus", "Iskandar", "Khairul Anwar", "Zulkifli", "Amirul",
                   "Haziq"],
    "ms_given_f": ["Nurul Aisyah", "Siti Aminah", "Nur Farhana", "Aishah", "Norhayati",
                   "Fatimah", "Rosnah", "Zarina", "Hidayah", "Farah Nadia", "Syafiqah",
                   "Nadiah"],
    "ms_father": ["Rahman", "Ismail", "Hassan", "Osman", "Abdullah", "Yusof", "Salleh",
                  "Ibrahim", "Kassim", "Rahim", "Jamal", "Hamid"],
    "in_given_m": ["Arun", "Rajesh", "Suresh", "Vikram", "Ganesh", "Prakash", "Ravi", "Senthil",
                   "Mohan", "Karthik", "Naveen", "Dinesh"],
    "in_given_f": ["Priya", "Kavitha", "Lakshmi", "Divya", "Anitha", "Meena", "Revathi",
                   "Shanti", "Deepa", "Malathi", "Sangeetha", "Vani"],
    "in_father": ["Subramaniam", "Rajendran", "Krishnan", "Muthu", "Ramasamy", "Pillai", "Nair",
                  "Gopal", "Balakrishnan", "Selvaraj", "Chandran", "Velu"],
    "eu_surname": ["de Souza", "Pereira", "Oliveiro", "Rodrigues", "Fernandez", "D'Cruz",
                   "Hendricks", "Scully", "Westerhout", "Aeria", "Martens", "Sequeira",
                   "de Silva", "Shepherdson", "Minjoot", "Fonseka"],
    "hdb_street": ["Ang Mo Kio Avenue 3", "Bedok North Street 1", "Jurong West Street 42",
                   "Tampines Street 21", "Yishun Ring Road", "Toa Payoh Lorong 4",
                   "Woodlands Drive 14", "Hougang Avenue 8", "Bukit Batok West Avenue 6",
                   "Pasir Ris Drive 3", "Sengkang East Way", "Punggol Field", "Clementi Avenue 2",
                   "Choa Chu Kang Avenue 4", "Serangoon North Avenue 1", "Bishan Street 12"],
    "condo_street": ["Upper Thomson Road", "Pasir Panjang Road", "Holland Road",
                     "Tanjong Katong Road", "Bukit Timah Road", "Marine Parade Road",
                     "Jalan Bukit Merah", "River Valley Road"],
    "condo_name": ["The Verdana", "Casuarina Residences", "Orchid Park View", "Sunhill Grove",
                   "Palm Crest", "Lakeshore Suites", "Kingsford Heights", "Emerald Vista"],
    "landed_street": ["Jalan Kembangan", "Lorong Chuan", "Sunset Way", "Jalan Bunga Rampai",
                      "Mimosa Walk", "Siglap Drive", "Frankel Avenue", "Greenwood Avenue",
                      "Chestnut Drive", "Jalan Kechubong", "Namly Avenue", "Jalan Tua Kong"],
}

_SHI = {
    "hiv_sti": ["HIV infection", "HIV positive status", "syphilis", "gonorrhoea",
                "chlamydia infection", "genital herpes"],
    "mental_health": ["major depressive disorder", "schizophrenia", "bipolar disorder",
                      "generalised anxiety disorder", "previous suicide attempt", "PTSD",
                      "anorexia nervosa"],
    "substance_use": ["alcohol use disorder", "methamphetamine use", "heroin dependence",
                      "IV drug use", "methadone maintenance", "cannabis use"],
    "genetic": ["BRCA1 mutation carrier", "Huntington disease gene positive", "Lynch syndrome",
                "cystic fibrosis carrier status", "genetically confirmed familial "
                "hypercholesterolaemia"],
    "reproductive_sexual": ["termination of pregnancy", "IVF treatment", "erectile dysfunction",
                            "infertility workup", "gender-affirming hormone therapy",
                            "recurrent miscarriage"],
    "other_sensitive": ["domestic violence", "sexual assault", "child protection referral",
                        "elder abuse concerns"],
}

_COMPLAINTS = ["chest pain", "shortness of breath", "fever and productive cough",
               "abdominal pain", "a fall at home", "palpitations", "left leg swelling",
               "giddiness", "poor oral intake"]
_PROCEDURES = ["coronary angiogram", "colonoscopy", "echocardiogram", "CT abdomen",
               "knee arthroscopy", "cataract surgery"]
_DEVICES = ["Permanent pacemaker", "ICD", "Insulin pump", "Loop recorder", "CGM sensor"]
_RELATIONS = ["wife", "husband", "son", "daughter", "sister", "brother", "mother", "father"]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_REF_YEAR = 2025  # "now" for ages; admission/clinic dates fall in 2019-2026
_SECTORS = [f"{i:02d}" for i in range(1, 83) if i != 74]  # SG postal sectors 01-82, no 74
_PLATE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O
_PLATE_CHECK = "AZYXUTSRPMLKJHGEDCB"


class _Pools:
    """Split-aware pools: test gets every 4th item, train/dev get the rest."""

    def __init__(self, split: str):
        unseen = split == "test"
        self._p = {k: [x for i, x in enumerate(v) if (i % 4 == 3) == unseen]
                   for k, v in _POOLS.items()}

    def __getitem__(self, key: str) -> list[str]:
        return self._p[key]


# --- document builder ------------------------------------------------------------------------


class _Doc:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._n = 0
        self.spans: list[dict] = []
        self.decoys: list[dict] = []

    def add(self, s: str) -> _Doc:
        self._parts.append(s)
        self._n += len(s)
        return self

    def gold(self, s: str, type_: str, **attrs) -> _Doc:
        start = self._n + 1
        self.add(s)
        self.spans.append({"start": start, "end": self._n, "match": s, "label": _TYPES[type_],
                           "type": type_, "attrs": attrs})
        return self

    def decoy(self, s: str, type_: str, **attrs) -> _Doc:
        start = self._n + 1
        self.add(s)
        self.decoys.append({"start": start, "end": self._n, "match": s, "type": type_,
                            "attrs": attrs})
        return self

    @property
    def text(self) -> str:
        return "".join(self._parts)


# --- value generators ------------------------------------------------------------------------


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.digits) for _ in range(n))


def _national_id(rng: random.Random, prefix: str) -> str:
    """A 9-char NRIC/FIN with a correct check letter (checked by rules.nric_valid)."""
    while True:
        body = prefix + _digits(rng, 7)
        for letter in string.ascii_uppercase:
            if rules.nric_valid(body + letter):
                return body + letter


def _temp_ic(rng: random.Random) -> str:
    return rng.choice("XY") + _digits(rng, rng.choice((7, 10))) + rng.choice(string.ascii_uppercase)


def _passport(rng: random.Random) -> str:
    fmt = rng.choice(("K#######L", "E########", "EA#######", "A########", "J#######"))
    return "".join(_digits(rng, 1) if c == "#" else
                   rng.choice(string.ascii_uppercase) if c == "L" else c for c in fmt)


def _mrn(rng: random.Random) -> str:
    return rng.choice((_digits(rng, 8), "MR" + _digits(rng, 7),
                       rng.choice(("HN", "PT")) + _digits(rng, 7)))


def _case(rng: random.Random) -> str:
    if rng.random() < 0.7:
        return _digits(rng, 10) + rng.choice(string.ascii_uppercase)
    return "V" + _digits(rng, 9)


def _phone(rng: random.Random, kind: str) -> str:
    first = rng.choice("89") if kind == "mobile" else "6"
    n = first + _digits(rng, 7)
    fmt = rng.choice(("{a} {b}", "{a}{b}", "{a}-{b}", "+65 {a} {b}", "+65{a}{b}", "(65) {a}{b}"))
    return fmt.format(a=n[:4], b=n[4:])


def _email(rng: random.Random, person: dict) -> str:
    parts = [p for p in person["parts"] if p]
    local = ".".join("".join(c for c in p.lower() if c.isalnum()) for p in parts[:2])
    if rng.random() < 0.5:
        local += _digits(rng, 2)
    return f"{local}@{rng.choice(('example.com', 'example.org', 'example.net'))}"


def _date(rng: random.Random, lo: int, hi: int) -> dt.date:
    start = dt.date(lo, 1, 1)
    return start + dt.timedelta(days=rng.randrange((dt.date(hi, 12, 31) - start).days))


_DATE_FORMATS = ("dmy_slash", "iso", "d_mon_y", "d-mon-y", "compact", "dmy_dot")


def _fmt_date(rng: random.Random, d: dt.date) -> tuple[str, str]:
    fmt = rng.choice(_DATE_FORMATS)
    return _fmt_as(d, fmt), fmt


def _fmt_as(d: dt.date, fmt: str) -> str:
    mon = _MONTHS[d.month - 1]
    return {
        "dmy_slash": f"{d.day:02d}/{d.month:02d}/{d.year}",
        "iso": d.isoformat(),
        "d_mon_y": f"{d.day} {mon} {d.year}",
        "d-mon-y": f"{d.day:02d}-{mon}-{d.year}",
        "compact": f"{d.year}{d.month:02d}{d.day:02d}",
        "dmy_dot": f"{d.day:02d}.{d.month:02d}.{d.year}",
    }[fmt]


def _card(rng: random.Random) -> str:
    body = "4" + _digits(rng, 14)
    check = next(c for c in string.digits if rules.luhn(body + c))
    n = body + check
    sep = rng.choice((" ", "-", ""))
    return sep.join(n[i:i + 4] for i in range(0, 16, 4))


def _plate(rng: random.Random) -> str:
    """SG-style plate. The check letter follows the commonly published LTA scheme; it is NOT
    verified against an authoritative source, so don't build a validator on this."""
    prefix = rng.choice("SEF") + rng.choice(_PLATE_LETTERS) + rng.choice(_PLATE_LETTERS)
    num = str(rng.randint(1, 9999))
    vals = [ord(prefix[1]) - 64, ord(prefix[2]) - 64] + [int(c) for c in num.zfill(4)]
    check = _PLATE_CHECK[sum(v * w for v, w in zip(vals, (9, 4, 5, 4, 3, 2), strict=True)) % 19]
    return prefix + num + check


def _serial(rng: random.Random) -> str:
    return rng.choice(("PM" + _digits(rng, 7), "SN-" + _digits(rng, 6),
                       "".join(rng.choice(string.ascii_uppercase) for _ in range(3))
                       + _digits(rng, 9), _digits(rng, 2) + "-" + _digits(rng, 4) + "-"
                       + _digits(rng, 2)))


def _person(rng: random.Random, P: _Pools, role: str) -> dict:
    eth = rng.choice(("chinese", "malay", "indian", "eurasian"))
    sex = rng.choice("mf")
    if eth == "chinese":
        sur, given = rng.choice(P["cn_surname"]), rng.choice(P["cn_given"])
        eng = rng.choice(P["en_given"]) if rng.random() < 0.4 else None
        full = f"{eng} {sur} {given}" if eng else f"{sur} {given}"
        initials = f"{sur} " + ".".join(w[0] for w in given.split()) + "."
        return dict(ethnicity=eth, sex=sex, full=full, short=sur, initials=initials,
                    parts=[eng or given, sur], role=role)
    if eth == "malay":
        given = rng.choice(P["ms_given_m" if sex == "m" else "ms_given_f"])
        father = rng.choice(P["ms_father"])
        full = f"{given} {'bin' if sex == 'm' else 'binte'} {father}"
        return dict(ethnicity=eth, sex=sex, full=full, short=given.split()[-1], initials=None,
                    parts=[given.split()[-1], father], role=role)
    if eth == "indian":
        given = rng.choice(P["in_given_m" if sex == "m" else "in_given_f"])
        father = rng.choice(P["in_father"])
        rel = "s/o" if sex == "m" else "d/o"
        full = f"{given} {rel} {father}" if rng.random() < 0.6 else f"{given} {father}"
        return dict(ethnicity=eth, sex=sex, full=full, short=given, initials=None,
                    parts=[given, father], role=role)
    eng, sur = rng.choice(P["en_given"]), rng.choice(P["eu_surname"])
    return dict(ethnicity=eth, sex=sex, full=f"{eng} {sur}", short=sur, initials=None,
                parts=[eng, sur], role=role)


def _title(person: dict) -> str:
    if person["role"] == "clinician":
        return "Dr"
    return "Mr" if person["sex"] == "m" else "Mdm"


def _name(rng: random.Random, doc: _Doc, person: dict, form: str | None = None) -> None:
    """Append a name mention; titles go outside the span."""
    form = form or rng.choices(("full", "caps", "titled_full", "initials"), (5, 2, 3, 1))[0]
    if form == "initials" and not person["initials"]:
        form = "full"
    attrs = dict(ethnicity=person["ethnicity"], role=person["role"], form=form)
    if form == "short":
        doc.add(_title(person) + " ").gold(person["short"], "name", **attrs)
    elif form == "titled_full":
        doc.add(_title(person) + " ").gold(person["full"], "name", **attrs)
    elif form == "caps":
        doc.gold(person["full"].upper(), "name", **attrs)
    elif form == "initials":
        doc.gold(person["initials"], "name", **attrs)
    else:
        doc.gold(person["full"], "name", **attrs)


def _address(rng: random.Random, P: _Pools, doc: _Doc, kind: str | None = None) -> None:
    kind = kind or rng.choice(("hdb", "hdb", "condo", "landed"))
    fl, unit = f"{rng.randint(2, 25):02d}", str(rng.randint(1, 999)).zfill(rng.choice((2, 3)))
    if kind == "hdb":
        blk = str(rng.randint(1, 999)) + (rng.choice("ABCD") if rng.random() < 0.15 else "")
        street = rng.choice(P["hdb_street"])
        addr = rng.choice((f"Blk {blk} {street} #{fl}-{unit}",
                           f"Block {blk} {street}, #{fl}-{unit}",
                           f"{blk} {street} #{fl}-{unit}"))
    elif kind == "condo":
        no, street = rng.randint(1, 500), rng.choice(P["condo_street"])
        condo = rng.choice(P["condo_name"])
        addr = rng.choice((f"{no} {street} #{fl}-{unit} {condo}",
                           f"{condo}, {no} {street} #{fl}-{unit}"))
    else:
        addr = f"{rng.randint(1, 120)}{rng.choice(('', '', 'A'))} {rng.choice(P['landed_street'])}"
    doc.gold(addr, "address", property=kind)
    postal = rng.choice(_SECTORS) + _digits(rng, 4)
    pre, post = rng.choice(((", Singapore ", ""), (" Singapore ", ""), (" S(", ")"), (" S", ""),
                            (", ", "")))
    doc.add(pre).gold(postal, "postal", property=kind).add(post)


def _gold_date(rng: random.Random, doc: _Doc, role: str, d: dt.date) -> None:
    s, fmt = _fmt_date(rng, d)
    doc.gold(s, "dob" if role == "dob" else "date_of_death", role=role, format=fmt)


def _decoy_date(rng: random.Random, doc: _Doc, role: str, d: dt.date | None = None) -> None:
    s, fmt = _fmt_date(rng, d or _date(rng, 2019, 2026))
    doc.decoy(s, "date", role=role, format=fmt)


# --- sentence builders (each appends one sentence + trailing space) --------------------------

_Ctx = dict  # rng, P, doc, patient


def _s_intro(c: _Ctx) -> None:
    rng, doc, pt = c["rng"], c["doc"], c["patient"]
    _name(rng, doc, pt)
    doc.add(f", {pt['age']}-year-old {'male' if pt['sex'] == 'm' else 'female'}")
    r = rng.random()
    if r < 0.45:
        doc.add(", NRIC ").gold(_national_id(rng, rng.choice("ST")), "nric")
    elif r < 0.65:
        doc.add(", FIN ").gold(_national_id(rng, rng.choice("FGM")), "fin")
    elif r < 0.75:
        doc.add(" (temp IC ").gold(_temp_ic(rng), "temp_ic").add(")")
    elif r < 0.85:
        doc.add(", passport no. ").gold(_passport(rng), "passport")
    doc.add(". ")


def _s_dob(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("DOB: ", "Date of birth ", "Born on ", "D.O.B. ")))
    year = _REF_YEAR - c["patient"]["age"] - 1
    _gold_date(rng, doc, "dob", _date(rng, year, year))
    doc.add(". ")


def _s_admission(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("Admitted on ")
    _decoy_date(rng, doc, "admission")
    pre, post = rng.choice(((" under case no. ", ""), (" (case ", ")"), (", visit no. ", ",")))
    doc.add(pre).gold(_case(rng), "case").add(post)
    doc.add(f" with {rng.choice(_COMPLAINTS)} for ").decoy(f"{rng.randint(1, 6)}/7", "measurement",
                                                           kind="duration")
    doc.add(". ")


def _s_mrn(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("MRN ", "MRN: ", "Hospital no. "))).gold(_mrn(rng), "mrn").add(". ")


def _s_vitals(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("BP ").decoy(f"{rng.randint(95, 180)}/{rng.randint(50, 100)}", "measurement",
                         kind="bp")
    doc.add(f" mmHg, HR {rng.randint(50, 120)}, SpO2 {rng.randint(90, 100)}% on RA, ")
    doc.decoy(f"Ward {rng.randint(40, 79)} Bed {rng.randint(1, 30)}", "location").add(". ")


def _s_labs(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(f"HbA1c {rng.randint(55, 110) / 10}% and eGFR {rng.randint(20, 110)} on ")
    _decoy_date(rng, doc, "lab")
    doc.add("; ICD-10 ").decoy(f"{rng.choice('EIJKN')}{rng.randint(10, 99)}.{rng.randint(0, 9)}",
                               "code").add(". ")


def _s_shi(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    cat = rng.choice(list(_SHI))
    phrase = rng.choice(_SHI[cat])
    negated = rng.random() < 0.2
    pre, post = rng.choice((("No history of ", "."), ("Denies ", ".")) if negated else (
        ("Background of ", "."), ("Known ", ", follows up at the specialist clinic."),
        ("History of ", "."), ("Referred for ", " counselling.")))
    doc.add(pre).gold(phrase, cat, negated=negated).add(post + " ")


def _s_address(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("Lives at ", "Address: ", "Home address ", "Stays at ")))
    _address(rng, c["P"], doc)
    doc.add(". ")


def _s_nok(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    nok = _person(rng, c["P"], "nok")
    doc.add(f"NOK is {rng.choice(_RELATIONS)} ")
    _name(rng, doc, nok, rng.choice(("full", "short", "titled_full")))
    doc.add(", HP ").gold(_phone(rng, "mobile"), "phone", kind="mobile")
    if rng.random() < 0.3:
        doc.add(", email ").gold(_email(rng, nok), "email")
    doc.add(". ")


def _s_contact(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    kind = rng.choice(("mobile", "landline"))
    doc.add("Contact ").gold(_phone(rng, kind), "phone", kind=kind)
    if rng.random() < 0.6:
        doc.add("; email ").gold(_email(rng, c["patient"]), "email")
    doc.add(". ")


def _s_fax(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("Please fax results to ", "Fax: ", "Reply by fax to ")))
    doc.gold(_phone(rng, "landline"), "fax").add(". ")


def _s_device(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(f"{rng.choice(_DEVICES)} inserted on ")
    _decoy_date(rng, doc, "procedure")
    doc.add(rng.choice((" (s/n ", " (serial ", " (device ID "))).gold(_serial(rng), "serial")
    doc.add("). ")


def _s_procedure(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(f"Underwent {rng.choice(_PROCEDURES)} on ")
    _decoy_date(rng, doc, "procedure")
    doc.add(". ")


def _s_discharge(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("Discharged on ")
    _decoy_date(rng, doc, "discharge")
    doc.add(". Plan: metformin ").decoy(f"{rng.choice((250, 500, 850))} mg", "measurement",
                                        kind="dose")
    doc.add(" BD, TCU in ").decoy(f"{rng.randint(1, 6)}/12", "measurement", kind="duration")
    doc.add(" at the clinic on ")
    _decoy_date(rng, doc, "clinic")
    doc.add(". ")


def _s_death(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("Pronounced dead on ", "Date of death: ", "Passed away on ")))
    _gold_date(rng, doc, "death", _date(rng, 2019, 2026))
    doc.add(f" at {rng.randint(0, 23):02d}{rng.choice(('00', '15', '30', '45'))}h. ")


def _s_clinician(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("Seen by ")
    _name(rng, doc, _person(rng, c["P"], "clinician"), rng.choice(("titled_full", "short")))
    if rng.random() < 0.4:
        doc.add(" (MCR ").gold(f"M{_digits(rng, 5)}{rng.choice(string.ascii_uppercase)}",
                               "licence").add(")")
    doc.add(". ")


def _s_billing(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    r = rng.random()
    if r < 0.4:
        doc.add("Deposit paid by card ").gold(_card(rng), "account")
    elif r < 0.7:
        doc.add("Refund to bank account ").gold(
            f"{_digits(rng, 3)}-{_digits(rng, 5)}-{_digits(rng, 1)}", "account")
    else:
        doc.add("Insurer member no. ").gold(f"HP{_digits(rng, 10)}", "beneficiary")
    doc.add(". ")


def _s_vehicle(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(rng.choice(("Brought in after a road accident involving vehicle ",
                        "Family car ", "Patient's motorcycle ")))
    doc.gold(_plate(rng), "vehicle").add(". ")


def _s_it(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("Scan report uploaded to ").gold(
        f"https://portal.example.org/r/{_digits(rng, 6)}", "url")
    doc.add(" from workstation ").gold(
        rng.choice((f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
                    f"192.168.{rng.randint(0, 255)}.{rng.randint(1, 254)}")), "ip").add(". ")


def _s_complaint(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add(f"Presented with {rng.choice(_COMPLAINTS)} for ").decoy(
        f"{rng.randint(1, 6)}/7", "measurement", kind="duration").add(". ")


def _s_letter_date(c: _Ctx) -> None:
    rng, doc = c["rng"], c["doc"]
    doc.add("Letter dated ")
    _decoy_date(rng, doc, "other")
    doc.add(". ")


# (sentence, probability) per template; the first entry of each is always included.
_TEMPLATES: dict[str, list[tuple[Callable[[_Ctx], None], float]]] = {
    "admission": [(_s_intro, 1), (_s_dob, .6), (_s_mrn, .5), (_s_admission, 1), (_s_vitals, .6),
                  (_s_shi, .4), (_s_address, .6), (_s_nok, .5), (_s_clinician, .5),
                  (_s_vehicle, .1)],
    "discharge": [(_s_intro, 1), (_s_admission, .8), (_s_procedure, .5), (_s_device, .35),
                  (_s_labs, .5), (_s_shi, .3), (_s_discharge, 1), (_s_contact, .5),
                  (_s_clinician, .6), (_s_billing, .2)],
    "referral": [(_s_letter_date, 1), (_s_intro, 1), (_s_dob, .7), (_s_shi, .6),
                 (_s_complaint, .7), (_s_fax, .6), (_s_contact, .4), (_s_clinician, .8)],
    "nursing": [(_s_vitals, 1), (_s_intro, .7), (_s_nok, .6), (_s_contact, .4),
                (_s_vehicle, .15), (_s_billing, .2)],
    "social_work": [(_s_intro, 1), (_s_address, .8), (_s_shi, .9), (_s_nok, .6),
                    (_s_billing, .5), (_s_contact, .4)],
    "death_summary": [(_s_intro, 1), (_s_dob, .7), (_s_admission, .7), (_s_death, 1),
                      (_s_nok, .6), (_s_clinician, .7)],
    "admin": [(_s_mrn, 1), (_s_it, .8), (_s_billing, .5), (_s_device, .3), (_s_fax, .3)],
    "negative": [(_s_complaint, 1), (_s_vitals, .8), (_s_labs, .6), (_s_procedure, .4),
                 (_s_letter_date, .3)],
}
_TEMPLATE_WEIGHTS = {"admission": 20, "discharge": 18, "referral": 14, "nursing": 12,
                     "social_work": 10, "death_summary": 8, "admin": 8, "negative": 10}


def _note(rng: random.Random, P: _Pools) -> tuple[str, _Doc]:
    name = rng.choices(list(_TEMPLATE_WEIGHTS), list(_TEMPLATE_WEIGHTS.values()))[0]
    doc = _Doc()
    patient = _person(rng, P, "patient") | {"age": rng.randint(18, 95)}
    c = {"rng": rng, "P": P, "doc": doc, "patient": patient}
    for i, (fn, p) in enumerate(_TEMPLATES[name]):
        if i == 0 or rng.random() < p:
            fn(c)
    doc._parts[-1] = doc._parts[-1].rstrip()
    return name, doc


# --- structured cells ------------------------------------------------------------------------

_PII_VALUES: dict[str, Callable[[random.Random, _Pools, _Doc], None]] = {
    "nric": lambda r, P, d: d.gold(_national_id(r, r.choice("ST")), "nric"),
    "fin": lambda r, P, d: d.gold(_national_id(r, r.choice("FGM")), "fin"),
    "temp_ic": lambda r, P, d: d.gold(_temp_ic(r), "temp_ic"),
    "phone": lambda r, P, d: d.gold(_phone(r, "mobile"), "phone", kind="mobile"),
    "email": lambda r, P, d: d.gold(_email(r, _person(r, P, "patient")), "email"),
    "case": lambda r, P, d: d.gold(_case(r), "case"),
    "mrn": lambda r, P, d: d.gold(_mrn(r), "mrn"),
    "name": lambda r, P, d: _name(r, d, _person(r, P, "patient"), r.choice(("full", "caps"))),
    "postal": lambda r, P, d: d.gold(r.choice(_SECTORS) + _digits(r, 4), "postal",
                                     property="unknown"),
    "serial": lambda r, P, d: d.gold(_serial(r), "serial"),
    "dob": lambda r, P, d: _gold_date(r, d, "dob", _date(r, 1930, 2015)),
}
_HOME_COLUMN = {"nric": "nric", "fin": "nric", "temp_ic": "nric", "phone": "phone",
                "email": "email", "case": "case_no", "mrn": "mrn", "name": "patient_name",
                "postal": "postal_code", "serial": "serial_no", "dob": "dob"}
_HOST_COLUMNS = ["procedure_date", "serial_no", "ward", "diagnosis", "remarks", "device_id",
                 "dose"]
_BURIED = [
    lambda r, P, d: (d.add("Call NOK "), _name(r, d, _person(r, P, "nok"), "short"),
                     d.add(" at "), d.gold(_phone(r, "mobile"), "phone", kind="mobile"),
                     d.add(".")),
    lambda r, P, d: (d.add("Pt IC "), d.gold(_national_id(r, "S"), "nric"),
                     d.add(", pls verify.")),
    lambda r, P, d: (d.add("Email report to "), d.gold(_email(r, _person(r, P, "nok")), "email")),
    lambda r, P, d: (d.add("Moved to "), _address(r, P, d), d.add(".")),
    lambda r, P, d: (d.add("Known "), d.gold("HIV infection", "hiv_sti", negated=False),
                     d.add(", on ART.")),
]


def _cell(rng: random.Random, P: _Pools) -> tuple[str, str, _Doc, dict]:
    doc = _Doc()
    r = rng.random()
    if r < 0.35:
        vtype = rng.choice(list(_PII_VALUES))
        _PII_VALUES[vtype](rng, P, doc)
        return "proper", _HOME_COLUMN[vtype], doc, {"misplaced": False, "value_type": vtype}
    if r < 0.6:
        column = rng.choice(("procedure_date", "ward", "diagnosis", "remarks", "dose"))
        if column == "procedure_date":
            _decoy_date(rng, doc, "procedure")
        elif column == "ward":
            doc.decoy(f"Ward {rng.randint(40, 79)}", "location")
        elif column == "dose":
            doc.decoy(f"{rng.choice((5, 10, 20, 40))} mg", "measurement", kind="dose")
        elif column == "diagnosis":
            doc.add(rng.choice(_COMPLAINTS))
        else:
            doc.add(rng.choice(("Stable, for review.", "No issues.", "Seen in clinic.")))
        return "clean", column, doc, {"misplaced": False}
    if r < 0.9:
        vtype = rng.choice([v for v in _PII_VALUES if v != "serial"])
        column = rng.choice(_HOST_COLUMNS)
        _PII_VALUES[vtype](rng, P, doc)
        return "misplaced", column, doc, {"misplaced": True, "value_type": vtype}
    rng.choice(_BURIED)(rng, P, doc)
    return "buried", "remarks", doc, {"misplaced": True, "buried": True}


# --- public API ------------------------------------------------------------------------------


def generate(split: str, n_notes: int, n_cells: int, seed: int) -> list[dict]:
    """Generate one split deterministically from ``seed``."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    rng = random.Random(f"{GENERATOR_VERSION}:{seed}:{split}")
    P = _Pools(split)
    docs = []
    for i in range(n_notes):
        template, doc = _note(rng, P)
        docs.append(_record(f"{split}-note-{i:05d}", split, "note", template, None, doc, {}))
    for i in range(n_cells):
        template, column, doc, meta = _cell(rng, P)
        docs.append(_record(f"{split}-cell-{i:05d}", split, "cell", template, column, doc, meta))
    return docs


def column_peers(column: str, n: int, key: str) -> list[str]:
    """``n`` clean values of ``column``, the rest of the table a cell sits in, for column-level
    checks (``slmjev.column``). One date format per column, as in a real export. Only date columns
    have peers; ``[]`` otherwise. Seeded by ``key`` alone, so :func:`generate` is unchanged."""
    if column != "procedure_date":
        return []
    rng = random.Random(f"{GENERATOR_VERSION}:peers:{column}:{key}")
    fmt = rng.choice(_DATE_FORMATS)
    return [_fmt_as(_date(rng, 2019, 2026), fmt) for _ in range(n)]


_HARD_PLACES = ("Block {b} Level {l} clinic", "Block {b}", "Tower Block", "#{l:02d}-{u} clinic")
_HARD_LABS = ("platelets", "platelet count", "PLT", "WBC count", "total bill")


def generate_hard(split: str, n: int, seed: int) -> list[dict]:
    """Notes built to trip the rule-certain fast path (``judge.rule_certain``): a 6-digit lab
    value with a valid postal sector right after a hospital block or unit, or right after an ID
    keyword, each next to a real address with its postal code. The decoys measure the rules'
    precision; the gold postal codes check that they still fire. Separate from :func:`generate`
    (own seed stream), so the main corpus is unchanged."""
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    rng = random.Random(f"{GENERATOR_VERSION}:hard:{seed}:{split}")
    P = _Pools(split)
    docs = []
    for i in range(n):
        doc = _Doc()
        value = rng.choice(_SECTORS) + _digits(rng, 4)
        lab = rng.choice(_HARD_LABS)
        if rng.random() < 0.7:
            place = rng.choice(_HARD_PLACES).format(b=rng.randint(1, 9), l=rng.randint(1, 12),
                                                    u=rng.randint(1, 99))
            doc.add(f"Reviewed at {place}, {lab} ")
        else:
            doc.add(f"{rng.choice(('NRIC', 'FIN', 'MRN', 'Passport'))} verified; {lab} ")
        doc.decoy(value, "measurement", kind="lab").add(". Lives at ")
        _address(rng, P, doc)
        doc.add(".")
        docs.append(_record(f"{split}-hard-{i:05d}", split, "note", "hard_decoy", None, doc, {}))
    return docs


def _record(id_, split, kind, template, column, doc: _Doc, meta) -> dict:
    return {"id": id_, "split": split, "kind": kind, "template": template, "column": column,
            "text": doc.text, "meta": meta, "spans": doc.spans, "decoys": doc.decoys}


def validate_doc(d: dict, labels: dict) -> None:
    """Raise AssertionError if ``d`` breaks the gold-record invariants."""
    allowed = set(labels["identifiers"]) | set(labels["shi"])
    for s in d["spans"]:
        assert d["text"][s["start"] - 1:s["end"]] == s["match"], (d["id"], s)
        assert s["label"] in allowed and labels["types"][s["type"]] == s["label"], (d["id"], s)
    for s in d["decoys"]:
        assert d["text"][s["start"] - 1:s["end"]] == s["match"], (d["id"], s)
        assert s["type"] in labels["decoy_types"], (d["id"], s)
    ivs = sorted((s["start"], s["end"]) for s in d["spans"])
    assert all(b[0] > a[1] for a, b in zip(ivs, ivs[1:], strict=False)), d["id"]


def write_jsonl(docs: list[dict], path: Path) -> dict:
    """Write one JSON document per line; return a manifest with count and SHA-256."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs).encode("utf-8")
    path.write_bytes(data)
    return {"file": path.name, "n_docs": len(docs), "sha256": hashlib.sha256(data).hexdigest(),
            "generator": GENERATOR_VERSION}
