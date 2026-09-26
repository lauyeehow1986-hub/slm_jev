import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from slmjev import judge as J
from slmjev import policy
from slmjev.netguard import NetworkForbidden

_TEXT_TO_KEY = ({v: k for k, v in J.OPTION_TEXT.items()}
                | {v: "none" for v in J.NONE_TEXT.values()}
                | {v: k for k, v in J.PROPERTY_OPTIONS.items()}
                | {k: k for k in [*J.SCORE_OPTIONS, "yes", "no"]})
_OPT = re.compile(r"^([A-X])\) (.*)$")
_STEMS = [(t, re.compile(re.escape(t).replace(re.escape("{span}"), ".*") + "$"))
          for t in (J.CHOICE_STEM, J.NOUL_STEM, J.SCORE_STEM, J.PROPERTY_STEM)]


def stem_template(line):
    """The stem template a question line was formatted from (the line itself if none)."""
    return next((t for t, rx in _STEMS if rx.match(line)), line)


class Fake:
    """Answers from a script ``{stem template: {option key: prob}}``; leftover mass is spread
    evenly. ``bias`` moves that share of the mass onto letter A, whatever option it labels."""

    def __init__(self, script, bias=0.0, fail=(), off_format=()):
        self.script, self.bias, self.fail, self.off = script, bias, fail, off_format
        self.calls = []

    def first_token(self, prefix, question):
        self.calls.append((prefix, question))
        stem = stem_template(question.split("\n", 1)[0])
        if stem in self.fail:
            raise J.JudgeError("boom")
        if stem in self.off:
            return {"The": 0.9, "A": 0.05}
        shown = [(m[1], _TEXT_TO_KEY[m[2]]) for line in question.split("\n")
                 if (m := _OPT.match(line))]
        want = self.script.get(stem, {})
        rest = [k for _, k in shown if k not in want]
        left = max(0.0, 1.0 - sum(want.get(k, 0) for _, k in shown))
        p = {letter: want.get(k, left / len(rest) if rest else 0.0) for letter, k in shown}
        p = {letter: (1 - self.bias) * v + (self.bias if letter == "A" else 0.0)
             for letter, v in p.items()}
        return {(" " + k if i % 2 else k): v for i, (k, v) in enumerate(p.items())}


def script(choice, noul=None, score=None, prop=None):
    s = {J.CHOICE_STEM: choice}
    if noul is not None:
        s[J.NOUL_STEM] = {"yes": noul, "no": 1 - noul}
    if score is not None:
        s[J.SCORE_STEM] = score
    if prop is not None:
        s[J.PROPERTY_STEM] = prop
    return s


TEXT = "Pt Tan Ah Kow, ref S1234567A, admitted 03/02/2025. DOB 12/05/1950. Hb 12.1."
NOUL_GATED = J.Thresholds(disagree_at=0.5)


def cand(sub, **kw):
    i = TEXT.index(sub)
    return J.Candidate(i + 1, i + len(sub), **kw)


# --- building blocks -------------------------------------------------------------------------


def test_rotations_put_every_option_in_every_position():
    rs = J.rotations(5)
    assert len(rs) == 5
    for pos in range(5):
        assert sorted(r[pos] for r in rs) == list(range(5))
    sub = J.rotations(10, 4)
    assert len(sub) == 4 and len({r[0] for r in sub}) == 4
    assert J.rotations(3, 99) == J.rotations(3)


def test_letter_mass_merges_token_variants():
    m = J.letter_mass({"A": 0.2, " A": 0.1, "B)": 0.3, "b": 0.1, "Hello": 0.3}, "AB")
    assert m == pytest.approx({"A": 0.3, "B": 0.4})


