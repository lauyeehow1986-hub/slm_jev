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
    _STREET_TYPES,
    HANDLE,
    MASKED_NRIC,
    NRIC_TAIL,
    SOCIAL_LEAD,
    Candidate,
    rule_certain,
)

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
    # an international number in groups (``+63 917 552 0184``); 8-15 digits, checked in code
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[ -]?\(?\d{1,4}\)?){2,5}(?![\d-])"),
]
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
_IMAGE_FILE = re.compile(r"(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?i:jpe?g|png|gif|bmp|tiff?|heic|webp|"
                         r"dcm|dicom|mp4|mov|avi)(?![\w])")
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
_NUM_YEAR = re.compile(r"(?<![\w/])(?:[A-Z]{1,4}\.? ?)?(?:\d{1,6}/(?:19|20)\d{2}|"
                       r"(?:19|20)\d{2}/\d{3,6})(?![\w/])")
# Initials signing a record (``Signed: L.W.X.``, ``Sgd K.M.T``).
_INITIALS = re.compile(r"(?i:\b(?:signed|sgd|initials?|countersigned)\b(?:[ \t]+by)?[ \t]*:?[ \t]*)"
                       r"((?:[A-Z]\.){1,3}[A-Z]\.?)(?![\w.])")
# A masked NRIC/FIN (``G****262U``).
_MASKED_NRIC = re.compile(rf"(?<![\w*#]){MASKED_NRIC}(?![\w*])")
# An NRIC/FIN tail given on its own (``NRIC ending 412D``), group 1.
_NRIC_TAIL = re.compile(NRIC_TAIL)
# The value after an ID field label, which may hold spaces (``MRN: BTC 22 118 406``,
# ``donation no. W0417 26 118203 X``, ``Policy no.: HS-IP-7739 0021 45``); see ``_field_ids``.
_ID_FIELD = re.compile(
    r"(?i:\b(?:MRN|HRN|NRIC|FIN|IC|passport|case|visit|episode|encounter|admission|account|acct|"
    r"policy|claim|member|employee|staff|donation|accession|specimen|sample|lab|serial|record|"
    r"file|ref|reference|ID)\b(?:[ \t]*(?:no\b\.?|number\b|num\b|#))?)[ \t]*[:#.]?[ \t]*"
    r"(?=[A-Za-z0-9])")
_FIELD_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9/\-]*[A-Za-z0-9])?")
# an @handle that is not part of an e-mail address
_AT_HANDLE = re.compile(r"(?<![\w.@])@[A-Za-z][A-Za-z0-9._]{1,30}[A-Za-z0-9_]")

# --- names ---------------------------------------------------------------------------------

_HONORIFIC = r"(?:Mr|Mrs|Ms|Mdm|Madam|Miss|Dr|Prof|Mstr|Master)\.?"
# Title case (``Tan``, ``D'Cruz``, ``Ah-Kow``, ``E-Lynn``), upper case (``TAN``, ``D'CRUZ``),
# initials (``G.C.``) or the ``Md.`` / ``MD.`` short form of Mohammad
_WORD = (r"(?:[A-Z]'?[A-Z]?[a-z]+(?:['\-][A-Z]?[a-z]+)*|[A-Z]-[A-Z](?:[a-z]+|[A-Z]+)"
         r"|[A-Z]'?[A-Z]+(?:['\-][A-Z]+)*|[A-Z]\.(?:[A-Z]\.)+|M[Dd]\.)")
_PARTICLE = r"(?:van|von|de|da|dos|del|la|le)"
_CONNECTOR = rf"(?i:bin|binti|binte|bte|b\.|d/o|s/o|a/l|a/p|@|{_PARTICLE})"
# The words of a name are joined by spaces or tabs, never a line break: a run across lines joins
# a heading to the next line (``LABORATORY REPORT\nPatient Name``, P7).
_NAME_RUN = re.compile(rf"(?<![\w']){_WORD}(?:(?:[ \t]+{_CONNECTOR})?[ \t]+{_WORD}){{1,5}}"
                       rf"(?![\w'])")
# ``de Souza``: a lower-case surname particle and one capitalised word
_PARTICLE_NAME = re.compile(rf"(?<![\w']){_PARTICLE}[ \t]+{_WORD}(?![\w'])")
# Role and relation words that are usually followed (``Nurse Lim``, ``SON VIJAY``,
# ``Caller: Aisyah``) or tagged (``Aisyah (daughter)``) by one person's name.
_ROLES = (r"nurse|sn|sons?|daughters?|wife|husband|mother|father|brothers?|sisters?|spouse|"
          r"partner|children|child|grandchildren|siblings?|parents?|relatives?|"
          r"caller|carer|caregiver|helper|maid|guardian|nok|informant|friend|neighbou?r|"
          r"uncle|aunt|aunty|auntie|grandmother|grandfather|grandson|granddaughter|niece|"
          r"nephew|cousin|colleague|supervisor|physio|physiotherapist|therapist|"
          r"endoscopist|surgeon|anaesthetist|anesthetist|pharmacist|dietitian|counsell?or|"
          r"interpreter|translator|witness|visitors?|fdw|girlfriend|boyfriend|fianc[eé]e?|"
          r"driver|paramedic|proband|named|called|known[ \t]+as|baby[ \t]+of|family[ \t]+of|"
          r"witnessed(?:[ \t]+by)?")
# Staff and form-field abbreviations that take a colon or hyphen before one name (``PT:
# Rajeswari``, ``DSA: Salina``, ``Bed 2 - RAJOO``); case-sensitive, so ``pt`` in prose is no cue.
_STAFF = r"PT|OT|ST|DSA|RN|SSN|SRN|EN|MO|HO|MSW|SW|APN|NC|CM"
_FIELD = (rf"(?:\b(?:{_STAFF}|Pt|Patient|Client|Name|Attn|Re)|\bBed[ \t]*\d{{1,3}}[A-Z]?)"
          r"[ \t]*[:\-]")
