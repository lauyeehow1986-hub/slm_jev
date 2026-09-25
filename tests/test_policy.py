"""Tests for slmjev.policy. The policies here are made up; real ones stay in docs/private/."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from slmjev import policy

REPO = Path(__file__).resolve().parents[1]

# Deliberately arbitrary: exercises every shape without mirroring any real standard.
TOY = {
    "name": "toy",
    "version": "1",
    "rules": {
        "email": {"action": "retain"},
        "national_id": {"action": "pseudonymize"},
        "url": {"action": "remove"},
        "other_id": {"action": "flag", "options": ["remove", "retain"]},
        "ward_note": {
            "by": "setting",
            "cases": {"inpatient": {"action": "generalize", "method": "year"},
                      "outpatient": {"action": "retain"}},
            "unknown": {"action": "generalize", "method": "year"},
        },
    },
}


def span(type_, identifier):
    return {"type": type_, "identifier": identifier}


@pytest.fixture
def toy():
    return policy.validate(TOY)


def test_identifier_lookup(toy):
    r = policy.resolve(span("nric", "national_id"), toy)
    assert r == {"policy_key": "national_id", "action": "pseudonymize", "method": None,
                 "options": [], "needs_review": False}


def test_type_wins_over_identifier(toy):
    assert policy.resolve(span("url", "other_id"), toy)["action"] == "remove"


def test_flag_always_needs_review(toy):
    r = policy.resolve(span("ip", "other_id"), toy)
    assert (r["policy_key"], r["action"], r["options"], r["needs_review"]) == (
        "other_id", "flag", ["remove", "retain"], True)


def test_missing_rule_fails_closed(toy):
    r = policy.resolve(span("phone", "phone"), toy)
    assert r["policy_key"] is None
    assert r["action"] == "flag"
    assert r["needs_review"] is True


def test_context_case_is_applied(toy):
    r = policy.resolve(span("ward_note", "other_id"), toy, {"setting": "outpatient"})
    assert (r["action"], r["needs_review"]) == ("retain", False)


def test_unknown_context_uses_unknown_rule_and_needs_review(toy):
    for ctx in (None, {}, {"setting": "not_a_case"}):
        r = policy.resolve(span("ward_note", "other_id"), toy, ctx)
        assert (r["action"], r["method"], r["needs_review"]) == ("generalize", "year", True)


@pytest.mark.parametrize("bad", [
    {"rules": {"x": {"action": "shred"}}},
    {"rules": {"x": {"action": "generalize"}}},
    {"rules": {"x": {"action": "flag"}}},
    {"rules": {"x": {"action": "flag", "options": ["flag"]}}},
    {"rules": {"x": {"by": "k", "cases": {"a": {"action": "retain"}}}}},
    {"rules": {"x": {"by": "k", "cases": {}, "unknown": {"action": "retain"}}}},
    {"rules": []},
    {},
])
def test_validate_rejects_malformed(bad):
    with pytest.raises(ValueError):
        policy.validate(bad)


def test_load_from_explicit_path(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(TOY), encoding="utf-8")
    assert policy.load_policy(p)["name"] == "toy"


def test_load_from_env(tmp_path, monkeypatch):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(TOY), encoding="utf-8")
    monkeypatch.setenv("SLMJEV_POLICY", str(p))
    assert policy.load_policy()["name"] == "toy"


def test_load_missing_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SLMJEV_POLICY", raising=False)
    with pytest.raises(FileNotFoundError):
        policy.load_policy(search_from=tmp_path)


# --- guards for the local, uncommitted policy ---------------------------------------------

def test_private_folder_is_gitignored():
    if shutil.which("git") is None:
        pytest.skip("git not available")
    out = subprocess.run(["git", "check-ignore", "-q", "docs/private/policy_probe.json"],
                         cwd=REPO, capture_output=True)
    assert out.returncode == 0, "docs/private/ must stay gitignored"


def test_local_policy_is_valid_if_present(monkeypatch):
    monkeypatch.delenv("SLMJEV_POLICY", raising=False)
    try:
        pol = policy.load_policy(search_from=REPO)
    except FileNotFoundError:
        pytest.skip("no local policy on this machine")
    assert pol["rules"]
