import io
import json
import re

import pytest

from slmjev import calibrate, engine, netguard
from slmjev import judge as J

_KEY = ({v: k for k, v in J.OPTION_TEXT.items()} | {v: "none" for v in J.NONE_TEXT.values()})
_OPT = re.compile(r"^([A-X])\) (.*)$")
_SPAN = re.compile(re.escape(J.CHOICE_STEM).replace(re.escape("{span}"), "(.*)"))


class SpanFake:
    """Answers the Choice question from ``answers[span] = {option key: prob}`` (default: ``none``
    with 0.99); leftover mass is spread over the other options shown."""

    def __init__(self, answers=None, default=None, fail=False):
        self.answers, self.fail = answers or {}, fail
        self.default = default or {"none": 0.99}
        self.spans = []

    def first_token(self, prefix, question):
        span = _SPAN.match(question.split("\n", 1)[0])[1]
        self.spans.append((prefix, span))
        if self.fail:
            raise J.JudgeError("boom")
        shown = [(m[1], _KEY[m[2]]) for line in question.split("\n") if (m := _OPT.match(line))]
        want = self.answers.get(span, self.default)
        rest = [k for _, k in shown if k not in want]
        left = max(0.0, 1.0 - sum(want.get(k, 0) for _, k in shown))
        return {letter: want.get(k, left / len(rest) if rest else 0.0) for letter, k in shown}


def judge(fake, drop=0.05, accept=0.9):
    th = J.Thresholds(drop_below=drop, accept_at=accept)
    return J.Judge(fake, thresholds=th, calibrator=calibrate.Identity(), **engine.PROD)


NOTE = "Pt Tan Ah Kow, NRIC S1234567D, seen for cough. BP 120/80."


def test_scan_accepts_drops_and_fast_paths():
    fake = SpanFake({"Tan Ah Kow": {"name": 0.99}})
    out = engine.run({"texts": [NOTE]}, judge(fake))
    got = {r["match"]: r for r in out}
    assert set(got) == {"Tan Ah Kow", "S1234567D"}
    name, nric = got["Tan Ah Kow"], got["S1234567D"]
    assert name["identifier"] == "name" and name["decision"] == "identifier"
    assert not name["needs_review"] and name["detector"] == "slm:jev" and name["row"] == 1
    assert "shape:name" in name["sources"]
    assert nric["identifier"] == "national_id" and nric["fast_path"] == "nric_checksum"
    assert "S1234567D" not in [s for _, s in fake.spans]  # rule-certain: never asked
    assert NOTE[name["start"] - 1:name["end"]] == "Tan Ah Kow"  # 1-based, end inclusive



def test_the_model_vocabulary_turns_on_the_token_sweep(tmp_path):
    text = "B/O Sharmila delivered at 0412h."
    fake = SpanFake({"Sharmila": {"name": 0.99}})
    assert engine.scan_text(judge(fake), text) == []
    got = engine.run({"texts": [text]}, judge(fake), vocab=engine.model_vocab(None))
    assert [(r["match"], r["sources"]) for r in got] == [("Sharmila", ["shape:token"])]
    assert engine.model_vocab(str(tmp_path / "none.gguf")) == frozenset()

def test_candidates_inside_a_rule_certain_span_are_not_asked():
    text = "Email tan.ah.kow@example.com today"
    s = text.index("tan") + 1
    fake = SpanFake()
    out = engine.run({"texts": [text], "candidates": [[{"start": s, "end": s + 2}]]},
                     judge(fake))
    assert [r["match"] for r in out] == ["tan.ah.kow@example.com"]
    assert "tan" not in [sp for _, sp in fake.spans]


def test_failed_judgments_go_to_review_with_a_label():
    out = engine.run({"texts": [NOTE]}, judge(SpanFake(fail=True)))
    rev = {r["match"]: r for r in out if r["decision"] == "review"}
    assert "Tan Ah Kow" in rev and rev["Tan Ah Kow"]["needs_review"]
    assert rev["Tan Ah Kow"]["identifier"] == "name"  # from the proposer's type hint
    assert rev["Tan Ah Kow"]["confidence"] is None and rev["Tan Ah Kow"]["errors"]
    assert any(r["match"] == "S1234567D" and r["decision"] == "identifier" for r in out)


def test_unsure_none_names_the_best_label():
    fake = SpanFake({"Tan Ah Kow": {"none": 0.5, "name": 0.3, "mrn": 0.1}})
    rec = next(r for r in engine.run({"texts": [NOTE]}, judge(fake))
               if r["match"] == "Tan Ah Kow")
    assert rec["decision"] == "review" and rec["category"] == "none"
    assert rec["identifier"] == "name"


def test_include_dropped_and_empty_rows():
    fake = SpanFake({"Tan Ah Kow": {"name": 0.99}})
    req = {"texts": [None, "", NOTE], "candidates": [None, None, [{"start": 1, "end": 2}]]}
    assert not any(r["match"] == "Pt" for r in engine.run(req, judge(fake)))
    out = engine.run({**req, "include_dropped": True}, judge(fake))
    assert {r["row"] for r in out} == {3}
    assert [r["match"] for r in out if r["decision"] == "not_identifier"] == ["Pt"]
    assert engine.run({"texts": [None, "  "]}, judge(fake)) == []


def rec(s, e, decision):
    return {"start": s, "end": e, "decision": decision}


