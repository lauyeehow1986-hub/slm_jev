import pytest

from slmjev import propose
from slmjev.judge import Candidate


def spans(text, source=None):
    """The proposed strings (optionally only those from a source prefix)."""
    return [text[p.start - 1:p.end] for p in propose.propose(text)
            if source is None or any(s.startswith(source) for s in p.sources)]


@pytest.mark.parametrize("text, want", [
    ("Born 31.08.1959. Seen today.", "31.08.1959"),
    ("DOB 4 Oct 1962, male", "4 Oct 1962"),
    ("admitted 28-Oct-1947 for review", "28-Oct-1947"),
    ("Admitted on 20210804 under case", "20210804"),
    ("on 2024-03-16 at noon", "2024-03-16"),
    ("seen on March 3, 2021 in clinic", "March 3, 2021"),
    ("FIN M1234567K, DOB 1998年11月2日. Verified", "1998年11月2日"),
])
def test_dates(text, want):
    assert want in spans(text, "shape:date")


def test_date_lookahead_rejects_longer_numbers():
    assert "12.05.2020" not in spans("version 12.05.2020.3 installed", "shape:date")


@pytest.mark.parametrize("text, want, source", [
    ("call +65 9123 4567 today", "+65 9123 4567", "shape:phone"),
    ("tel 6123-4567.", "6123-4567", "shape:phone"),
    ("device SN-518862 implanted", "SN-518862", "shape:id"),
    ("ref 85-2466-43 filed", "85-2466-43", "shape:id"),
    ("drives FWL4331H daily", "FWL4331H", "shape:plate"),
    ("motorcycle FBL 6632 E thrown", "FBL 6632 E", "shape:plate"),
    ("via WhatsApp +63 917 552 0184.", "+63 917 552 0184", "shape:phone"),
    ("office 6225 1180 ext 312, email", "6225 1180 ext 312", "shape:phone"),
    ("case ref MOM/FDW/2026/33719.", "MOM/FDW/2026/33719", "shape:id"),
    ("(report no. G/20260923/4471)", "G/20260923/4471", "shape:id"),
    ("saved as IMG_20260923_1542.jpg.", "IMG_20260923_1542.jpg", "shape:file"),
    ("WeChat ID linzq_1992sg.", "linzq_1992sg", "shape:handle"),
    ("page (social.example.com/hafiz.jamal.1993) had", "social.example.com/hafiz.jamal.1993",
     "shape:url"),
    ("(TCM Reg. No. TCM-P1033)", "TCM-P1033", "shape:id"),
    # letter segments joined by hyphens, a number with a year, a masked FIN (notes_v3 misses)
    ("Our ref XY-RFL-26-118830; seen", "XY-RFL-26-118830", "shape:id"),
    ("aliquots BIO-26-00918-A to BIO-26-00918-D.", "BIO-26-00918-D", "shape:id"),
    ("Coroner's case: CC 1187/2026 re", "CC 1187/2026", "shape:id"),
    ("masked FIN G****262U. Pt", "G****262U", "shape:id"),
    # notes_v4 misses: the year first, a short code with an upper-case prefix
    ("Coroner's case: CC 2026/1187 re", "CC 2026/1187", "shape:id"),
    ("study ID: FGS-0142\nName", "FGS-0142", "shape:id"),
    ("(template ref FPT-3391-B).", "FPT-3391-B", "shape:id"),
    # P12: the value of an ID field, spaces and all; an @handle
    ("MRN: BTC 22 118 406      Episode: E1", "BTC 22 118 406", "shape:id"),
    ("Unit 1: donation no. W0417 26 118203 X (O neg)", "W0417 26 118203 X", "shape:id"),
    ("Policy no.: HS-IP-7739 0021 45 / 18 Sep 2026", "HS-IP-7739 0021 45", "shape:id"),
    ("from handle @darren.s_kx, reported", "@darren.s_kx", "shape:handle"),
    # notes_v6 miss: an NRIC tail given on its own
    ("verified with DOB 5/1/93 and NRIC ending 412D.", "412D", "shape:id"),
    ("FIN ends with 088K; ok", "088K", "shape:id"),
    # notes_v8 misses: a dotted accession no., blood donation nos. in a list, a two-part
    # prefix before a number with a year, a local number a digit short
    ("MRI SPINE   Accession: MR.25.1103.00417\nPt:", "MR.25.1103.00417", "shape:id"),
    ("seen, ref XR.24.0301.11873 filed", "XR.24.0301.11873", "shape:id"),
    ("donation nos. W0512 25 441203 A and W0512 25 441219 C. Lab", "W0512 25 441219 C",
     "shape:id"),
    ("Unit: W0512 25 441203, RBC", "W0512 25 441203", "shape:id"),
    ("for case no. FC/OSF 2231/2025.\nPlease", "FC/OSF 2231/2025", "shape:id"),
    ("FAILED (7 digits): 8123 456 for Mdm Lee", "8123 456", "shape:phone"),
])
def test_numbers_ids_and_plates(text, want, source):
    assert want in spans(text, source)