def test_permutation_averaging_removes_position_bias():
    # every single order puts half the mass on letter A; only the average recovers the truth
    fake = Fake({"Q": {"x": 1.0}}, bias=0.5)
    _TEXT_TO_KEY.update({"x": "x", "y": "y", "z": "z"})
    opts = {"y": "y", "z": "z", "x": "x"}
    first = J.ask(fake, "", "Q", opts, [[0, 1, 2]])
    assert first.top[0] == "y"  # a single order is fooled by the bias
    avg = J.ask(fake, "", "Q", opts, J.rotations(3))
    assert avg.top[0] == "x"
    assert avg.probs["x"] == pytest.approx(0.5 + 0.5 / 3)
    assert avg.spread == pytest.approx(0.5)


@pytest.mark.parametrize(("s", "fam"), [
    ("12/05/1950", "date"), ("1950-05-12", "date"), ("12 May 1950", "date"),
    ("May 12, 1950", "date"), ("19500512", "numeric"), ("S1234567A", "code"),
    ("tan@example.com", "code"), ("ahkow.tan@example.org", "code"), ("9123 4567", "numeric"),
    ("Blk 123 Ang Mo Kio Ave 3", "alnum"), ("HIV-1", "alnum"), ("SGH1234567", "alnum"),
    ("Tan Ah Kow", "text"), ("schizophrenia", "text"), ("91234567", "code"),
    ("3/7", "fraction"), ("120/80", "fraction"), ("12/05/50", "date"),
])
def test_family_of_uses_surface_shape(s, fam):
    assert J.family_of(s) == fam


@pytest.mark.parametrize(("text", "sub", "column", "fam"), [
    ("Admitted on 20230602 for review", "20230602", None, "date"),
    ("DOB: 19830112", "19830112", None, "date"),
    ("D.O.B. 19830112", "19830112", None, "date"),
    ("dated 20230602", "20230602", None, "date"),
    ("Ref 20230602 on file", "20230602", None, "numeric"),  # no date word before it
    ("Contact 91234567 today", "91234567", None, "code"),
    ("20230602", "20230602", "procedure_date", "date"),  # a whole cell of a date column
    ("20230602", "20230602", "serial_no", "numeric"),
    ("3/7", "3/7", None, "fraction"),
])
def test_family_in_context_reads_compact_dates_after_date_words(text, sub, column, fam):
    assert J.family_in_context(text, _addr(text, sub), column) == fam


def test_fractions_are_offered_no_id_options():
    fake = Fake(script({"none": 0.9}))
    J.Judge(fake).judge("Fever for 5/7, BP 120/80", _addr("Fever for 5/7, BP 120/80", "5/7"))
    opts = {_TEXT_TO_KEY[m[2]] for _, q in fake.calls for line in q.split("\n")
            if (m := _OPT.match(line))}
    assert opts == set(J.FAMILIES["fraction"]) and "case_visit" not in opts


def test_questions_end_with_the_answer_cue():
    fake = Fake(script({"none": 0.9}))
    J.Judge(fake).judge(TEXT, cand("S1234567A"))
    assert fake.calls and all(q.endswith(f"\n{J.ANSWER_CUE}") for _, q in fake.calls)


def test_every_family_has_none_and_fits_the_letters():
    for fam, keys in J.FAMILIES.items():
        assert keys[-1] == "none" and len(set(keys)) == len(keys) <= len(J.LETTERS)
        assert fam in J.NONE_TEXT
    assert set(J.FAMILIES["all"]) - {"none"} == set(J.OPTION_TEXT)


def test_context_window_marks_span_and_escapes_brackets():
    t = "a [[b]] NAME c"
    i = t.index("NAME")
    w = J.context_window(t, i + 1, i + 4, width=100)
    assert w == "a [ [b] ] [[NAME]] c"
    w2 = J.context_window("x" * 50 + "NAME" + "y" * 50, 51, 54, width=10)
    assert w2 == "..." + "x" * 10 + "[[NAME]]" + "y" * 10 + "..."


# --- decisions -------------------------------------------------------------------------------


