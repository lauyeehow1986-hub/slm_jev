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
  particle with one word (``de Souza``); a single word only after an honorific or a role or
  relation word (``Nurse Lim``, ``SON VIJAY``, ``Caller: Aisyah``, ``Aisyah (daughter)``). Runs
  never cross a line break. Stop words (sentence starters, clinical words, acronyms) are trimmed
  off both ends of a run.
- ``address``: street addresses ending in a street-type word or starting with ``Jalan`` /
  ``Lorong``, with an optional block, building name and ``#floor-unit``; a numbered street with
  no block (``Woodlands Ave 6``).
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
from slmjev.judge import (
    _DEATH_LEAD,
    _LOGIN_COLUMN,
    _STREET_TYPES,
    _TABLE_DELIMS,
    DEATH_DATE,
    DONATION_NO,
    HANDLE,
    IMAGE_FILE,
    LOGIN,
    MASKED_NRIC,
    NRIC_TAIL,
    OCR_DATE,
    SOCIAL_LEAD,
    SPACED_NRIC,
    SPOKEN_DIGITS,
    SPOKEN_NRIC,
    SPOKEN_REF,
    Candidate,
    _cells,
    fixed_width_column,
    hl7_people,
    ocr_date,
    rule_certain,
    spoken_digits,
    table_column,
)

# --- dates ---------------------------------------------------------------------------------

# a date with OCR look-alike letters for digits (``l4.O2.l95l``); ``ocr_date`` validates it
_OCR_DATE = re.compile(rf"(?<![\w./-]){OCR_DATE}(?![\w./-])")

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]{0,6}\.?"
_NUM_END = r"(?![\d/\-]|\.\d)"  # a sentence's full stop may follow; another digit group may not
_DATE_RX = [
    re.compile(rf"(?<![\d/.\-])\d{{1,2}}[/.\-]\d{{1,2}}[/.\-](?:19|20)?\d{{2}}{_NUM_END}"),
    re.compile(rf"(?<![\d/.\-])(?:19|20)\d{{2}}[/.\-]\d{{1,2}}[/.\-]\d{{1,2}}{_NUM_END}"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?[ \-]{_MON}[ \-,]*(?:19|20)\d{{2}}\b", re.IGNORECASE),
    re.compile(rf"\b{_MON}[ \-]\d{{1,2}}(?:st|nd|rd|th)?,?[ \-](?:19|20)\d{{2}}\b", re.IGNORECASE),
    re.compile(r"(?<![\d])(?:19|20)\d{6}(?![\d])"),
    # Chinese year-month-day (``2004年3月8日``)
    re.compile(r"(?<!\d)(?:19|20)\d{2}[ \t]?年[ \t]?\d{1,2}[ \t]?月[ \t]?\d{1,2}[ \t]?日"),
]

# --- numbers and IDs -----------------------------------------------------------------------

_PHONE_RX = [
    re.compile(r"(?:\+65|\(\+?65\))[ -]?\d{4}[ -]?\d{4}(?!\d)"),
    re.compile(r"(?<![\d-])\d{4}-\d{4}(?![\d-])"),
    re.compile(r"(?<!\d)[3689]\d{3} \d{4}(?!\d)"),
    # an international number in groups (``+63 917 552 0184``); 8-15 digits, checked in code
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[ -]?\(?\d{1,4}\)?){2,5}(?![\d-])"),
]
# A local number a digit short (``9888 012``): 7 digits, proposed with no digit-count check.
_PHONE_SHORT = re.compile(r"(?<![\w+\-])[3689]\d{3}[ -]\d{3}(?![\d\-])")
# An extension after a phone number (``6225 1180 ext 312``) is proposed with the number.
_PHONE_EXT = re.compile(r"[ ,]*(?i:ext|extn|x)\.?[ ]?\d{1,5}(?!\d)")
# A token of letters, digits and inner hyphens with at least 5 digits in it (checked in code).
_IDLIKE = re.compile(r"(?<![\w-])[A-Za-z]{0,4}-?\d[\dA-Za-z-]*\d[A-Za-z]{0,2}(?![\w-])")
# Segments joined by slashes (``FDW/26/0071834``, ``WIC/2026/0914/55821``); 5+ digits, not a date.
_SLASH_ID = re.compile(r"(?<![\w/.:-])[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+){2,}(?![\w/-])")
_SLASH_DATE = re.compile(r"\d{1,4}/\d{1,2}/\d{1,4}")
# A vehicle plate: 1-3 letters, 1-4 digits, a check letter (``FWL4331H``, ``SBA123A``), maybe
# written in groups (``FBL 6632 E``).
_PLATE = re.compile(r"\b[A-Z]{1,3} ?\d{1,4} ?[A-Z]\b")
# A picture or scan file name (``IMG_20260923_1542.jpg``): it can point to a photograph.
_IMAGE_FILE = re.compile(rf"(?<![\w.-]){IMAGE_FILE}(?![\w])")
_SPOKEN_DIGITS = re.compile(rf"(?<![\w-]){SPOKEN_DIGITS}(?![\w-])")
_SPOKEN_REF = re.compile(rf"(?<![\w-]){SPOKEN_REF}(?![\w-])")
_SPOKEN_NRIC = re.compile(rf"(?<![\w-]){SPOKEN_NRIC}(?![\w-])")
# a date after a word saying someone died, a year alone too (``d. 1998``; notes_v14)
_DEATH_DATE = re.compile(rf"(?<![\w/.-]){DEATH_DATE}(?![\w/-]|\.\d)")
# a log-in name alone in a cell of a fixed-width table (see ``fixed_width_column``)
_LOGIN_CELL = re.compile(rf"(?:^|(?<=  ))({LOGIN})(?=  |[ \t]*$)", re.MULTILINE)
# A handle after a messaging or social-media keyword (``WeChat ID linzq_1992sg``).
_HANDLE = re.compile(rf"{SOCIAL_LEAD}({HANDLE})(?![\w@])")
# A URL without a scheme, with a path (``social.example.com/hafiz.jamal.1993``).
_URL_PATH = re.compile(r"(?<![\w@./-])(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                       r"\.[A-Za-z]{2,6}/[\w.%?=&#~+/-]*[\w/]")
# Any letters-digits token (``TCM-P1033``); proposed only when a rule is sure of it (a reference
# right after ``Reg. No.``), which covers shorter references than ``_IDLIKE``.
_TOKEN = re.compile(r"(?<![\w/.:-])[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*(?![\w/-])")
# A number with a year after a slash and a short prefix (``CC 1187/2026``, ``No. 482/2025``),
# or with the year first (``CC 2026/1187``).
# The prefix may have two parts (``FC/OSM 1482/2026``).
_NUM_YEAR = re.compile(r"(?<![\w/])(?:[A-Z]{1,4}(?:/[A-Z]{1,4})?\.? ?)?(?:\d{1,6}/(?:19|20)\d{2}|"
                       r"(?:19|20)\d{2}/\d{3,6})(?![\w/])")
# Letter-prefixed groups of digits joined by dots (``CT.26.0914.00382``); 5+ digits in code.
_DOTTED_ID = re.compile(r"(?<![\w.])[A-Za-z]{1,4}\.\d{2,}(?:\.\d{2,})+(?!\w|\.\d)")
# An ISBT 128 blood donation number (``W0417 26 318857 K``)
_DONATION_NO = re.compile(rf"(?<![\w-]){DONATION_NO}(?![\w-])")
# Initials signing a record (``Signed: L.W.X.``, ``Sgd K.M.T``).
_INITIALS = re.compile(r"(?i:\b(?:signed|sgd|initials?|countersigned)\b(?:[ \t]+by)?[ \t]*:?[ \t]*)"
                       r"((?:[A-Z]\.){1,3}[A-Z]\.?)(?![\w.])")
# A masked NRIC/FIN (``G****262U``).
_MASKED_NRIC = re.compile(rf"(?<![\w*#]){MASKED_NRIC}(?![\w*])")
_SPACED_NRIC = re.compile(rf"(?<![\w-]){SPACED_NRIC}(?![\w-])")
# An NRIC/FIN tail given on its own (``NRIC ending 412D``), group 1.
_NRIC_TAIL = re.compile(NRIC_TAIL)
# The value after an ID field label, which may hold spaces (``MRN: BTC 22 118 406``,
# ``donation no. W0417 26 118203 X``, ``Policy no.: HS-IP-7739 0021 45``); see ``_field_ids``.
_ID_FIELD = re.compile(
    r"(?i:\b(?:MRN|HRN|NRIC|FIN|IC|passport|case|visit|episode|encounter|admission|account|acct|"
    r"policy|claim|member|employee|staff|donation|accession|specimen|sample|lab|serial|record|"
    r"file|ref|reference|ID)\b(?:[ \t]*(?:nos?\b\.?|numbers?\b|num\b|#))?)[ \t]*"
    # a colon, a hash, a full stop or a dotted leader (``NRIC ......... S 7709 506 C``; notes_v15)
    r"(?:[:#]|\.+)?[ \t]*(?=[A-Za-z0-9])")
# a token of the value; inner dots too (``Accession: CT.26.0914.00382``)
_FIELD_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9/.\-]*[A-Za-z0-9])?")
# a short upper-case code in a value, maybe in parts (``case no. FC/OSM 1482/2026``)
_FIELD_CODE = re.compile(r"[A-Z]{1,4}(?:/[A-Z]{1,4})*")
# an @handle that is not part of an e-mail address
_AT_HANDLE = re.compile(r"(?<![\w.@])@[A-Za-z][A-Za-z0-9._]{1,30}[A-Za-z0-9_]")