def test_short_numbers_are_not_ids():
    assert spans("BP 120/80, HR 72, 3 tabs", "shape:id") == []
    # an ID field label followed by prose, or by too few digits, gives no field value
    assert spans("Case: patient is a 45yo man; visit 2", "shape:id") == []
    assert spans("email a.b@example.com today", "shape:handle") == []
    # neither a date nor a URL path is a slash-joined ID
    got = spans("seen 23/09/2026, see https://example.org/r/301451", "shape:id")
    assert not any("/" in g for g in got)
    # a dotted code after an ID label needs 5+ digits (ICD-10 codes); nor is a date an ID
    assert spans("ICD-10 reference: K21.9. Audit", "shape:id") == []
    assert spans("version v1.2.3 and 12.05.2020 noted", "shape:id") == []
    # 7 digits in other shapes are no phone numbers
    assert spans("HRN: 0012-883-1947; lot 4471 203", "shape:phone") == []


@pytest.mark.parametrize("text, want", [
    ("Mr Dinesh s/o Muthu, 67-year-old male", "Dinesh s/o Muthu"),
    ("DINESH S/O MUTHU reviewed", "DINESH S/O MUTHU"),
    ("Nadiah binte Osman, 33F", "Nadiah binte Osman"),
    ("VIVIAN D'CRUZ, 80-year-old male", "VIVIAN D'CRUZ"),
    ("Seen with Vivian D'Cruz today", "Vivian D'Cruz"),
    ("NOK is Maria de Souza (wife)", "Maria de Souza"),
    ("NOK: de Souza.", "de Souza"),
    ("Discussed with Koh G.C. (consultant)", "Koh G.C."),
    ("Ho Jun Jie was seen", "Ho Jun Jie"),
    ("YEO JUN JIE, 40M", "YEO JUN JIE"),
    ("Reviewed by Mdm Tan in clinic", "Tan"),
    # one word after a role or relation word, or tagged with one (P7 misses)
    ("Seen by Nurse Lim at 3pm.", "Lim"),
    ("NOK: SON VIJAY (tel 9123 4567)", "VIJAY"),
    ("Caller: Aisyah (daughter) called.", "Aisyah"),
    ("Spoke to Aisyah (daughter) today.", "Aisyah"),
    # notes_v2 misses: a relation before a bracketed name, lists, field labels, inverted and
    # hyphenated names, "Md.", a capitalised particle at the end
    ("Wife (Rosnah) at bedside", "Rosnah"),
    ("present: wife Norhayati, sons Irfan and Hakim. Not", "Irfan"),
    ("present: wife Norhayati, sons Irfan and Hakim. Not", "Hakim"),
    ("Dental officer: Dr Hannah Ng; DSA: Salina", "Salina"),
    ("PT: Rajeswari (Senior Physio)", "Rajeswari"),
    ("Bed 2 - RAJOO, 74M, cellulitis", "RAJOO"),
    ("Name: KOH WEI LIANG, DARREN   NRIC: S1234567D", "KOH WEI LIANG, DARREN"),
    ("Client: BAUTISTA, Maricel Dizon   FIN: G1234567X", "BAUTISTA, Maricel Dizon"),
    ("Referring: Dr Lim E-Lynn (ED)", "Lim E-Lynn"),
    ("Re: MD. SHAHADAT HOSSAIN, FIN", "MD. SHAHADAT HOSSAIN"),
    ("Pt: Brandon Sim Jia Le, 22M", "Brandon Sim Jia Le"),
    ("Dear Siti, thank you", "Siti"),
    # notes_v3 misses: initials after an honorific, a staff tag, visitor/FDW, a sign-off, Han
    ("Verified: Dr R. Balakrishnan.", "R. Balakrishnan"),
    ("[23/09/26, 08:15] Balan (MSW): Noted.", "Balan"),
    ("do NOT give info to visitor Arun.", "Arun"),
    ("found by FDW Suryati at 0630", "Suryati"),
    ("the medical details.\nThanks, Farhan | Safety", "Farhan"),
    ("Regards,\nMeiling\nWard 5", "Meiling"),
    ("Patient: TAN Bee Hwa (陈美华), 43F", "陈美华"),
    # notes_v4 misses: initials signing a record, a Han-character name after "Patient:"
    ("handed over at 04:09.\nSigned: L.W.X.", "L.W.X."),
    ("病人 Patient: 陈美玲 (TAN MEI LING)", "陈美玲"),
    # P12: more relation words, lists joined by a slash, possessives, quoted nicknames,
    # initials alone on a line
    ("CD count correct, witnessed Aung / Kavitha.", "Kavitha"),
    ("Break-up with girlfriend Jolene 2 weeks ago", "Jolene"),
    ("A nurse named Ruby told him", "Ruby"),
    ("family of SIVA (proband)", "SIVA"),
    ("driver Balachandran.", "Balachandran"),
    ("Baby of Santos admitted", "Santos"),
    ("Also note Siva's wife Meena is", "Siva"),
    ('Supachai Wongsakul ("Jay"), S Pass', "Jay"),
    ('known as "Ah Boy". Previous', "Ah Boy"),
    ("Re: Mdm Fong Siew Lan 方秀兰, DOB 1951", "方秀兰"),  # notes_v6: after the romanised name
    ("please raise prenatal testing.\n> AP\n", "AP"),
    # notes_v9 misses: a Malay kinship cue, a quoted name with an initial, initials with a
    # staff tag, a Han-character name after a bilingual role label
    ("Anak perempuan Salmah datang", "Salmah"),
    ("isteri Rosnah di sisi katil", "Rosnah"),
    ('name "ARUMUGAM V" on the tag', "ARUMUGAM V"),
    ("for review in AM.\nPN/HO\n", "PN"),
    ("医师 Physician: 梁国栋\n", "梁国栋"),
    ("主诊医生: 林志强 (Dr Lim)", "林志强"),
])
def test_names(text, want):
    assert want in spans(text, "shape:name")