def test_clear_identifier_is_accepted_with_contract_fields():
    fake = Fake(script({"national_id": 0.95, "none": 0.01}, noul=0.97,
                       score={"high": 0.9, "moderate": 0.1}))
    r = J.Judge(fake, ask_score=True).judge(TEXT, cand("S1234567A", type="nric"))
    assert r["match"] == "S1234567A" and r["start"] == TEXT.index("S1234567A") + 1
    assert r["end"] - r["start"] + 1 == len("S1234567A")
    assert r["detector"] == "slm:jev" and r["type"] == "nric"
    assert r["category"] == r["identifier"] == "national_id"
    assert r["decision"] == "identifier" and r["needs_review"] is False
    assert r["p_identifier"] == pytest.approx(0.99) and r["confidence"] == r["p_identifier"]
    assert r["sensitivity"] == pytest.approx((0.9 * 3 + 0.1 * 2) / 3, abs=1e-3)
    assert sum(r["category_probs"].values()) == pytest.approx(1.0, abs=1e-3)


def test_clear_decoy_is_dropped():
    fake = Fake(script({"none": 0.99}, noul=0.02))
    r = J.Judge(fake).judge(TEXT, cand("12.1"))
    assert r["decision"] == "not_identifier" and not r["needs_review"]
    assert r["category"] == "none" and r["identifier"] is None


def test_uncertain_goes_to_review():
    fake = Fake(script({"none": 0.5, "mrn": 0.4}, noul=0.5))
    r = J.Judge(fake).judge(TEXT, cand("S1234567A"))
    assert r["decision"] == "review" and r["needs_review"]
    assert "uncertain" in r["judge"]["reasons"]


def test_noul_disagreement_goes_to_review_when_gated():
    fake = Fake(script({"none": 0.99}, noul=0.9))
    r = J.Judge(fake, ask_noul=True, thresholds=NOUL_GATED).judge(TEXT, cand("12.1"))
    assert r["decision"] == "review" and "choice_noul_disagree" in r["judge"]["reasons"]


def test_noul_is_advisory_by_default():
    fake = Fake(script({"none": 0.99}, noul=0.9))
    r = J.Judge(fake, ask_noul=True).judge(TEXT, cand("12.1"))
    assert r["decision"] == "not_identifier" and r["judge"]["p_noul"] == pytest.approx(0.9)
    fake = Fake(script({"none": 0.99}), fail={J.NOUL_STEM})
    r = J.Judge(fake, ask_noul=True).judge(TEXT, cand("12.1"))
    assert r["decision"] == "not_identifier" and r["judge"]["errors"]


def test_stems_name_the_span():
    fake = Fake(script({"none": 0.99}))
    J.Judge(fake).judge('He said "ok"\nthen left', J.Candidate(9, 17))
    assert all(q.startswith('In this text, what is "\'ok\' then"?') for _, q in fake.calls)


def test_low_category_confidence_goes_to_review():
    fake = Fake(script({"mrn": 0.45, "case_visit": 0.45, "none": 0.0}, noul=1.0))
    r = J.Judge(fake).judge(TEXT, cand("S1234567A"))
    assert r["decision"] == "review" and "category_uncertain" in r["judge"]["reasons"]


def test_backend_failure_fails_closed():
    fake = Fake(script({"none": 0.99}), fail={J.CHOICE_STEM})
    r = J.Judge(fake).judge(TEXT, cand("12.1"))
    assert r["needs_review"] and r["decision"] == "review"
    assert r["p_identifier"] is None and r["category"] is None
    assert r["judge"]["errors"]


def test_off_format_answer_fails_closed():
    fake = Fake(script({"none": 0.99}), off_format={J.CHOICE_STEM})
    r = J.Judge(fake).judge(TEXT, cand("12.1"))
    assert r["needs_review"] and "off-format" in r["judge"]["errors"][0]


def test_noul_failure_blocks_a_drop():
    fake = Fake(script({"none": 0.99}), fail={J.NOUL_STEM})
    r = J.Judge(fake, ask_noul=True, thresholds=NOUL_GATED).judge(TEXT, cand("12.1"))
    assert r["decision"] == "review" and "noul_failed" in r["judge"]["reasons"]