# --- names ---------------------------------------------------------------------------------

_HONORIFIC = r"(?:Mr|Mrs|Ms|Mdm|Madam|Miss|Dr|Prof|Mstr|Master)\.?"
# Title case (``Tan``, ``D'Cruz``, ``Ah-Kow``, ``E-Lynn``), upper case (``TAN``, ``D'CRUZ``),
# initials (``G.C.``) or the ``Md.`` / ``MD.`` short form of Mohammad
# Letters may carry accents (``Nguyễn Thị Hoa``, ``José Peña``; Latin-1, Latin Extended-A, the
# Vietnamese horned letters and Latin Extended Additional): those ranges mix cases, so they count
# as both.
_UP = "A-ZÀ-ÖØ-ÞĀ-ſƠƯẠ-ỹ"
_LO = "a-zß-öø-ÿĀ-ſơưẠ-ỹ"
_WORD = (rf"(?:[{_UP}]'?[{_UP}]?[{_LO}]+(?:['\-][{_UP}]?[{_LO}]+)*|[{_UP}]-[{_UP}]"
         rf"(?:[{_LO}]+|[{_UP}]+)|[{_UP}]'?[{_UP}]+(?:['\-][{_UP}]+)*|[A-Z]\.(?:[A-Z]\.)+|M[Dd]\.)")
_PARTICLE = r"(?:van|von|de|da|dos|del|la|le)"
_CONNECTOR = rf"(?i:bin|binti|binte|bte|b\.|d/o|s/o|a/l|a/p|@|{_PARTICLE})"
# The words of a name are joined by spaces or tabs, never a line break: a run across lines joins
# a heading to the next line (``LABORATORY REPORT\nPatient Name``, P7).
# Words of a run are one or two spaces (or a tab) apart: a wider gap is a fixed-width table's
# column gap (``TIMESTAMP            USER-ID    USER NAME``, notes_v14).
_GAP = r"(?: {1,2}|\t)"
_NAME_RUN = re.compile(rf"(?<![\w']){_WORD}(?:(?:{_GAP}{_CONNECTOR})?{_GAP}{_WORD}){{1,5}}"
                       rf"(?![\w'])")
# ``de Souza``: a lower-case surname particle and one capitalised word
_PARTICLE_NAME = re.compile(rf"(?<![\w']){_PARTICLE}[ \t]+{_WORD}(?![\w'])")
# Role and relation words that are usually followed (``Nurse Lim``, ``SON VIJAY``,
# ``Caller: Aisyah``) or tagged (``Aisyah (daughter)``) by one person's name.
# (``daughter-in-law Deepa``, notes_v5)
_ROLES = (r"(?:sons?|daughters?|brothers?|sisters?|mother|father)-in-law|"
          r"nurse|sn|sons?|daughters?|wife|husband|mother|father|brothers?|sisters?|spouse|"
          r"partner|children|child|grandchildren|siblings?|parents?|relatives?|"
          r"caller|carer|caregiver|helper|maid|guardian|nok|informant|friend|neighbou?r|"
          r"uncle|aunt|aunty|auntie|grandmother|grandfather|grandson|granddaughter|niece|"
          r"nephew|cousin|colleague|supervisor|physio|physiotherapist|therapist|"
          r"endoscopist|surgeon|anaesthetist|anesthetist|pharmacist|dietitian|counsell?or|"
          r"interpreter|translator|witness|visitors?|fdw|girlfriend|boyfriend|fianc[eé]e?|"
          r"driver|paramedic|proband|named|called|known[ \t]+as|baby[ \t]+of|family[ \t]+of|"
          r"witnessed(?:[ \t]+by)?|"
          # Malay and Indonesian relations (``anak perempuan Salmah``, ``cucu Irfan``, notes_v9)
          r"anak(?:[ \t]+(?:perempuan|lelaki|laki-laki))?|cucu|isteri|istri|suami|ibu|bapa|"
          r"ayah|abang|kakak|adik|menantu|sepupu|"
          # shorthand relations (``Husb (Khairul)``, ``dtr Alicia``, ``my bro Alvin``; notes_v10)
          r"husb|hubby|bro|sis|dtr|"
          # Malay and Indonesian terms of address and form labels, Tagalog relations
          # (``Jururawat: Zarina``, ``Puan Rosnah``, ``Ibu Sari``, ``Pak Budi``; notes_v12)
          r"encik|puan|cik|tuan|pak|bu|jururawat|perawat|penjaga|majikan|penerjemah|"
          r"penterjemah|doktor|dokter|pesakit|pasien|bidan|asawa|kapatid|nars|pasyente")
# Staff and form-field abbreviations that take a colon or hyphen before one name (``PT:
# Rajeswari``, ``DSA: Salina``, ``Bed 2 - RAJOO``); case-sensitive, so ``pt`` in prose is no cue.
_STAFF = r"PT|OT|ST|DSA|RN|SSN|SRN|EN|MO|HO|MSW|SW|APN|NC|CM"
_FIELD = (rf"(?:\b(?:{_STAFF}|Pt|Patient|Client|Name|Attn|Re)|\bBed[ \t]*\d{{1,3}}[A-Z]?)"
          r"[ \t]*[:\-]")
# a salutation or greeting before a name (``Dear Siti``, ``Hi Joyce``, ``Morning Jess``)
_SALUTE = r"\b(?:Dear|Hi|Hello|Hey|(?:Good[ \t]+)?(?:[Mm]orning|[Aa]fternoon|[Ee]vening))"
# A single word is proposed only after one of these cues: an honorific, a role word, a field
# label or a salutation.
_CUE = rf"(?:\b{_HONORIFIC}|(?i:\b(?:{_ROLES})\b)[:\-]?|{_FIELD}|{_SALUTE})"
# ``Dr Tan``, and with initials first: ``Dr R. Balakrishnan``, ``Mr K.M. Wong``
_AFTER_HONORIFIC = re.compile(rf"\b{_HONORIFIC}\s+((?:[A-Z]\.[ ]?){{0,3}}{_WORD})(?![\w'])")
# ``Nurse Lim``, ``NOK: SON VIJAY``, ``Wife (Rosnah)``, ``PT: Rajeswari``, ``Dear Siti``
_AFTER_ROLE = re.compile(rf"(?:(?i:\b(?:{_ROLES})\b)(?:[:\-]?[ \t]+|[ \t]*\([ \t]*)"
                         rf"|{_FIELD}[ \t]*|{_SALUTE}[ \t]+)({_WORD})(?![\w'])")
# ``Aisyah (daughter)``, ``Balan (MSW)``
_BEFORE_ROLE = re.compile(rf"(?<![\w'])({_WORD})[ \t]*\((?:(?i:{_ROLES})|{_STAFF})\)")
# a name signing off a letter, e-mail or message (``Thanks, Farhan``, ``Regards,\nMei Ling``)
_SIGNOFF = re.compile(r"\b(?:Thanks|Thank you|Many thanks|Regards|Best regards|Kind regards|"
                      r"Warm regards|Best wishes|Cheers|Sincerely|Yours sincerely|"
                      rf"Yours faithfully)[,.!]?[ \t]*\n?[ \t]*({_WORD})(?![\w'])")