@pytest.mark.parametrize("text", [
    "Herbs: decoction plus 当归 10 g, 甘草 6 g",  # after a lower-case word
    "Sunrise Wellness Clinic 中医馆\nhours 9-5",  # no surname first
    "Seen by Dr Ong Li Ting 医生 today",
])
def test_chinese_terms_after_romanised_words_are_not_names(text):
    assert not set(spans(text, "shape:name")) & {"当归", "甘草", "中医馆", "医生"}


def test_name_runs_do_not_cross_line_breaks():
    got = spans("LABORATORY REPORT\nPatient Name: Tan Ah Kow", "shape:name")
    assert "Tan Ah Kow" in got
    assert not any("\n" in g for g in got)


def test_a_street_inside_a_block_address_is_not_proposed_again():
    got = spans("Lives at Blk 123 Pasir Ris Drive 3, #07-403 with son.", "shape:address")
    assert "Blk 123 Pasir Ris Drive 3, #07-403" in got
    assert "Ris Drive 3, #07-403" not in got


def test_name_runs_are_trimmed_of_stop_words():
    got = spans("Patient Tan Ah Kow Admitted today", "shape:name")
    assert "Tan Ah Kow" in got
    assert not any(g.startswith("Patient") or g.endswith("Admitted") for g in got)


@pytest.mark.parametrize("text", [
    "Patient Admitted Today",
    "HIV CXR ECG normal",
    "Seen in Emergency Department",
    "Tan was seen",  # one capitalised word without an honorific
    "the nurse Station was busy",  # a stop word after a role word
    "Employer: Acme\nPte Ltd",  # company suffixes are stop words
])
def test_non_names_are_not_proposed(text):
    assert spans(text, "shape:name") == []