def test_score_failure_is_advisory_only():
    fake = Fake(script({"national_id": 0.99, "none": 0.0}, noul=1.0), fail={J.SCORE_STEM})
    r = J.Judge(fake, ask_score=True).judge(TEXT, cand("S1234567A"))
    assert r["decision"] == "identifier" and r["sensitivity"] is None


def test_all_questions_share_one_prefix():
    fake = Fake(script({"national_id": 0.99, "none": 0.0}, noul=1.0))
    J.Judge(fake, choice_rotations=None, ask_noul=True, ask_score=True).judge(
        TEXT, cand("S1234567A"))
    prefixes = {p for p, _ in fake.calls}
    assert len(prefixes) == 1 and "[[S1234567A]]" in prefixes.pop()
    # code family: all 10 rotations + 2 Noul + 2 Score orders
    assert len(fake.calls) == 10 + 2 + 2


def test_default_is_choice_only_at_four_rotations():
    fake = Fake(script({"national_id": 0.99, "none": 0.0}, noul=1.0))
    J.Judge(fake).judge(TEXT, cand("S1234567A"))
    assert len(fake.calls) == 4
    fake = Fake(script({"national_id": 0.99, "none": 0.0}, noul=1.0))
    J.Judge(fake, choice_rotations=2, ask_noul=True).judge(TEXT, cand("S1234567A"))
    assert len(fake.calls) == 2 + 2


# --- context for the policy's `by` rules ---------------------------------------------------


def test_date_roles_come_from_the_choice():
    j = J.Judge(Fake(script({"dob": 0.9, "none": 0.05}, noul=0.95)))
    assert j.judge(TEXT, cand("12/05/1950"))["context"] == {"date_role": "dob"}
    j = J.Judge(Fake(script({"none": 0.97}, noul=0.03)))
    assert j.judge(TEXT, cand("03/02/2025"))["context"] == {"date_role": "other"}
    j = J.Judge(Fake(script({"dob": 0.5, "none": 0.3}, noul=0.6)))
    assert j.judge(TEXT, cand("12/05/1950"))["context"] == {"date_role": "unknown"}


def _addr(t, sub):
    i = t.index(sub)
    return J.Candidate(i + 1, i + len(sub))


def test_property_comes_from_address_rules():
    t = "Lives at Blk 123 Ang Mo Kio Ave 3 #05-12 Singapore 560123."
    fake = Fake(script({"address": 0.95, "none": 0.0}, prop={"landed": 0.9}))
    r = J.Judge(fake).judge(t, _addr(t, "Blk 123 Ang Mo Kio Ave 3 #05-12"))
    assert r["context"] == {"property_kind": "hdb", "property_type": "non_landed"}
    assert all(J.PROPERTY_STEM not in q for _, q in fake.calls)  # rules decided; no question
    t2 = "Addr: 12 Holland Road #03-04 The Verdana, Singapore 278960."
    r = J.Judge(fake).judge(t2, _addr(t2, "12 Holland Road #03-04 The Verdana"))
    assert r["context"] == {"property_kind": "unknown", "property_type": "non_landed"}
    t3 = "Addr: 75 Jalan Kechubong, Singapore 537890."
    r = J.Judge(fake).judge(t3, _addr(t3, "75 Jalan Kechubong"))
    assert r["context"] == {"property_kind": "unknown", "property_type": "unknown"}


def test_property_model_question_is_opt_in_and_never_overrides_rules():
    t3 = "Addr: 75 Jalan Kechubong, Singapore 537890."
    fake = Fake(script({"address": 0.95, "none": 0.0}, prop={"landed": 0.9}))
    r = J.Judge(fake, ask_property=True).judge(t3, _addr(t3, "75 Jalan Kechubong"))
    assert r["context"] == {"property_kind": "landed", "property_type": "landed"}
    fake = Fake(script({"address": 0.95, "none": 0.0}, prop={"landed": 0.4}))
    r = J.Judge(fake, ask_property=True).judge(t3, _addr(t3, "75 Jalan Kechubong"))
    assert r["context"]["property_type"] == "unknown"
    t2 = "Addr: 12 Holland Road #03-04 The Verdana, Singapore 278960."
    fake = Fake(script({"address": 0.95, "none": 0.0}, prop={"landed": 0.9}))
    r = J.Judge(fake, ask_property=True).judge(t2, _addr(t2, "12 Holland Road #03-04 The Verdana"))
    assert r["context"]["property_type"] == "non_landed"  # the unit number outranks the model