def test_resolve_by_containment_and_rank():
    out = engine.resolve([rec(1, 20, "identifier"), rec(5, 9, "identifier"),
                          rec(3, 6, "review"), rec(18, 30, "review"), rec(25, 28, "review"),
                          rec(40, 50, "review"), rec(42, 45, "identifier")])
    assert [(r["start"], r["end"]) for r in out] == [(1, 20), (18, 30), (40, 50), (42, 45)]


def test_cells_use_the_column_and_its_outliers():
    dates = [f"2024-0{m}-1{d}" for m in range(1, 7) for d in range(3)]
    texts = [*dates, "1952-02-02"]
    fake = SpanFake(default={"none": 0.999})
    out = engine.run({"texts": texts, "kind": "cells", "column": "procedure_date"}, judge(fake))
    assert [(r["row"], r["match"], r["reasons"]) for r in out] == [
        (len(texts), "1952-02-02", ["column_outlier"])]
    assert all(p.startswith("Column: procedure_date\n") for p, _ in fake.spans)
    fake2 = SpanFake()
    engine.run({"texts": [NOTE], "column": "notes"}, judge(fake2))
    assert not any(p.startswith("Column:") for p, _ in fake2.spans)  # free text: no header


@pytest.mark.parametrize("req, msg", [
    ({}, "texts"), ({"texts": ["a"], "kind": "pdf"}, "kind"),
    ({"texts": ["a", "b"], "candidates": [[]]}, "one entry per text"),
])
def test_bad_requests(req, msg):
    with pytest.raises(ValueError, match=msg):
        engine.run(req, judge(SpanFake()))


@pytest.fixture
def io_env(monkeypatch, tmp_path):
    cal = tmp_path / "cal.json"
    calibrate.save(cal, calibrate.Identity(), {"drop_below": 0.05, "accept_at": 0.9},
                   {"prompt": J.PROMPT_VERSION, "model": "judge.gguf"})
    monkeypatch.setenv(engine.ENV_CALIBRATION, str(cal))
    monkeypatch.setenv(engine.ENV_URL, "http://127.0.0.1:9")
    fake = SpanFake({"Tan Ah Kow": {"name": 0.99}})
    monkeypatch.setattr(engine.J, "LlamaServer", lambda url, key: fake)
    yield monkeypatch
    netguard.allow_network()


def _stdin(monkeypatch, payload: bytes):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(payload)))


def test_main_round_trip(io_env, capsysbinary):
    _stdin(io_env, json.dumps({"texts": [NOTE]}).encode("utf-8"))
    assert engine.main([]) == 0
    out = json.loads(capsysbinary.readouterr().out.decode("utf-8"))
    assert {r["match"] for r in out} == {"Tan Ah Kow", "S1234567D"}
    assert set(engine._FIELDS) <= set(out[0])


def test_main_forbids_the_network_and_fails_loudly(io_env, capsysbinary):
    _stdin(io_env, b"{not json")
    assert engine.main([]) == 2
    assert "JSONDecodeError" in capsysbinary.readouterr().err.decode()
    with pytest.raises(netguard.NetworkForbidden):
        netguard._guard(("8.8.8.8", 53))
    _stdin(io_env, json.dumps({"texts": "x"}).encode())
    assert engine.main([]) == 2  # a bad request is an error, never an empty result


def test_probe_reports_missing_files(monkeypatch, capsys):
    for v in (engine.ENV_URL, engine.server.ENV_BIN, engine.server.ENV_MODEL):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(engine.ENV_CALIBRATION, "no/such/file.json")
    try:
        assert engine.main(["--probe"]) == 0
    finally:
        netguard.allow_network()
    p = json.loads(capsys.readouterr().out)
    assert p["ok"] is False and p["detector"] == "slm:jev" and not any(p["found"].values())


def test_records_follow_the_span_schema():
    from pathlib import Path

    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "span.v1.json")
                        .read_text(encoding="utf-8"))
    fake = SpanFake({"Tan Ah Kow": {"none": 0.5, "name": 0.4}})
    out = engine.run({"texts": [NOTE], "include_dropped": True}, judge(fake))
    assert out
    for r in out:
        assert set(schema["required"]) <= set(r)
        assert r["detector"] == schema["properties"]["detector"]["const"]
        assert r["decision"] in schema["properties"]["decision"]["enum"]
        assert 1 <= r["start"] <= r["end"] and NOTE[r["start"] - 1:r["end"]] == r["match"]


def test_settings_prefer_the_request_over_the_environment(monkeypatch):
    monkeypatch.setenv(engine.server.ENV_MODEL, "env.gguf")
    monkeypatch.setenv(engine.ENV_THREADS, "4")
    monkeypatch.delenv(engine.ENV_CALIBRATION, raising=False)
    cfg = engine.settings({"model": "req.gguf"})
    assert cfg["model"] == "req.gguf" and cfg["threads"] == 4
    assert cfg["calibration"] == str(engine.DEFAULT_CALIBRATION)


def test_scan_checks_the_request_before_starting_anything(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("server started for a bad request")

    monkeypatch.setattr(engine.server, "start", boom)
    monkeypatch.delenv(engine.ENV_URL, raising=False)
    try:
        with pytest.raises(ValueError, match="texts"):
            engine.scan({"texts": "not a list"})
    finally:
        netguard.allow_network()
