"""Tests for slmjev.rules: unit checks plus R parity against tests/parity/r_expected.json."""

import datetime as dt
import json
from pathlib import Path

import pytest

from slmjev import rules

PARITY = Path(__file__).parent / "parity"
CASES = json.loads((PARITY / "cases.json").read_text(encoding="utf-8"))
R = json.loads((PARITY / "r_expected.json").read_text(encoding="utf-8"))
TEXTS = {c["id"]: c for c in CASES["scan"]}


def _key(sp):
    return (sp["start"], sp["end"], sp["match"], sp["type"], sp["identifier"], sp["detector"])


def _assert_spans_equal(py, r):
    assert [_key(s) for s in py] == [_key(s) for s in r]
    for a, b in zip(py, r, strict=True):
        assert a["confidence"] == pytest.approx(b["confidence"], abs=1e-12)


# --- R parity ------------------------------------------------------------------------------

def test_golden_covers_every_case():
    assert [c["id"] for c in R["scan"]] == list(TEXTS)
    assert len(R["classify"]) == len(CASES["classify"])


@pytest.mark.parametrize("case", R["scan"], ids=lambda c: c["id"])
def test_scan_text_matches_r(case):
    src = TEXTS[case["id"]]
    spans = rules.scan_text(src["text"], rules.detectors(postal6=src["postal6"]))
    _assert_spans_equal(spans, case["spans"])


@pytest.mark.parametrize("case", R["scan"], ids=lambda c: c["id"])
def test_dedup_overlaps_matches_r(case):
    src = TEXTS[case["id"]]
    spans = rules.scan_text(src["text"], rules.detectors(postal6=src["postal6"]))
    _assert_spans_equal(rules.dedup_overlaps(spans), case["dedup"])


@pytest.mark.parametrize("case", R["classify"], ids=lambda c: repr(c["value"]))
def test_classify_value_matches_r(case):
    hits = rules.classify_value(case["value"], rules.detectors(postal6=case["postal6"]))
    assert list(hits) == list(case["hits"])
    for name, conf in case["hits"].items():
        assert hits[name] == pytest.approx(conf, abs=1e-12)


@pytest.mark.parametrize("case", R["nric"], ids=lambda c: repr(c["x"]))
def test_nric_valid_matches_r(case):
    assert rules.nric_valid(case["x"]) is case["ok"]


@pytest.mark.parametrize("case", R["compact_date"], ids=lambda c: repr(c["s"]))
def test_parse_compact_date_matches_r(case):
    got = rules.parse_compact_date(case["s"])
    assert (got.isoformat() if got else None) == case["date"]


@pytest.mark.parametrize("case", R["luhn"], ids=lambda c: repr(c["x"]))
def test_luhn_matches_r(case):
    assert rules.luhn(case["x"]) is case["ok"]


# --- contract and unit checks --------------------------------------------------------------

@pytest.mark.parametrize("case_id", list(TEXTS))
def test_spans_slice_back_to_match(case_id):
    text = TEXTS[case_id]["text"]
    for sp in rules.scan_text(text, rules.detectors(postal6=TEXTS[case_id]["postal6"])):
        assert text[sp["start"] - 1:sp["end"]] == sp["match"]


def test_detector_order_and_postal6_last():
    names = list(rules.detectors(postal6=True))
    assert names[:4] == ["nric", "temp_ic", "email", "phone"]
    assert names[-1] == "postal6"
    assert "postal6" not in rules.detectors()


def test_extra_detectors_append_after_shipped():
    extra = rules.Detector("wl_name", "name", "name", r"Tan Ah Kow", None, 0.95)
    spans = rules.scan_text("Pt Tan Ah Kow", rules.detectors(extra=[extra]))
    assert spans == [{"start": 4, "end": 13, "match": "Tan Ah Kow", "type": "name",
                      "identifier": "name", "detector": "rule:wl_name", "confidence": 0.95}]


def test_nric_check_letters():
    assert rules.nric_valid("S1234567D")
    assert rules.nric_valid("t1234567j")
    assert not rules.nric_valid("S1234567A")
    assert not rules.nric_valid(None)


def test_compact_date_orders():
    assert rules.parse_compact_date("19900131") == dt.date(1990, 1, 31)
    assert rules.parse_compact_date("31011990") == dt.date(1990, 1, 31)
    assert rules.parse_compact_date("12131990") == dt.date(1990, 12, 13)
    assert rules.parse_compact_date("19901231t143000") is None


def test_failed_validator_drops_confidence_by_0_4():
    [sp] = [s for s in rules.scan_text("S1234567A") if s["detector"] == "rule:nric"]
    assert sp["confidence"] == pytest.approx(0.2)
    assert rules.classify_value("S1234567A")["nric"] == pytest.approx(0.2)
    [cd] = [s for s in rules.scan_text("12345678") if s["detector"] == "rule:date_compact"]
    assert cd["confidence"] == pytest.approx(0.15)


def test_empty_inputs():
    assert rules.scan_text("") == []
    assert rules.scan_text(None) == []
    assert rules.classify_value("  \t") == {}
    assert rules.dedup_overlaps([]) == []