# --- rule-certain fast path ------------------------------------------------------------------


@pytest.mark.parametrize(("t", "sub", "ident"), [
    ("NRIC S1234567D on file", "S1234567D", "national_id"),
    ("mail tan.ak@example.com today", "tan.ak@example.com", "email"),
    ("see https://example.org/p?id=3 now", "https://example.org/p?id=3", "other_id"),
    ("Blk 5 Bedok North St 1 #02-11, Singapore 460005.", "460005", "postal_code"),
    ("12 Holland Road S(278960)", "278960", "postal_code"),
    # the lead merged into the candidate (the proposer joins them): labs miss in 0007
    ("Blk 690 Hougang Avenue 8 #02-029 S276963. x", "S276963", "postal_code"),
    ("12 Holland Road S(278960)", "S(278960)", "postal_code"),
    ("Clinic visit, Singapore 482263.", "Singapore 482263", "postal_code"),
    ("Block 395 Clementi Avenue 2, #17-495, 120395. Seen", "120395", "postal_code"),
    ("Stays at 17 Lorong Chuan, 556745.", "556745", "postal_code"),
    ("(temp IC Y5308811O). NOK", "Y5308811O", "national_id"),
    ("passport no. E12345678 seen", "E12345678", "national_id"),
    ("MRN: 12345678.", "12345678", "mrn"),
    ("Seen by Dr Nair (MCR M97984B).", "M97984B", "other_id"),
    ("Refund to bank account 250-03851-0.", "250-03851-0", "other_id"),
    ("Paid from a/c no. 0123 456789.", "0123 456789", "other_id"),
])
def test_rule_certain_spans_skip_the_model(t, sub, ident):
    fake = Fake(script({"none": 0.99}))
    r = J.Judge(fake).judge(t, _addr(t, sub))
    assert fake.calls == []
    assert r["decision"] == "identifier" and not r["needs_review"]
    assert r["identifier"] == ident and r["judge"]["fast_path"]
    assert r["confidence"] == J.RULE_CONFIDENCE
    if ident == "postal_code" and "Blk" in t:
        assert r["context"]["property_type"] == "non_landed"


@pytest.mark.parametrize(("t", "sub"), [
    ("ref S1234567A on file", "S1234567A"),  # bad checksum, no ID keyword
    ("Hb 460005 x", "460005"),  # six digits, no postal keyword
    ("ref S2769631 x", "S2769631"),  # seven digits after S: not a postal code
    ("ref S276963A x", "S276963A"),  # a trailing letter: an ID, not a postal code
    ("Ward 64 Bed 18, 801556", "801556"),  # no street before it
    ("Clementi Avenue 2, 991234", "991234"),  # sector 99 does not exist
    ("Lorong Chuan. Lab 556745", "556745"),  # sentence break after the street
    ("ref: tan@", "tan@"),
    ("clinic 1234567", "1234567"),  # "ic" inside a word is no keyword
    ("NRIC 3/7", "3/7"),  # not ID-shaped
    ("account balance 1234567", "1234567"),  # not right after the keyword; 7 digits
    ("bank account 250-038", "250-038"),  # too few digits for an account number
    # hard decoys: a hospital block or unit, then a lab value in a valid postal sector
    ("Reviewed at Block 4 Level 3 clinic, platelets 245000.", "245000"),
    ("Reviewed at Tower Block, PLT 245000.", "245000"),
    ("Reviewed at #05-12 clinic, WBC count 245000.", "245000"),
    ("Reviewed at Block 7, total bill 245000.", "245000"),
])
def test_uncertain_shapes_still_go_to_the_model(t, sub):
    fake = Fake(script({"none": 0.99}))
    J.Judge(fake).judge(t, _addr(t, sub))
    assert fake.calls
    fake = Fake(script({"none": 0.99}))
    J.Judge(fake, fast_path=False).judge("NRIC S1234567D", J.Candidate(6, 14))
    assert fake.calls  # the fast path can be switched off