@pytest.mark.parametrize("text, want", [
    ("Lives at Blk 429 Punggol Field #13-264, 372826.", "Blk 429 Punggol Field #13-264"),
    ("Home: 12 Jalan Bukit Merah, Singapore", "12 Jalan Bukit Merah"),
    ("at Palm Crest, 54 Jalan Bukit Merah #12-505 today",
     "Palm Crest, 54 Jalan Bukit Merah #12-505"),
    ("stays at 88 Tampines Street 81 #18-991 The Verdana, Singapore",
     "88 Tampines Street 81 #18-991 The Verdana"),
    ("Block 5 Lorong 3 Geylang", "Block 5 Lorong 3 Geylang"),
    # "St" is a street type; a numbered street needs no block (P7 misses)
    ("Address: Blk 123 Tampines St 11 #05-432 Singapore", "Blk 123 Tampines St 11 #05-432"),
    ("Lives at Woodlands Ave 6 with family.", "Woodlands Ave 6"),
    ("near Bedok North Avenue 2.", "Bedok North Avenue 2"),
    ("at Sungei Kadut Harmony Dormitory, Blk B Rm 07-12, 12 Sungei Kadut Ave, Singapore",
     "Sungei Kadut Harmony Dormitory, Blk B Rm 07-12, 12 Sungei Kadut Ave"),
])
def test_addresses(text, want):
    assert want in spans(text, "shape:address")


@pytest.mark.parametrize("text, want, label", [
    ("Known HIV infection on antiretroviral", "Known HIV infection", "hiv_sti"),
    ("genetically confirmed familial hypercholesterolaemia noted",
     "genetically confirmed familial hypercholesterolaemia", "genetic"),
    ("Huntington disease gene positive", "Huntington disease gene positive", "genetic"),
    ("generalised anxiety disorder on SSRI", "generalised anxiety disorder", "mental_health"),
    ("on gender-affirming hormone therapy", "gender-affirming hormone therapy",
     "reproductive_sexual"),
    ("methadone maintenance counselling", "methadone maintenance counselling", "substance_use"),
    # notes_v3 misses
    ("HIV-1 reactive on rapid test", "HIV-1 reactive", "hiv_sti"),
    ("known G6PD deficiency, avoid", "known G6PD deficiency", "genetic"),
    ("Drinks 4-5 cans of beer most nights", "cans of beer", "substance_use"),
    ("delivered by emergency caesarean section", "caesarean section", "reproductive_sexual"),
])
def test_shi_lexicon(text, want, label):
    assert want in spans(text, f"lexicon:{label}")


def test_rules_and_extra_are_merged_once_per_interval():
    text = "NRIC S1234567D, name Tan Ah Kow"
    s = text.index("S1234567D") + 1
    extra = [Candidate(s, s + 8, type="nric", detector="pf"),
             Candidate(1, 4, type=None, detector=None)]
    props = propose.propose(text, extra=extra)
    nric = [p for p in props if (p.start, p.end) == (s, s + 8)]
    assert len(nric) == 1 and "pf" in nric[0].sources and len(nric[0].sources) >= 2
    assert nric[0].candidate().detector == "+".join(nric[0].sources)
    assert any(p.sources == ["extra"] for p in props)
    assert props == sorted(props, key=lambda p: (p.start, -p.end))
    assert len({(p.start, p.end) for p in props}) == len(props)


def test_one_word_engine_names_that_are_stop_words_are_skipped():
    text = "Daughter at bedside; Nurse Lim"
    extra = [Candidate(1, 8, type="name", detector="pf"),
             Candidate(22, 26, type="person", detector="ner"),
             Candidate(28, 30, type="person", detector="ner")]
    got = {text[p.start - 1:p.end] for p in propose.propose(text, extra=extra)}
    assert "Daughter" not in got and "Nurse" not in got and "Lim" in got


@pytest.mark.parametrize("text", [
    "To: Work Pass Division, Ministry",
    "STUDY ENROLMENT\nParticipant ID 12",
    "Referred to Speech and Language Therapy today",
    "Solicitors: Lim & Varghese LLC",
    "PRIVATE AMBULANCE RUN SHEET\nCrew",
    "Reviewed by the Nursing Practice Committee.",
    "Validated by Clinical Scientist on duty",
    "escalated to the Night Supervisor at 20:00",
    "Carer present. Coroner notified.",
])
def test_headings_organisations_and_roles_are_not_names(text):
    assert spans(text, "shape:name") == []


@pytest.mark.parametrize("text, want", [
    ("Hi Joyce, please see bed 9", "Joyce"),
    ("[07:02] R: Morning Jess! ok", "Jess"),
])
def test_a_greeting_cues_one_name(text, want):
    assert want in spans(text, "shape:name")


def _extra_spans(text, *items):
    """The proposals for ``text`` given engine spans ``(match, type)`` (first occurrence)."""
    extra = [Candidate(text.index(m) + 1, text.index(m) + len(m), type=t, detector="pf")
             for m, t in items]
    return {text[p.start - 1:p.end] for p in propose.propose(text, extra=extra)}


