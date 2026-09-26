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
])
def test_numbers_ids_and_plates(text, want, source):
    assert want in spans(text, source)


def test_short_numbers_are_not_ids():
    assert spans("BP 120/80, HR 72, 3 tabs", "shape:id") == []


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
])
def test_names(text, want):
    assert want in spans(text, "shape:name")


def test_name_runs_are_trimmed_of_stop_words():
    got = spans("Patient Tan Ah Kow Admitted today", "shape:name")
    assert "Tan Ah Kow" in got
    assert not any(g.startswith("Patient") or g.endswith("Admitted") for g in got)


@pytest.mark.parametrize("text", [
    "Patient Admitted Today",
    "HIV CXR ECG normal",
    "Seen in Emergency Department",
    "Tan was seen",  # one capitalised word without an honorific
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


def test_empty_text_and_trimmed_edges():
    assert propose.propose("") == []
    for p in propose.propose("Pt: (Tan Ah Kow), tel 6123-4567; seen."):
        m = "Pt: (Tan Ah Kow), tel 6123-4567; seen."[p.start - 1:p.end]
        assert m == m.strip(" ,;:") and m


def test_contained():
    a, b = propose.Proposal(3, 10), propose.Proposal(5, 8)
    assert propose.contained(b, a) and not propose.contained(a, b)
    assert not propose.contained(a, propose.Proposal(3, 10))