# ``Siva's wife``: a possessive before a relation word
_POSSESSIVE = re.compile(rf"(?<![\w'])({_WORD})['’]s[ \t]+(?i:{_ROLES})\b")
# a name or nickname in quotes (``known as "Ah Boy"``, ``Supachai ("Jay")``), maybe ending in one
# or two initials (``name "ARUMUGAM V"``, ``"TAN B L / S3318204Z"``); a label's ID may follow
_QUOTED = re.compile(rf"(?<!\w)[\"“]({_WORD}(?:[ \t]+{_WORD}){{0,2}}(?:[ \t]+[A-Z]\.?){{0,2}})"
                     r"(?:[\"”](?!\w)|[ \t]*/[ \t]*[A-Z]?\d)")
# initials alone on a line signing a message (``> AP``, ``-- KL``), maybe with a staff role
# (``-- PN/HO``)
_INITIALS_LINE = re.compile(rf"^[ \t]*(?:>+|-{{1,2}})?[ \t]*([A-Z]{{2,3}})(?:/(?:{_STAFF}))?"
                            r"[ \t]*$", re.MULTILINE)
# Undotted initials after a word saying who checked, signed or wrote (``Checked: CMX/TYS``,
# ``Minutes taken by BT``; notes_v12); each part of a ``/`` pair is its own candidate
_INITIALS_CUE = re.compile(
    r"(?i:\b(?:signed|sgd|initials?|countersigned|checked|dispensed|verified|prepared|packed|"
    r"witnessed|counted|scribe|minuted|(?:minutes|notes|taken|recorded|written)[ \t]+by|"
    # a list's status before a patient's initials (``5. Withdrawn: KPL``; notes_v14)
    r"withdrawn|deferred|postponed)\b"
    r"(?:[ \t]+by)?[ \t]*[:\-]?[ \t]*)"
    r"([A-Z]{2,3}(?:[ \t]*/[ \t]*[A-Z]{2,3})*)(?!\w|\.\w)")
# Initials opening a line as a speaker's label (``CY: Explained ...``), taken when the same
# initials open two lines or more
_SPEAKER = re.compile(r"^[ \t]*(?:[-*•][ \t]*)?([A-Z]{2,3})[ \t]*:[ \t]*(?=[A-Za-z])",
                      re.MULTILINE)
# Initials a record gives a person, in brackets after the name (``Dr Clara Yeo [CY]``, ``wife
# Kamala [K]``, ``Benjamin Tan (BT)``; notes_v12). One letter only in square brackets: ``(M)``
# is a sex. Only a role in brackets may stand between the name and its initials.
_LEGEND = re.compile(r"\[([A-Z]{1,3})\]|\(([A-Z]{2,3})\)")
_LEGEND_GAP = re.compile(r"[ \t]*(?:\([^()\n]{0,40}\)[ \t]*)?")
# a person in a key=value export (``order.by=DR_NAIR_SUNIL``), and a login there
# (``verified.by=lwchong``; notes_v12)
_KV_NAME = re.compile(r"(?i:\b(?:by|name|doctor|dr|clinician|nurse|author|requester))[ \t]*="
                      r"[ \t]*(?:DR_|Dr_)?([A-Za-z]+(?:_[A-Za-z]+){1,3})(?![\w])")
_KV_LOGIN = re.compile(r"(?i:\b(?:by|user|userid|login|username))[ \t]*=[ \t]*"
                       r"([a-z][a-z0-9.]{2,19}[a-z0-9])(?![\w.])")
# A name written in lower case after a lower-case honorific, as dictation software and quick
# phone notes write it (``mister lim ah beng``, ``doctor harpreet core``; notes_v12). Each run of
# one to three words up to a stop word is proposed; the judge picks. Lower-case relation words
# (``son marcus``) were tried on notes_v1-v12: 29 candidates for 1 name.
_LOWER_AFTER_HONORIFIC = re.compile(
    r"(?<![\w])(?:mr|mrs|ms|mdm|madam|dr|mister|missus|doctor|prof)\.?[ \t]+"
    r"([a-z][a-z'’-]*(?:[ \t]+[a-z][a-z'’-]*){0,2})")
# ... and a common verb or adverb ends the run: ``doctor`` is often a plain noun (``doctor say
# hip fracture``, ``the GI doctor also wants``; notes_v4-v11)
_LOWER_NOT_NAME_WORDS = """
    say says said also write writes wrote will would can could should may might must did does
    do is was are has have had want wants wanted ask asks asked tell tells told give gives gave
    ok okay already just not never still then end come came go went see saw check checked
    dictating dictated speaking here
    """
_LOWER_NOT_NAME = set(_LOWER_NOT_NAME_WORDS.split())
# Names in Tamil, Devanagari, Bengali, Thai or Myanmar script: glossed in Latin letters in
# brackets (``ரேவதி (Revathi)``), or after a relation word or a name label of that language
# (``மகள் ரேவதி``, ``நோயாளி: லட்சுமி``; notes_v12)
_SCRIPT = r"[\u0900-\u097F\u0980-\u09FF\u0B80-\u0BFF\u0E00-\u0E7F\u1000-\u109F]+"
_SCRIPT_GLOSSED = re.compile(rf"({_SCRIPT})[ \t]*[(（]"
                             rf"({_WORD}(?:[ \t]+(?:{_CONNECTOR}[ \t]+)?{_WORD}){{0,4}})[)）]")
_SCRIPT_ROLES = ("நோயாளி|பெயர்|மகள்|மகன்|மனைவி|கணவர்|தாய்|தந்தை|"  # Tamil
                 "नाम|मरीज़|मरीज|बेटा|बेटी|पत्नी|पति|"  # Hindi
                 "নাম|রোগী|ছেলে|মেয়ে|স্ত্রী|স্বামী|"  # Bengali
                 "ชื่อ|ผู้ป่วย|คุณ")  # Thai
_SCRIPT_AFTER_ROLE = re.compile(rf"(?:{_SCRIPT_ROLES})[ \t]*[:：]?[ \t]*({_SCRIPT})")
# ... or in brackets after a name in Latin letters (``SOMCHAI KAEWKLA (สมชาย แก้วกล้า)``;
# notes_v13)
_SCRIPT_BRACKETED = re.compile(rf"[A-Za-z][ \t]*[(（]({_SCRIPT}(?:[ \t]+{_SCRIPT}){{0,3}})[)）]")
# ... or a name in Latin letters after such a word (``மகன் Suresh``, notes_v14)
_LATIN_AFTER_SCRIPT_ROLE = re.compile(rf"(?:{_SCRIPT_ROLES})[ \t]*[:：]?[ \t]*"
                                      rf"({_WORD}(?:[ \t]+(?:{_CONNECTOR}[ \t]+)?{_WORD}){{0,3}})"
                                      r"(?![\w'])")
# One word signing a line with the signer's department after a comma (``Hamidah, Medical
# Records.``; notes_v14)
_SIGN_DEPT = re.compile(rf"^[ \t]*(?:[-–—][ \t]*)?({_WORD}),[ \t]*(?:[A-Z][a-z]+[ \t]+){{0,3}}"
                        r"(?:Records|Affairs|Office|Department|Dept|Unit|Services|Team|Section|"
                        r"Registry|Pharmacy|Admissions|Billing|Finance|Counter)\b",
                        re.MULTILINE)
# The name-slot sweep (P22): slots where a person stands, so one word or initials are proposed
# however they are written. On notes_v15 every silent miss was a person or an ID in such a slot
# that no shape above proposed.
_SLOT_TOKEN = rf"(?:(?:[A-Z]\.){{2,4}}|[A-Z]{{2,4}}(?![\w'])|{_WORD})"
# ... after a staff role written without a colon (``RN AFR``, ``MO Priya``); case-sensitive
# (not across a fixed-width column gap: ``RN     EDIT``)
_STAFF_SLOT = re.compile(rf"(?<![\w/])(?:{_STAFF}){_GAP}((?:[A-Z]\.){{2,4}}|[A-Z]{{2,4}}(?![\w'])"
                         r"|[A-Z][a-z]+(?![\w'])(?![ \t]+[A-Z][a-z]))")
# ... before an identifier in brackets (``source pt H.K.L. (EG0999812H)``,
# ``LTS (MRN 71-440-9921)``)
_BEFORE_BRACKET_ID = re.compile(rf"(?<![\w.'])({_SLOT_TOKEN})[ \t]*\("
                                r"(?:(?i:MRN|HRN|NRIC|FIN|IC|ID)[ \t]*[:#.]?[ \t]*)?"
                                r"[A-Za-z]{0,3}\d[\d\- ]{3,}")