def test_engine_names_that_are_organisations_headings_or_stop_words_are_skipped():
    text = "Sending: Eastshore GH Ward 5. Adm via ED Resus. Birth cert / NRIC seen. Ros called."
    got = _extra_spans(text, ("Eastshore GH Ward", "person"), ("Adm", "name"),
                       ("ED Resus", "name"), ("Birth cert / NRIC", "person"), ("Ros", "name"))
    assert not got & {"Eastshore GH Ward", "Adm", "ED Resus", "Birth cert / NRIC"}
    assert "Ros" in got  # a short name is no fragment


def test_engine_fragments_are_skipped_unless_a_shape_proposes_them():
    text = "Pain 7 of 10 at 0200; seen by T. Called 6123 4567."
    got = _extra_spans(text, ("10", "account"), ("T", "address"), ("6123 4567", "phone"))
    assert not got & {"10", "T"} and "6123 4567" in got


def test_family_terms_of_address_are_not_names():
    text = "Jasmine: Ah Ma fell again. Don't tell Papa. Seen by Dr Ma and SN Ma. Lourdes Bautista."
    got = _extra_spans(text, ("Ah Ma", "name"), ("Papa", "person"), ("Ma. Lourdes", "name"))
    assert not got & {"Ah Ma", "Papa"} and "Ma. Lourdes" in got
    got = {text[p.start - 1:p.end] for p in propose.propose(text)}
    assert "Ma" in got  # after an honorific: the surname Ma


def test_kinship_phrases_alone_are_not_names():
    text = "Anak perempuan Salmah called. - Mak Cik's son will come."
    got = _extra_spans(text, ("Anak perempuan", "person"), ("Mak Cik", "name"))
    assert not got & {"Anak perempuan", "Mak Cik", "Mak Cik's"} and "Salmah" in got


def test_a_han_term_after_a_colon_is_not_a_name():
    assert "肝郁脾虚" not in spans("证: 肝郁脾虚, 舌淡", "shape:name")


def test_a_name_found_once_is_proposed_at_every_mention():
    text = "[3/10, 09:01] Marivic: ok po\n[3/10, 09:05] Marivic: masakit\nsaid Marivic again"
    s = text.index("Marivic") + 1
    props = propose.propose(text, extra=[Candidate(s, s + 6, type="person", detector="pf")])
    got = [p for p in props if text[p.start - 1:p.end] == "Marivic"]
    assert len(got) == 3
    assert sum("shape:repeat" in p.sources for p in got) == 2


def test_only_name_like_words_are_repeated():
    text = "buntis po ako. Ah Boy ok. may bleeding po ako; Ah said so"
    got = _extra_spans(text, ("po ako", "person"), ("Ah", "name"))
    assert got & {"po ako"} and text.count("po ako") == 2
    props = propose.propose(text, extra=[
        Candidate(text.index("po ako") + 1, text.index("po ako") + 6, type="person",
                  detector="pf")])
    assert not any("shape:repeat" in p.sources for p in props)


def test_a_cue_before_an_organisation_cues_no_name():
    text = ("Yours faithfully,\nMarina Crest Law LLC\n"
            "Fell at work (Goh & Sons Plumbing Pte Ltd).\nRegards,\nWei Ling")
    got = spans(text, "shape:name")
    assert not {"Marina", "Plumbing"} & set(got)
    assert "Wei Ling" in got  # a sign-off before a person still cues the name


def test_engine_names_across_a_line_break_keep_the_first_line():
    text = "on 2 L.\nSon asked for photos. Seen by Karen Ong\nFamily Physician, today."
    got = _extra_spans(text, ("L.\nSon", "name"), ("Karen Ong\nFamily", "person"))
    assert not got & {"L.\nSon", "L.", "Son", "Karen Ong\nFamily", "Family"}
    assert "Karen Ong" in got


def test_empty_text_and_trimmed_edges():
    assert propose.propose("") == []
    for p in propose.propose("Pt: (Tan Ah Kow), tel 6123-4567; seen."):
        m = "Pt: (Tan Ah Kow), tel 6123-4567; seen."[p.start - 1:p.end]
        assert m == m.strip(" ,;:") and m


def test_contained():
    a, b = propose.Proposal(3, 10), propose.Proposal(5, 8)
    assert propose.contained(b, a) and not propose.contained(a, b)
    assert not propose.contained(a, propose.Proposal(3, 10))
