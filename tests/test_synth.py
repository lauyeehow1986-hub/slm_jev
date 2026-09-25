"""Tests for the synthetic Singapore corpus generator (slmjev.synth)."""

import json
from collections import Counter

import pytest

from slmjev import rules, synth

LABELS = synth.load_labels()


@pytest.fixture(scope="module")
def train():
    return synth.generate("train", n_notes=400, n_cells=300, seed=7)


@pytest.fixture(scope="module")
def test_split():
    return synth.generate("test", n_notes=400, n_cells=300, seed=7)


def _all(docs):
    return [(d, s) for d in docs for s in d["spans"]]


def test_every_doc_validates(train, test_split):
    for d in train + test_split:
        synth.validate_doc(d, LABELS)


def test_offsets_slice_back(train):
    for d in train:
        for s in d["spans"] + d["decoys"]:
            assert d["text"][s["start"] - 1:s["end"]] == s["match"]


def test_gold_spans_do_not_overlap(train):
    for d in train:
        ivs = sorted((s["start"], s["end"]) for s in d["spans"])
        for (_, e1), (s2, _) in zip(ivs, ivs[1:], strict=False):
            assert s2 > e1, d["id"]


def test_deterministic():
    a = synth.generate("dev", n_notes=30, n_cells=30, seed=11)
    b = synth.generate("dev", n_notes=30, n_cells=30, seed=11)
    c = synth.generate("dev", n_notes=30, n_cells=30, seed=12)
    assert json.dumps(a) == json.dumps(b)
    assert json.dumps(a) != json.dumps(c)


def test_label_coverage(train):
    got = Counter(s["label"] for _, s in _all(train))
    text_labels = set(LABELS["identifiers"]) - {"biometric", "photo"}
    for label in text_labels | set(LABELS["shi"]):
        assert got[label] >= 5, (label, got[label])


def test_name_ethnicity_coverage(train):
    eth = Counter(s["attrs"]["ethnicity"] for _, s in _all(train) if s["label"] == "name")
    assert set(eth) == {"chinese", "malay", "indian", "eurasian"}
    assert min(eth.values()) >= 20


def test_test_split_names_and_streets_unseen(train, test_split):
    def pool(docs, label):
        return {s["match"].lower() for _, s in _all(docs) if s["label"] == label}
    assert not pool(train, "name") & pool(test_split, "name")
    assert not pool(train, "address") & pool(test_split, "address")


def test_national_ids_have_valid_check_letters(train):
    for _, s in _all(train):
        if s["type"] in ("nric", "fin"):
            assert rules.nric_valid(s["match"]), s["match"]


def test_address_and_postal_are_separate_with_property_kind(train):
    for d in train:
        for s in d["spans"]:
            if s["label"] in ("address", "postal_code"):
                assert s["attrs"]["property"] in LABELS["property_kinds"]
            if s["label"] == "address":
                assert "singapore" not in s["match"].lower()
            if s["label"] == "postal_code":
                assert s["match"].isdigit() and len(s["match"]) == 6


def test_every_date_carries_a_role(train):
    roles = Counter()
    for d in train:
        for s in d["spans"]:
            if s["label"] in ("dob", "date_of_death"):
                roles[s["attrs"]["role"]] += 1
        for s in d["decoys"]:
            if s["type"] == "date":
                roles[s["attrs"]["role"]] += 1
    assert set(roles) == set(LABELS["date_roles"]["gold"]) | set(LABELS["date_roles"]["decoy"])


def test_has_negative_controls_and_misplaced_cells(train):
    notes = [d for d in train if d["kind"] == "note"]
    cells = [d for d in train if d["kind"] == "cell"]
    assert sum(1 for d in notes if not d["spans"]) >= 0.05 * len(notes)
    assert sum(1 for d in cells if d["meta"].get("misplaced")) >= 0.2 * len(cells)
    assert any(d["decoys"] and not d["spans"] for d in notes)


def test_rules_find_every_nric_and_email(train):
    # Formats the rule layer is built for must be proposed; the judge can't pick what's missing.
    for d in train:
        found = {(sp["start"], sp["end"]) for sp in rules.scan_text(d["text"])}
        for s in d["spans"]:
            if s["type"] in ("nric", "fin", "email"):
                assert (s["start"], s["end"]) in found, (d["id"], s["match"])


def test_write_jsonl_roundtrip(tmp_path):
    docs = synth.generate("dev", n_notes=5, n_cells=5, seed=1)
    path = tmp_path / "dev.jsonl"
    manifest = synth.write_jsonl(docs, path)
    back = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert back == docs
    assert manifest["n_docs"] == 10 and len(manifest["sha256"]) == 64