# ... initials in brackets after a bed or room number (``52-01 (KH) after OGD``), not after a
# phone number (``6225 8814 (DID)``)
_NUMBER_INITIALS = re.compile(r"(?<![\w-])\d{1,3}(?:-\d{1,3})?[A-Z]?[ \t]*\(([A-Z]{2,3})\)")
# ... the rest of a name a line break cut after a title (``cc Dr Ananda\n     Ravindran, O&G``)
_NAME_WRAP = re.compile(rf"\b{_HONORIFIC}[ \t]+{_WORD}[ \t]*\n[ \t]*({_WORD})"
                        r"(?=[ \t]*(?:[,(]|$))", re.MULTILINE)
# ... a dash sign-off with a role ending a line, in any case (``next hv 1 oct. -sn fatimah``)
_DASH_SIGNOFF = re.compile(rf"(?:^|(?<=[ \t.]))-{{1,2}}[ \t]*(?i:{_STAFF}|sn|dr|nurse)\.?[ \t]+"
                           r"([A-Za-z][a-z]+(?:[ \t][A-Za-z][a-z]+)?)[ \t]*$", re.MULTILINE)
# ... dotted initials anywhere (``H.K.L.``), less the dotted abbreviations of dosing and prose
_DOTTED_INITIALS = re.compile(r"(?<![\w.])((?:[A-Z]\.){2,4})(?!\w)")
_DOTTED_NOT_NAME = {"A.M.", "P.M.", "E.G.", "I.E.", "N.B.", "P.S.", "U.S.", "U.K.", "B.D.", "O.D.",
                    "O.M.", "O.N.", "P.O.", "P.R.N.", "T.D.S.", "T.I.D.", "Q.I.D.", "Q.D.", "I.V.",
                    "I.M.", "S.C.", "S.L.", "N.B.M.", "R.I.P.", "D.O.B.", "D.O.D.", "M.D.",
                    "M.B.B.S.", "A.D.", "B.C.", "C.C.", "N.A.", "N.K.A.", "N.K.D.A.", "Y.O."}
# a table column with a title-case header line (``No  Name   ID   Mobile``): three or more short
# labels two or more spaces apart
_FIXED_TITLE_HEAD = re.compile(r"[A-Z][A-Za-z0-9/#()&'.-]*(?: [A-Za-z][A-Za-z0-9/#()&'.-]*){0,2}")
# A DNA (STR) profile: two or more loci with their alleles (``D3S1358 15/16, vWA 17/18``)
_STR_LOCUS = (r"(?:D\d{1,2}S\d{2,4}|vWA|VWA|FGA|TH01|TPOX|CSF1PO|SE33|Penta[ \t]?[DE]|Amelogenin"
              r"|AMEL)[ \t]*:?[ \t]*"
              r"(?:\d{1,2}(?:\.\d)?(?:[ \t]*/[ \t]*\d{1,2}(?:\.\d)?)?|X/?Y|X/?X)")
_STR_PROFILE = re.compile(rf"(?<!\w){_STR_LOCUS}(?:[ \t]*[,;][ \t]*{_STR_LOCUS})+"
                          r"(?![\w/])")
# a name in Chinese characters in brackets (``TAN Bee Hwa (陈美华)``) or after a name field
_HAN_NAME = re.compile(r"(?:(?<=\()|(?<=（)|(?<=[Nn]ame:)[ \t]*|(?<=[Pp]atient:)[ \t]*|"
                       r"(?<=姓名[:：])[ \t]*)"
                       r"([一-鿿]{2,4})(?=[)）]|[ \t,;.]|$)", re.MULTILINE)
# common Chinese surnames, simplified and traditional (the compound ones start with 欧/歐/司/上)
_HAN_SURNAMES = ("陈陳林黄黃李张張王吴吳刘劉蔡杨楊郑鄭许許谢謝郭洪曾邱罗羅周何梁叶葉方苏蘇胡高"
                 "萧蕭庄莊潘江余赖賴卢盧彭朱徐钟鍾韩韓孙孫马馬邓鄧杜魏傅沈姚程汤湯温溫宋严嚴"
                 "柯施董石丁薛詹纪紀范戴游袁姜麦麥伍黎欧歐章符翁冯馮邝鄺卓尤龚龔骆駱连連文田"
                 "包孔易甘侯白凌关關邢曹蒋蔣谭譚贺賀金秦崔顾顧龙龍万萬钱錢吕呂任夏陆陸毛邹鄒"
                 "熊唐赵趙郝殷雷阮聂聶史汪岑巫容区區司上")
# ... or straight after a capitalised romanised word (``Mdm Fong Siew Lan 方秀兰``) when it starts
# with a surname: herbs and clinic words follow lower-case words or have no surname first
_HAN_AFTER_ROMAN = re.compile(rf"\b[A-Z][A-Za-z'\-]*[ \t]+([{_HAN_SURNAMES}][一-鿿]{{1,3}})"
                              r"(?=[)）]|[ \t,;.]|$)", re.MULTILINE)
# ... or after any field label's colon (``医师 Physician: 梁国栋``, notes_v9), again only when it
# starts with a surname: TCM diagnoses and formulas follow colons too (``证: 肝郁脾虚``)
_HAN_AFTER_COLON = re.compile(rf"[:：][ \t]*([{_HAN_SURNAMES}][一-鿿]{{1,3}})"
                              r"(?=[)）]|[ \t,;.]|$)", re.MULTILINE)
# ... or after a role word and a space (``医师 李建国``, notes_v11), again from a surname
_HAN_ROLES = ("主治医生|主治医师|医师|醫師|医生|醫生|护士|護士|主任|患者|病人|家属|家屬|联系人|"
              "聯絡人|签名|簽名|经手人|药剂师|藥劑師|治疗师|治療師|姓名")
_HAN_AFTER_ROLE = re.compile(rf"(?:{_HAN_ROLES})[ \t]*[:：]?[ \t]*([{_HAN_SURNAMES}][一-鿿]{{1,3}})"
                             r"(?=[)）(（]|[ \t,;.，。]|$)", re.MULTILINE)
# the next names of a list after a cued one (``sons Irfan and Hakim``, ``Irfan, Hakim``)
_LIST_NEXT = re.compile(rf"(?:[ \t]*[,/&][ \t]*|[ \t]+and[ \t]+)({_WORD})(?![\w'])")
# ``KOH WEI LIANG, DARREN`` / ``BAUTISTA, Maricel Dizon``: an upper-case surname, a comma, then
# the given names; after a field label or at the start of a line.
_INVERTED = re.compile(rf"(?:(?<=:)|(?<=:[ \t])|(?<=:[ \t]{{2}})|^)[ \t]*"
                       rf"([A-Z][A-Z'\-]+(?: [A-Z][A-Z'\-]+){{0,3}}, {_WORD}(?: {_WORD}){{0,3}})"
                       rf"(?![\w'])", re.MULTILINE)
# Words that end an organisation's name (``Tan Tock Seng Hospital``, ``Lionheart Assurance``,
# ``Ban Lee Huat Construction Pte Ltd``): a run up to one of these is the organisation, not a
# person; only the words after it can still be a name.
_ORG = """
    hospital hospice community clinic clinics polyclinic centre center home homes lab labs
    laboratory laboratories assurance insurance insurer claims police school college university
    institute pte ltd llp inc corp company construction services trading enterprise enterprises
    holdings group grant fund scheme foundation society association ministry board council
    authority agency bank church mosque temple court station department dept office
    crematorium columbarium parlour parlor branch
    """
# ... and words that end a heading, a service or a document rather than a name (``Work Pass
# Division``, ``STUDY ENROLMENT``, ``Speech and Language Therapy``, ``Varghese LLC``, P15)
_HEADING = """
    division committee courts llc logistics engineering transport ambulance service
    therapy pathology enrolment enrollment transfer consultation notice guidelines statement
    message appointments inpatients outpatients outpatient holder shield room day gh sheet scale
    request log register borang pendaftaran mdt
    """
_ORG_WORDS = set(_ORG.split()) | set(_HEADING.split())
_FRAGMENT = re.compile(r"[\W_]*(?:\d{1,3}|[A-Za-z])[\W_]*")
# Family terms of address (``Ah Ma``, ``Papa``): a span made only of these is no one's name.
# ``Ma`` is also a surname, so a span after an honorific (``Dr Ma``) is kept.
_KIN_WORDS = {"ah", "ma", "mah", "pa", "mama", "papa", "mum", "mummy", "mom", "dad", "daddy",
              "gong", "kong", "po", "popo", "grandma", "grandpa", "granny", "nenek", "atuk",
              "mak", "cik", "pak", "makcik", "pakcik",
              # Tamil, Hindi and Tagalog (``Amma``, ``Appa``, ``Lola``; notes_v12)
              "amma", "appa", "thatha", "paati", "patti", "akka", "nani", "dadi", "lola", "lolo",
              "nanay", "tatay", "inay", "itay", "kuya"}