# --- structured cells ------------------------------------------------------------------------


def test_cell_column_joins_the_prefix():
    fake = Fake(script({"dob": 0.9, "none": 0.05}))
    r = J.Judge(fake).judge("2 Feb 1940", J.Candidate(1, 10), column="date_of\nbirth")
    assert {p for p, _ in fake.calls} == {"Column: date_of birth\nText:\n[[2 Feb 1940]]\n\n"}
    assert r["decision"] == "identifier"
    J.Judge(fake).judge("2 Feb 1940", J.Candidate(1, 10))
    assert fake.calls[-1][0].startswith("Text:")  # free text: no column line


@pytest.mark.parametrize(("value", "column", "reason"), [
    ("2 Feb 1940", "diagnosis", "misplaced_date"),
    ("19781225", "serial_no", "misplaced_date"),
    ("7739353318R", "ward", "cell_id_shape"),  # a case number the header talks the model out of
    ("535856", "ward", "cell_id_shape"),
    ("2 Feb 1940", "procedure_date", None),  # a date column: the model may drop it
    ("2 Feb 1940", "DOB", None),
    ("2 Feb 1940", None, None),  # free text keeps its context
    ("10 mg", "dose", None),
    ("Ward 64 Bed 18", "ward", None),  # four digits: not ID-shaped
])
def test_context_free_cells_are_never_dropped(value, column, reason):
    fake = Fake(script({"none": 0.99}))
    r = J.Judge(fake).judge(value, J.Candidate(1, len(value)), column=column)
    assert (r["decision"] == "review") is (reason is not None)
    if reason:
        assert reason in r["judge"]["reasons"]
    # the rule only blocks a drop: a confident identifier in the same cell is still accepted
    label = "dob" if J.family_of(value) in ("date", "numeric") else "case_visit"
    sure = Fake(script({label: 0.97, "none": 0.0}))
    r = J.Judge(sure).judge(value, J.Candidate(1, len(value)), column=column)
    assert r["decision"] == "identifier"
    # part of a cell has context around it: the rule does not apply
    t = f"seen {value} today"
    r = J.Judge(fake).judge(t, J.Candidate(6, 5 + len(value)), column=column)
    assert r["decision"] == "not_identifier"


def test_column_outliers_are_never_dropped():
    # a DOB in a procedure-date column: the model may drop a date there, the column check may not
    fake = Fake(script({"none": 0.99}))
    r = J.Judge(fake).judge("19830112", J.Candidate(1, 8), column="procedure_date")
    assert r["decision"] == "not_identifier"
    r = J.Judge(fake).judge("19830112", J.Candidate(1, 8), column="procedure_date",
                            column_outlier=True)
    assert r["decision"] == "review" and "column_outlier" in r["judge"]["reasons"]
    # free text has no column: the flag does not apply
    t = "seen 19830112 today"
    r = J.Judge(fake).judge(t, J.Candidate(6, 13), column_outlier=True)
    assert r["decision"] == "not_identifier"


# --- calibrated decisions --------------------------------------------------------------------