# A single word is proposed only after one of these cues: an honorific, a role word, a field
# label or a salutation.
_CUE = rf"(?:\b{_HONORIFIC}|(?i:\b(?:{_ROLES})\b)[:\-]?|{_FIELD}|\bDear)"
# ``Dr Tan``, and with initials first: ``Dr R. Balakrishnan``, ``Mr K.M. Wong``
_AFTER_HONORIFIC = re.compile(rf"\b{_HONORIFIC}\s+((?:[A-Z]\.[ ]?){{0,3}}{_WORD})(?![\w'])")
# ``Nurse Lim``, ``NOK: SON VIJAY``, ``Wife (Rosnah)``, ``PT: Rajeswari``, ``Dear Siti``
_AFTER_ROLE = re.compile(rf"(?:(?i:\b(?:{_ROLES})\b)(?:[:\-]?[ \t]+|[ \t]*\([ \t]*)"
                         rf"|{_FIELD}[ \t]*|\bDear[ \t]+)({_WORD})(?![\w'])")
# ``Aisyah (daughter)``, ``Balan (MSW)``
_BEFORE_ROLE = re.compile(rf"(?<![\w'])({_WORD})[ \t]*\((?:(?i:{_ROLES})|{_STAFF})\)")
# a name signing off a letter, e-mail or message (``Thanks, Farhan``, ``Regards,\nMei Ling``)
_SIGNOFF = re.compile(r"\b(?:Thanks|Thank you|Many thanks|Regards|Best regards|Kind regards|"
                      r"Warm regards|Best wishes|Cheers|Sincerely|Yours sincerely|"
                      rf"Yours faithfully)[,.!]?[ \t]*\n?[ \t]*({_WORD})(?![\w'])")
# ``Siva's wife``: a possessive before a relation word
_POSSESSIVE = re.compile(rf"(?<![\w'])({_WORD})['’]s[ \t]+(?i:{_ROLES})\b")
# a name or nickname in quotes (``known as "Ah Boy"``, ``Supachai ("Jay")``)
_QUOTED = re.compile(rf"(?<!\w)[\"“]({_WORD}(?:[ \t]+{_WORD}){{0,2}})[\"”](?!\w)")
# initials alone on a line signing a message (``> AP``, ``-- KL``)
_INITIALS_LINE = re.compile(r"^[ \t]*(?:>+|-{1,2})?[ \t]*([A-Z]{2,3})[ \t]*$", re.MULTILINE)
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
    """
_ORG_WORDS = set(_ORG.split())

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
                "hpv", "sexually transmitted", "std", "sti", "prep", "antiretroviral"],
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


def _field_ids(text: str) -> Iterable[tuple[int, int]]:
    """1-based spans of ID field values: the tokens after the label, joined by single spaces,
    while each has a digit or is a short upper-case code; at least 3 digits in all."""
    for m in _ID_FIELD.finditer(text):
        pos, end = m.end(), None
        while (t := _FIELD_TOKEN.match(text, pos)) and (
                any(c.isdigit() for c in t.group()) or re.fullmatch(r"[A-Z]{1,4}", t.group())):
            end = t.end()
            if not (text[end:end + 1] == " " and text[end + 1:end + 2].isalnum()):
                break
            pos = end + 1
        if end and sum(c.isdigit() for c in text[m.end():end]) >= 3:
            yield m.end() + 1, end


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
        # (``BIO-26-00918-A``); an upper-case prefix takes shorter codes (``FGS-0142``)
        if rule_certain(text, Candidate(s, e)) or ("-" in tok and re.search(r"[A-Za-z]", tok) and (
                digits >= 5 or (digits >= 3 and re.match(r"[A-Z]{2,5}-", tok)))):
            add(s, e, "shape:id")
    for s, e in _spans(_NUM_YEAR, text):
        add(s, e, "shape:id")
    for s, e in _field_ids(text):
        add(s, e, "shape:id")
    for s, e in _spans(_AT_HANDLE, text):
        add(s, e, "shape:handle")
    for s, e in _spans(_INITIALS, text, group=1):
        add(s, e, "shape:name", "name")
    for s, e in _spans(_INITIALS_LINE, text, group=1):
        add(s, e, "shape:name", "name")
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
    for rx in (_HAN_NAME, _HAN_AFTER_ROMAN):
        for s, e in _spans(rx, text, group=1):
            add(s, e, "shape:name", "name")
    for rx in (_AFTER_ROLE, _BEFORE_ROLE, _SIGNOFF, _POSSESSIVE, _QUOTED):
        for s, e in _spans(rx, text, group=1):
            if _is_stop(text[s - 1:e]):
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
    for label, rx in _SHI_RX:
        for s, e in _spans(rx, text):
            add(s, e, f"lexicon:{label}", label)
    for c in extra:
        # an engine's one-word "name" that is a stop word (``Nurse``, ``Ward``, ``ED``) is the
        # same non-name the name shapes above skip
        if (c.type or "").lower() in ("name", "person") and _is_stop(text[c.start - 1:c.end]
                                                                     .strip()):
            continue
        add(c.start, c.end, c.detector or "extra", c.type)
    return sorted(found.values(), key=lambda p: (p.start, -p.end))


def contained(inner: Proposal, outer: Proposal) -> bool:
    """Whether ``inner`` lies within ``outer`` (and is not the same interval)."""
    return (outer.start <= inner.start and inner.end <= outer.end
            and (inner.start, inner.end) != (outer.start, outer.end))