# Function words of Tagalog, Malay and Indonesian, as written in lower case. An engine's "name"
# holding one is a phrase of the note (``siya pero``, ``minta nomor``, ``Tumawag na po ako sa``;
# notes_v12). ``Ng`` and ``Ang`` are surnames: only the lower-case words count.
_FOREIGN_FUNCTION = {"po", "sa", "ng", "na", "ako", "siya", "pero", "mga", "ay", "ko", "niya",
                     "lang", "din", "rin", "naman", "kasi", "yung", "ang", "ito", "minta", "nomor",
                     "dan", "yang", "di", "ke", "untuk", "dengan", "sudah", "saya", "dia", "tidak",
                     "ada", "pada", "itu", "ini", "akan", "dari", "atau", "juga", "boleh", "tak",
                     "nak", "lah", "kami", "kita", "mau", "belum", "lagi"}

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
    social work worker msw family meeting station counter desk
    pte ltd llp inc co corp company construction services trading enterprise
    dear sir sirs colleague colleagues all my our your re attn subject regarding complaint
    triage rn en ssn apn clinician executive manager officer senior junior principal chief
    head assistant associate physiotherapist physio therapist dietitian pharmacist accounts
    safety operations admin administrator coordinator secretary clerk
    fdw mom ica cpf moh hdb lta spf scdf wica medisave medishield grant scheme
    birth cert certificate record records cardiac implant gen med
    participant participants anonymous
    coroner victim client carer sitter endoscopist physician scientist geneticist supervisor
    clinical night mbbs frcpa mrcp frcs mmed phd adm resus recheck cbg care nationality
    occupation hi hello hey morning afternoon evening good
    anak perempuan lelaki cucu isteri istri suami ibu bapa ayah abang kakak adik menantu sepupu
    husb hubby bro sis dtr
    tarikh tanggal nama alamat umur jantina lahir telefon nombor nomor waris penjaga jururawat
    perawat doktor dokter pesakit pasien majikan agensi agency paspor pasport penerjemah
    penterjemah staf kontrol ulang lawatan seterusnya temujanji rujukan catatan tandatangan
    klinik ubat obat bidan keluarga pekerja pangalan petsa tirahan edad nars pasyente tumawag
    obituary cortege funeral wake crematorium columbarium beloved late condolences medifund
    paramedic
    beliau saya kepada encik puan tuan proband grandson granddaughter grandchild nephew niece
    july timestamp user role action
    """
_STOP = {w.lower() for w in _STOP_WORDS.split()}
_FUNCTION = {"the", "of", "and", "or", "for", "to", "in", "on", "at", "by", "with", "from", "my",
             "our", "your", "his", "her", "its", "re"}


def _is_stop(word: str) -> bool:
    w = word.lower().strip(".,")
    return (w[:-2] if w.endswith("'s") else w) in _STOP


# --- addresses -----------------------------------------------------------------------------

_TITLE = r"[A-Z][A-Za-z'&\-]+"
_UNIT_RX = r"#\s?\d{1,3}-\d{1,5}[A-Z]?"
_BUILDING = rf"{_TITLE}(?: {_TITLE}){{0,3}}"
# a building name after the unit, never the city
_BUILDING_AFTER = rf"(?: (?!Singapore\b){_TITLE}(?: (?!Singapore\b){_TITLE}){{0,3}})?"
# street types that Singapore numbers (``Ang Mo Kio Ave 3``, ``Jurong West St 42``)
_NUMBERED_STREET = r"Avenue|Ave|Street|St|Road|Rd|Drive|Crescent|Cres|Central|Ring|Link|Way"
# Tampines St 11, Bedok North Avenue 2, Woodlands Ave 6: a numbered street with no block (P7)
_NUMBERED_STREET_RX = re.compile(rf"(?<![\w-])(?<!\d )(?<!\d[A-Z] )(?:{_TITLE} ){{1,3}}"
                                 rf"(?:{_NUMBERED_STREET})\.? \d{{1,3}}[A-Z]?"
                                 rf"(?![\w-])(?:,? {_UNIT_RX}{_BUILDING_AFTER})?")
_ADDRESS_RX = [
    # [Building, ][Blk ]123[A] Word Word Street-type [12][,] [#01-23 [Building]]
    re.compile(rf"(?:{_BUILDING}, )?(?:(?:Blk|Block) )?\d{{1,4}}[A-Z]? "
               rf"(?:{_TITLE} ){{0,4}}(?:{_STREET_TYPES})\b\.?(?: \d{{1,3}}[A-Z]?)?"
               rf"(?:,? {_UNIT_RX}{_BUILDING_AFTER})?"),
    # [Building, ][Blk ][12 ]Jalan Word [Word][, #01-23]; Lorong 3 Geylang
    re.compile(rf"(?:{_BUILDING}, )?(?:(?:Blk|Block) )?(?:\d{{1,4}}[A-Z]? )?"
               rf"(?:Jalan|Jln|Lorong|Lor)(?: \d{{1,3}})?"
               rf"(?: {_TITLE}){{1,3}}(?:,? {_UNIT_RX}{_BUILDING_AFTER})?"),
    # [Dormitory, ]Blk B[,] [Rm 07-12, ]12 Sungei Kadut Ave: a lettered block and a room before
    # the street number (dormitories, hostels)
    re.compile(rf"(?:{_BUILDING}, )?(?:Blk|Block) [A-Z0-9]{{1,4}},? "
               rf"(?:(?:Rm|Room|Unit|Lvl|Level|#)\.? ?\d{{1,3}}(?:-\d{{1,5}})?[A-Z]?, )?"
               rf"\d{{1,4}}[A-Z]? (?:{_TITLE} ){{0,4}}(?:{_STREET_TYPES})\b\.?"
               rf"(?: \d{{1,3}}[A-Z]?)?"),
    # [Blk ]123[A] Word Word[,] #01-23: street types are an open class, the unit anchors it
    re.compile(rf"(?:(?:Blk|Block) )?\d{{1,4}}[A-Z]?(?: {_TITLE}){{1,4}},? {_UNIT_RX}"
               rf"{_BUILDING_AFTER}"),
    _NUMBERED_STREET_RX,
]

# --- sensitive health information ---------------------------------------------------------

# General clinical vocabulary, grouped by the provisional SHI labels. Matching is case-insensitive
# on word boundaries; each hit takes leading qualifiers and following head nouns with it.
_SHI_TERMS = {
    "hiv_sti": ["hiv-1", "hiv-2", "hiv", "aids", "human immunodeficiency virus", "syphilis",
                "gonorrhoea", "gonorrhea", "chlamydia", "genital herpes", "herpes simplex",
                "genital warts", "trichomonas", "trichomoniasis", "hepatitis b", "hepatitis c",
                "hpv", "sexually transmitted", "std", "sti", "prep", "antiretroviral",
                # syphilis serology (notes_v14)
                "vdrl", "rpr", "tpha"],
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
                      "benzodiazepine dependence", "iv drug", "binge drinking",
                      "heavy drinking", "cans of beer", "units of alcohol", "alcohol intake"],
    "genetic": ["brca1", "brca2", "brca", "lynch syndrome", "huntington", "cystic fibrosis",
                "thalassaemia trait", "thalassemia trait", "genetic testing", "gene positive",
                "hypercholesterolaemia", "hypercholesterolemia", "carrier status",
                "mutation carrier", "down syndrome", "trisomy", "g6pd deficiency", "genetic"],
    "reproductive_sexual": ["termination of pregnancy", "abortion", "miscarriage", "ivf",
                            "in vitro fertilisation", "in vitro fertilization", "infertility",
                            "erectile dysfunction", "contraception", "gender-affirming",
                            "gender affirming", "gender dysphoria", "transgender",
                            "sexual orientation", "ectopic pregnancy", "caesarean section",
                            "cesarean section"],
    "other_sensitive": ["sexual assault", "rape", "domestic violence", "family violence",
                        "elder abuse", "child abuse", "child protection", "abuse", "neglect",
                        "intimate partner violence", "inflicted by her partner",
                        "inflicted by his partner",
                        "incarceration", "prison", "criminal record"],
}
_SHI_LEADS = (r"previous|past|known|recurrent|chronic|active|suspected|confirmed|"
              r"genetically\s+confirmed|generali[sz]ed|severe|major|familial")
_SHI_HEADS = (r"infection|status|disorder|disease|use|dependence|abuse|carrier|attempt|"
              r"syndrome|positive|negative|reactive|treatment|therapy|workup|work-up|concerns?|"
              r"referral|maintenance|mutation|trait|history|ideation|episode|gene|hormones?|"
              r"hormonal|medication|clinic|counsell?ing")
_SHI_RX = [
    (label, re.compile(
        rf"(?<![\w-])(?:(?:{_SHI_LEADS})\s+)*"
        rf"(?:{'|'.join(re.escape(t) for t in sorted(terms, key=len, reverse=True))})"
        rf"(?:[\s-]+(?:{_SHI_HEADS}))*(?![\w-])", re.IGNORECASE))
    for label, terms in _SHI_TERMS.items()
] + [
    # a disclosed assault by a relative or partner: ``husband hit her``, ``son kicked him``
    ("other_sensitive", re.compile(
        r"\b(?:(?:ex-?)?(?:husband|wife|partner|boyfriend|girlfriend)|(?:step)?(?:father|mother)"
        r"|son|daughter|brother|sister|(?:son|daughter)-in-law|employer)[ \t]+"
        r"(?:hit|hits|slapped|slaps|punched|punches|kicked|kicks|beat|beats|choked|chokes"
        r"|strangled|shoved|pushed)[ \t]+(?:her|him|me|them)\b", re.IGNORECASE)),
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


def _after_cue(text: str, start: int) -> bool:
    return bool(re.search(rf"{_CUE}[ \t]+$", text[max(0, start - 1 - 24):start - 1]))


def _trim_name(text: str, s: int, e: int) -> tuple[int, int] | None:
    """Trim stop words off both ends of the name run ``text[s-1:e]``; None if too little is left
    (one word is kept only after an honorific or a role word)."""
    words = [(m.start() + s, m.end() + s - 1, m.group(0))
             for m in re.finditer(r"\S+", text[s - 1:e])]
    # an organisation's name ends at its type word; only what follows can be a person
    org = [i for i, w in enumerate(words) if w[2].lower().strip(".,") in _ORG_WORDS]
    if org:
        words = words[org[-1] + 1:]
    # an upper-case heading holds a function word inside it (``COMPLAINT REGARDING CARE OF MY``);
    # an upper-case name does not (``YEO JUN JIE``: a month is no function word)
    if (len(words) >= 3 and all(w[2].isupper() for w in words)
            and any(w[2].lower() in _FUNCTION for w in words[1:-1])):
        return None

    def drop(w: str) -> bool:
        # a capitalised connector at an end is a name word (``Jia Le``, ``Tan Bin``)
        return _is_stop(w) or (bool(re.fullmatch(_CONNECTOR, w)) and not w[:1].isupper())

    while words and drop(words[0][2]):
        words.pop(0)
    while words and drop(words[-1][2]):
        words.pop()
    capital = [w for w in words if not re.fullmatch(_CONNECTOR, w[2])]
    if not capital or all(_is_stop(w[2]) for w in capital):
        return None
    if len(capital) < 2 and not _after_cue(text, words[0][0]):
        return None
    return words[0][0], words[-1][1]


def _trim_engine_name(text: str, s: int, e: int) -> tuple[int, int] | None:
    """An engine's name span ``text[s-1:e]``, or what follows the organisation or heading in it
    (less stop words in front); None if only stop words are left. Otherwise the span is kept
    as the engine gave it, one word too: the engine found it."""
    words = [(m.start() + s, m.end() + s - 1, m.group(0))
             for m in re.finditer(r"[^\W_]+(?:['’\-.][^\W_]+)*", text[s - 1:e])]
    if any(w[2] in _FOREIGN_FUNCTION for w in words) and not _after_cue(text, s):
        return None
    # ... and two words or more starting with one are a phrase even after a cue (``Doktor yang
    # merawat``, ``Ayah saya tinggal``; notes_v14); ``Mr ng`` alone is a surname
    if len(words) > 1 and words[0][2] in _FOREIGN_FUNCTION:
        return None
    # a name written only in Tamil, Devanagari, Bengali, Thai or Myanmar script is proposed by the
    # script shapes above; an engine's is a word or a piece of one (``என்``, ``மக``; notes_v13-v14)
    if not re.search(r"[A-Za-z]", text[s - 1:e]) and re.search(_SCRIPT, text[s - 1:e]):
        return None
    org = [i for i, w in enumerate(words) if w[2].lower() in _ORG_WORDS]
    if org:
        words = words[org[-1] + 1:]
        while words and _is_stop(words[0][2]):
            words.pop(0)
    if all(_is_stop(w[2]) for w in words):
        return None
    return (words[0][0], words[-1][1]) if org else (s, e)


def _engine_name(text: str, s: int, e: int) -> tuple[int, int] | None:
    """An engine's name span, trimmed as by :func:`_trim_engine_name`. A name never runs across a
    line break, and an engine span that does has taken the next line's first word (``Karen
    Ong\\nFamily``, ``on 2 L.\\nSon asked``): only the first line's piece is kept, and not if it
    is a fragment or has no letter."""
    if "\n" not in text[s - 1:e]:
        return _trim_engine_name(text, s, e)
    m = re.search(r"[^\n]*\S", text[s - 1:e])
    ps, pe = s + m.start(), s + m.end() - 1
    piece = text[ps - 1:pe]
    if _FRAGMENT.fullmatch(piece) or not re.search(r"[^\W\d_]", piece):
        return None
    return _trim_engine_name(text, ps, pe)


_NEXT_WORDS = re.compile(rf"(?:[ \t]+(?:{_WORD}|&))+")


def _starts_org(text: str, e: int) -> bool:
    """Whether the word ending at ``e`` starts an organisation's name: capitalised words follow
    it on the same line up to an organisation or heading word (``Yours faithfully,\\nKallang
    Bridge Law LLC``)."""
    m = _NEXT_WORDS.match(text, e)
    return bool(m) and any(w.lower().strip(".,") in _ORG_WORDS for w in m.group().split())


def _initials_of(name: str, ini: str) -> bool:
    """Whether ``ini`` are initials of words of ``name``, in order (``CY`` of ``Clara Yeo
    Li-Ann``, ``BH`` of ``TAN Bee Hwa``)."""
    heads = iter(w[0].upper() for w in re.findall(r"[^\W\d_]+", name))
    return all(c in heads for c in ini)


def _kin_only(text: str, p: Proposal) -> bool:
    """Whether ``p`` holds only family terms of address and stop words (``Ah Ma``, ``Papa``,
    ``Ah Ma IC``), with no honorific or role word before it (``Dr Ma``, ``SN Ma``) and no name
    after it (``Ma. Lourdes``)."""
    span = re.sub(r"['’]s\b", "", text[p.start - 1:p.end])  # ``Mak Cik's``
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", span)]
    return (bool(words) and all(w in _KIN_WORDS or w in _STOP for w in words)
            and any(w in _KIN_WORDS - {"ah"} for w in words)
            and not _after_cue(text, p.start)
            and not (words == ["ma"] and re.match(r"\.[ \t]+[A-Z][a-z]", text[p.end:])))


def _field_ids(text: str) -> Iterable[tuple[int, int]]:
    """1-based spans of ID field values: the tokens after the label, joined by single spaces,
    while each has a digit or is a short upper-case code; at least 3 digits in all."""
    for m in _ID_FIELD.finditer(text):
        pos, end = m.end(), None
        while (t := _FIELD_TOKEN.match(text, pos)) and (
                any(c.isdigit() for c in t.group()) or _FIELD_CODE.fullmatch(t.group())):
            end = t.end()
            if not (text[end:end + 1] == " " and text[end + 1:end + 2].isalnum()):
                break
            pos = end + 1
        # a dotted value needs more digits: codes such as ``E16.2`` (ICD-10) are no identifiers
        value = text[m.end():end] if end else ""
        if end and sum(c.isdigit() for c in value) >= (5 if "." in value else 3):
            yield m.end() + 1, end


# A table column whose header names people (``PatientName``, ``user_name``, ``Staff``), and a
# cell under it written like a name: letters with spaces, dots, commas, hyphens, slashes or
# underscores (``KOH, SOOK LING``, ``RAJENDRAN S/O MUNUSAMY``, ``ONG_JIAHUI``; notes_v10).
_NAME_HEADER_WORDS = {"name", "names", "patient", "pt", "staff", "user", "username", "clinician",
                      "doctor", "dr", "nurse", "author", "caller", "nok", "carer", "caregiver",
                      "informant", "witness", "officer", "member", "employee", "worker",
                      "resident", "client", "attendee", "participant", "by",
                      # who signed a row (``| Sign |`` over ``SLK``, notes_v12) or did the work
                      # (theatre lists)
                      "sign", "signature", "sig", "initials", "initial", "checked", "verified",
                      "dispensed", "scribe", "rn", "surgeon", "anaesthetist", "anesthetist",
                      "scrub", "circulating", "assistant", "pharmacist", "therapist",
                      "midwife", "vaccinator", "consultant", "registrar", "parent", "guardian",
                      "mother", "father", "spouse", "kin"}
_NAME_CELL = re.compile(r"[A-Za-z][A-Za-z .,'’_/-]{0,58}[A-Za-z.]")


def _name_cells(text: str) -> Iterable[tuple[int, int]]:
    """1-based spans of the name-like cells of name columns in tables pasted into the text."""
    for m in re.finditer(r"[^\n]+", text):
        line, base = m.group(), m.start()
        for delim in _TABLE_DELIMS:
            cells = _cells(line, delim)
            if len(cells) < 3:
                continue
            for a, b in cells:
                raw = line[a:b]
                val = raw.strip().strip('"').strip()
                if not val or not _NAME_CELL.fullmatch(val):
                    continue
                if all(_is_stop(w) for w in re.split(r"[ _,./-]+", val) if w):
                    continue
                s = base + a + raw.index(val) + 1
                e = s + len(val) - 1
                head = table_column(text, s, e)
                words = {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", head or "")}
                if words & _NAME_HEADER_WORDS:
                    yield s, e
            break  # the first delimiter that splits the line is the table's, as in table_column


def _fixed_name_cells(text: str) -> Iterable[tuple[int, int]]:
    """1-based spans of name-like cells under a name column of a fixed-width table (cells two or
    more spaces apart), with a header in upper or title case (``4   Thant   (ID not given)``
    under ``No  Name  ID``; notes_v15)."""
    for m in re.finditer(r"[^\n]+", text):
        line, base = m.group(), m.start()
        if "\t" in line or "|" in line:
            continue
        cells = [(c.start(), c.group()) for c in re.finditer(r"\S+(?: \S+)*", line)]
        if len(cells) < 3:
            continue
        for a, val in cells:
            if not _NAME_CELL.fullmatch(val) or all(
                    _is_stop(w) for w in re.split(r"[ _,./-]+", val) if w):
                continue
            s = base + a + 1
            head = fixed_width_column(text, s, head=_FIXED_TITLE_HEAD)
            words = {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", head or "")}
            if words & _NAME_HEADER_WORDS:
                yield s, s + len(val) - 1


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
            if 8 <= sum(c.isdigit() for c in text[s - 1:e]) <= 15:
                add(s, e, "shape:phone", "phone")
                if x := _PHONE_EXT.match(text, e):
                    add(s, x.end(), "shape:phone", "phone")
    for s, e in _spans(_PHONE_SHORT, text):
        add(s, e, "shape:phone", "phone")
    for s, e in _spans(_IDLIKE, text):
        if sum(c.isdigit() for c in text[s - 1:e]) >= 5:
            add(s, e, "shape:id")
    for s, e in _spans(_SLASH_ID, text):
        tok = text[s - 1:e]
        if sum(c.isdigit() for c in tok) >= 5 and not _SLASH_DATE.fullmatch(tok):
            add(s, e, "shape:id")
    for s, e in _spans(_PLATE, text):
        if len(text[s - 1:e].replace(" ", "")) >= 5:
            add(s, e, "shape:plate")
    for s, e in _spans(_IMAGE_FILE, text):
        add(s, e, "shape:file", "photo")
    for s, e in _spans(_OCR_DATE, text):
        if ocr_date(text[s - 1:e]):
            add(s, e, "shape:ocr_date", "date")
    for s, e in _spans(_SPOKEN_DIGITS, text):  # dictated numbers (``nine one seven ...``)
        if len(spoken_digits(text[s - 1:e]).lstrip("+")) >= 7:
            add(s, e, "shape:spoken_number", "phone")
    for s, e in _spans(_SPOKEN_REF, text):  # ``two six slash four four ...``
        if len(spoken_digits(text[s - 1:e])) >= 5:
            add(s, e, "shape:spoken_number")
    for s, e in _spans(_SPOKEN_NRIC, text):  # ``S six seven three ... D``
        add(s, e, "shape:spoken_number", "national_id")
    for s, e in _spans(_DEATH_DATE, text):
        if _DEATH_LEAD.search(text[max(0, s - 1 - 32):s - 1]):
            add(s, e, "shape:date", "date")
    for s, e in _spans(_LOGIN_CELL, text, group=1):
        if _LOGIN_COLUMN.search(fixed_width_column(text, s) or ""):
            add(s, e, "shape:handle")
    for s, e in _spans(_HANDLE, text, group=1):
        add(s, e, "shape:handle")
    for s, e in _spans(_URL_PATH, text):
        add(s, e, "shape:url")
    for s, e in _spans(_TOKEN, text):
        tok = text[s - 1:e]
        digits = sum(c.isdigit() for c in tok)
        if (s, e) in found or digits < 4:
            continue
        # a reference a rule is sure of; or letter and digit segments joined by hyphens
        # (``OPD-PT-26-33091``, ``BIO-26-00918-A``): the whole code, where ``_IDLIKE`` finds
        # only its digit tail
        # (``BIO-26-00918-A``); an upper-case prefix takes shorter codes (``FGS-0142``). Three
        # digits (``HF-017``) were tried on notes_v1-v12: 25 decoys (``VP-300``, ``IPC-WI-014``)
        # for 6 subject codes.
        if rule_certain(text, Candidate(s, e)) or ("-" in tok and re.search(r"[A-Za-z]", tok) and (
                digits >= 5 or (digits >= 3 and re.match(r"[A-Z]{2,5}-", tok)))):
            add(s, e, "shape:id")
    for s, e in _spans(_NUM_YEAR, text):
        add(s, e, "shape:id")
    for s, e in _spans(_DOTTED_ID, text):
        if sum(c.isdigit() for c in text[s - 1:e]) >= 5:
            add(s, e, "shape:id")
    for s, e in _spans(_DONATION_NO, text):
        add(s, e, "shape:id")
    for s, e in _field_ids(text):
        add(s, e, "shape:id")
    for s, e in _spans(_AT_HANDLE, text):
        add(s, e, "shape:handle")
    for s, e in _spans(_INITIALS, text, group=1):
        add(s, e, "shape:name", "name")
    for s, e in _spans(_INITIALS_LINE, text, group=1):
        add(s, e, "shape:name", "name")
    for m in _INITIALS_CUE.finditer(text):
        for part in re.finditer(r"[A-Z]+", m.group(1)):
            if not _is_stop(part.group()) and part.group() != "OK":
                s = m.start(1) + part.start() + 1
                add(s, s + len(part.group()) - 1, "shape:initials", "name")
    speakers = [(m.group(1), m.start(1)) for m in _SPEAKER.finditer(text)
                if not _is_stop(m.group(1)) and not re.fullmatch(_STAFF, m.group(1))]
    for ini, s in speakers:
        if sum(1 for i, _ in speakers if i == ini) >= 2:
            add(s + 1, s + len(ini), "shape:initials", "name")
    for s, e in _spans(_KV_NAME, text, group=1):
        if not all(_is_stop(w) for w in text[s - 1:e].split("_")):
            add(s, e, "shape:name", "name")
    for s, e in _spans(_KV_LOGIN, text, group=1):
        add(s, e, "shape:handle")
    for s, e in _spans(_MASKED_NRIC, text):
        if e - s + 1 == 9:
            add(s, e, "shape:id", "national_id")
    for s, e in _spans(_NRIC_TAIL, text, group=1):
        add(s, e, "shape:id", "national_id")
    for s, e in _spans(_NAME_RUN, text):
        if (t := _trim_name(text, s, e)) is not None:
            add(*t, "shape:name", "name")
    for s, e in _spans(_PARTICLE_NAME, text):
        add(s, e, "shape:name", "name")
    for s, e in _spans(_AFTER_HONORIFIC, text, group=1):
        if not _is_stop(text[s - 1:e]):
            add(s, e, "shape:name", "name")
    for s, e in _spans(_INVERTED, text, group=1):
        if (t := _trim_name(text, s, e)) is not None:
            add(*t, "shape:name", "name")
    runs = [(p.start, p.end) for p in found.values() if "shape:name" in p.sources]
    for rx in (_HAN_NAME, _HAN_AFTER_ROMAN, _HAN_AFTER_COLON, _HAN_AFTER_ROLE,
               _SCRIPT_GLOSSED, _SCRIPT_AFTER_ROLE, _SCRIPT_BRACKETED):
        for s, e in _spans(rx, text, group=1):
            add(s, e, "shape:name", "name")
    for s, e in _spans(_SCRIPT_GLOSSED, text, group=2):  # the gloss: ``(Revathi)``
        t = _trim_name(text, s, e) if " " in text[s - 1:e] else (s, e)
        if t and not _is_stop(text[t[0] - 1:t[1]]):
            add(*t, "shape:name", "name")
    for m in _LOWER_AFTER_HONORIFIC.finditer(text):
        words = list(re.finditer(r"\S+", m.group(1)))
        for w in words:
            if _is_stop(w.group()) or w.group() in _LOWER_NOT_NAME:
                break
            add(m.start(1) + 1, m.start(1) + w.end(), "shape:name", "name")
    for rx in (_AFTER_ROLE, _BEFORE_ROLE, _SIGNOFF, _POSSESSIVE, _QUOTED, _SIGN_DEPT,
               _LATIN_AFTER_SCRIPT_ROLE):
        for s, e in _spans(rx, text, group=1):
            # a cue before an organisation's name cues no person (``Yours faithfully,\nKallang
            # Bridge Law LLC``: the run itself is no name, see ``_trim_name``)
            if _is_stop(text[s - 1:e]) or _starts_org(text, e):
                continue
            # one word of a longer name already proposed would only cost another judgment
            if not any(a <= s and e <= b for a, b in runs):
                add(s, e, "shape:name", "name")
            if rx is _AFTER_ROLE:  # the rest of a list: ``sons Irfan and Hakim``
                while (m := _LIST_NEXT.match(text, e)) and not _is_stop(m.group(1)):
                    s, e = m.start(1) + 1, m.end(1)
                    if not any(a <= s and e <= b for a, b in runs):
                        add(s, e, "shape:name", "name")
    addresses = [(rx, sp) for rx in _ADDRESS_RX for sp in _spans(rx, text)]
    for rx, (s, e) in addresses:
        # a numbered street inside a longer address (``Pasir [Ris Drive 3, #07-403]``) is not its
        # own candidate
        if rx is _NUMBERED_STREET_RX and any(a <= s and e <= b and (a, b) != (s, e)
                                             for _, (a, b) in addresses):
            continue
        add(s, e, "shape:address", "address")
    for s, e in _name_cells(text):
        add(s, e, "shape:name_column", "name")
    # the name-slot sweep: a word or initials in a slot where a person stands, unless it is part
    # of a longer name already proposed
    runs = [(p.start, p.end) for p in found.values() if p.type == "name"]
    for rx in (_STAFF_SLOT, _BEFORE_BRACKET_ID, _NUMBER_INITIALS, _NAME_WRAP, _DASH_SIGNOFF,
               _DOTTED_INITIALS):
        for s, e in _spans(rx, text, group=1):
            tok = text[s - 1:e]
            if (_is_stop(tok) or re.fullmatch(_STAFF, tok) or tok in _DOTTED_NOT_NAME
                    or _starts_org(text, e)
                    or any(a <= s and e <= b and (a, b) != (s, e) for a, b in runs)):
                continue
            add(s, e, "shape:slot", "name")
    for s, e in _fixed_name_cells(text):
        add(s, e, "shape:name_column", "name")
    for s, e in _spans(_STR_PROFILE, text):
        add(s, e, "shape:dna", "biometric")
    for s, e in _spans(_SPACED_NRIC, text):
        if rules.nric_valid(re.sub(r"[ -]", "", text[s - 1:e])):
            add(s, e, "shape:id", "national_id")
    for s, e, ident in hl7_people(text):  # ``PID|...|TAN^MEI LING``: by HL7 field position
        add(s, e, "shape:hl7", ident)
    for label, rx in _SHI_RX:
        for s, e in _spans(rx, text):
            add(s, e, f"lexicon:{label}", label)
    extra = list(extra)
    for c in extra:
        s, e = c.start, c.end
        if ((s, e) not in found and _FRAGMENT.fullmatch(text[s - 1:e])
                and not any(o is not c and o.start <= e + 2 and s <= o.end + 2 for o in extra)):
            # an engine's lone fragment that no rule or shape proposed: 1-3 digits or one letter
            # (``10``, ``041``, ``T``, ``S$``). Short names (``Ros``, ``Ng``) are no fragments,
            # nor is a piece next to another engine span (``Room [04]-[12, S637118]``)
            continue
        words = re.findall(r"[^\W\d_]+", text[s - 1:e])
        if (c.type and words and not re.search(r"\d", text[s - 1:e])
                and all(_is_stop(w) for w in words) and (s, e) not in found):
            # an engine's span of stop words only, of any type (``NRIC`` called an account)
            continue
        if (c.type or "").lower() in ("name", "person"):
            # an engine's "name" gets the same trimming as the name shapes above: what ends at
            # an organisation or heading word (``Eastshore GH Ward``) and stop words at either
            # end (``Adm``, ``Birth cert / NRIC``, ``Morning Jess``) are no part of a name
            if (t := _engine_name(text, s, e)) is None:
                continue
            s, e = t
        add(s, e, c.detector or "extra", c.type)
    # a span made only of family terms of address (``Ah Ma``, ``Papa``) names no one
    names = [p for p in found.values()
             if (p.type or "").lower() in ("name", "person") and not _kin_only(text, p)]
    kin = {(p.start, p.end) for p in found.values()
           if (p.type or "").lower() in ("name", "person") and p not in names}
    # Initials given to a person in brackets after the name (``Dr Clara Yeo [CY]``) name that
    # person wherever else they stand: as a speaker (``CY: ...``), a minute-taker (``Minutes
    # taken by BT``). One letter only as a speaker's label at the start of a line.
    legend = set()
    for m in _LEGEND.finditer(text):
        g = 1 if m.group(1) else 2
        ini = m.group(g)
        if _is_stop(ini) or re.fullmatch(_STAFF, ini):
            continue
        if any(p.end <= m.start() and _LEGEND_GAP.fullmatch(text[p.end:m.start()])
               and _initials_of(text[p.start - 1:p.end], ini) for p in names):
            add(m.start(g) + 1, m.end(g), "shape:initials", "name")
            legend.add(ini)
    for ini in legend:
        rx = rf"(?<![\w/])({ini})(?!\w)" if len(ini) > 1 else rf"(?m)^[ \t]*({ini})[ \t]*:"
        for s, e in _spans(re.compile(rx), text, group=1):
            add(s, e, "shape:initials", "name")
    # A name proposed once is proposed wherever else it is written the same way: the cue or the
    # engine that found it may not be there the next time (a chat speaker's third message,
    # notes_v9). Not inside another name, and each mention is still judged on its own context.
    # Only words written like a name: not all lower case (``po ako``, ``thiazide``) and not a
    # lone term of address (``Ah``). Single parts of a longer name are not repeated: on
    # notes_v1-v11 that added 36 non-identifiers for 1 name.
    for word in {text[p.start - 1:p.end] for p in names}:
        if (_FRAGMENT.fullmatch(word) or all(_is_stop(w) for w in word.split())
                or word == word.lower() != word.upper() or word.lower() in _KIN_WORDS):
            continue
        for m in re.finditer(rf"(?<![\w'’]){re.escape(word)}(?![\w'’])", text):
            s, e = m.start() + 1, m.end()
            if not any(p.start <= s and e <= p.end for p in names):
                add(s, e, "shape:repeat", "name")
    return sorted((p for p in found.values() if (p.start, p.end) not in kin),
                  key=lambda p: (p.start, -p.end))


def contained(inner: Proposal, outer: Proposal) -> bool:
    """Whether ``inner`` lies within ``outer`` (and is not the same interval)."""
    return (outer.start <= inner.start and inner.end <= outer.end
            and (inner.start, inner.end) != (outer.start, outer.end))