def test_decisions_use_the_calibrated_probability(tmp_path):
    from slmjev import calibrate
    fake = Fake(script({"none": 0.10, "mrn": 0.90}))
    raw = J.Judge(fake).judge(TEXT, cand("S1234567A"))
    assert raw["decision"] == "identifier"
    path = tmp_path / "cal.json"
    calibrate.save(path, calibrate.Temperature(4.0), {"drop_below": 0.02, "accept_at": 0.9},
                   {"prompt": J.PROMPT_VERSION, "model": "C:/m/Qwen3-1.7B-Q4_K_M.gguf"})
    j = J.Judge.calibrated(Fake(script({"none": 0.10, "mrn": 0.90})), path,
                           model="D:/other/Qwen3-1.7B-Q4_K_M.gguf")  # same file, moved
    r = j.judge(TEXT, cand("S1234567A"))
    assert r["p_identifier"] == pytest.approx(0.9, abs=1e-3)
    assert r["confidence"] < 0.9 and r["decision"] == "review"  # softened below accept_at
    assert j.thresholds.drop_below == 0.02 and j.thresholds.accept_at == 0.9


@pytest.mark.parametrize(("meta", "model", "err"), [
    ({}, None, "prompt version"),
    ({"prompt": J.PROMPT_VERSION - 1, "model": "q.gguf"}, None, "prompt version"),
    ({"prompt": J.PROMPT_VERSION, "model": "q.gguf"}, "other.gguf", "model"),
    ({"prompt": J.PROMPT_VERSION}, "q.gguf", "model"),
])
def test_calibration_for_another_prompt_or_model_is_refused(tmp_path, meta, model, err):
    from slmjev import calibrate
    path = tmp_path / "cal.json"
    calibrate.save(path, calibrate.Temperature(2.0), {"drop_below": 0.02, "accept_at": 0.9}, meta)
    with pytest.raises(ValueError, match=err):
        J.Judge.calibrated(Fake(script({"none": 0.9})), path, model=model)


def test_judge_output_resolves_against_a_policy():
    made_up = policy.validate({"rules": {
        "date": {"by": "date_role", "cases": {"dob": {"action": "remove"},
                                              "other": {"action": "retain"}},
                 "unknown": {"action": "flag", "options": ["remove", "retain"]}}}})
    j = J.Judge(Fake(script({"dob": 0.9, "none": 0.05}, noul=0.95)))
    r = j.judge(TEXT, cand("12/05/1950", type="date"))
    assert policy.resolve(r, made_up, r["context"])["action"] == "remove"
    j = J.Judge(Fake(script({"none": 0.97}, noul=0.03)))
    r = j.judge(TEXT, cand("03/02/2025", type="date"))
    assert policy.resolve(r, made_up, r["context"])["action"] == "retain"


# --- transport -------------------------------------------------------------------------------


def test_llamaserver_refuses_non_loopback():
    with pytest.raises(NetworkForbidden):
        J.LlamaServer("http://10.0.0.5:8089")
    with pytest.raises(NetworkForbidden):
        J.LlamaServer("https://api.example.com")
    J.LlamaServer("http://127.0.0.1:8089")
    J.LlamaServer("http://localhost:8089")


class _Stub(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.seen.append((self.headers.get("Authorization"), body))
        out = {"choices": [{"logprobs": {"content": [{"top_logprobs": [
            {"token": "A", "logprob": -0.1}, {"token": " B", "logprob": -2.4}]}]}}],
            "timings": {"prompt_n": 7, "cache_n": 93}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_llamaserver_reads_first_token_logprobs():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        b = J.LlamaServer(f"http://127.0.0.1:{srv.server_port}", key="k")
        dist = b.first_token("Text:\nX\n\n", "Q?\nA) yes\nB) no\nAnswer:")
        auth, body = _Stub.seen[-1]
        assert auth == "Bearer k"
        assert body["max_tokens"] == 1 and body["logprobs"] is True and body["cache_prompt"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["messages"][1]["content"].startswith("Text:\nX\n\nQ?")
        assert J.letter_mass(dist, "AB")["A"] == pytest.approx(0.9048, abs=1e-3)
        assert b.calls[-1]["cache_n"] == 93
    finally:
        srv.shutdown()


def test_llamaserver_unreachable_is_a_judge_error():
    b = J.LlamaServer("http://127.0.0.1:9", timeout=2)
    with pytest.raises(J.JudgeError):
        b.first_token("", "Q")
