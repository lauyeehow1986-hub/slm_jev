import importlib.util
import json
import sys
from pathlib import Path

import pytest

from slmjev import calibrate
from slmjev import judge as J

_EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(_EVAL))
_spec = importlib.util.spec_from_file_location("fit_bench_calibration",
                                               _EVAL / "fit_bench_calibration.py")
fb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fb)

# synthetic: an invented name, an SHI mention the judge is overconfident about, and a decoy
TEXT = "Tan Ah Kow reports HIV worry. Ward 5 bed 12."


def _rec(sub, cat, p):
    i = TEXT.index(sub)
    return {"start": i + 1, "end": i + len(sub), "match": sub, "p_identifier": p,
            "confidence": round(p, 4), "category": cat,
            "category_probs": {cat: p, "none": round(1 - p, 6)} if cat != "none"
            else {"none": 1 - p, "name": p},
            "decision": "not_identifier" if p < 0.05 else "identifier",
            "fast_path": None, "sources": ["rules"]}


def _report(tmp_path, name, n=45):
    docs, judged = [], []
    for i in range(n):
        spans = [{"start": 1, "end": 10, "match": "Tan Ah Kow", "label": "name"}]
        if i % 3 == 0:  # the SHI call is right a third of the time
            spans.append({"start": 20, "end": 22, "match": "HIV", "label": "hiv_sti"})
        docs.append({"id": f"n{i}", "text": TEXT, "spans": spans})
        judged.append([_rec("Tan Ah Kow", "name", 0.99), _rec("HIV worry", "hiv_sti", 0.99),
                       _rec("Ward 5", "none", 0.01)])
    (tmp_path / f"{name}.json").write_text(json.dumps({"docs": docs}), encoding="utf-8")
    path = tmp_path / f"rep_{name}.json"
    path.write_text(json.dumps({"set": str(tmp_path / f"{name}.json"),
                                "judged": {fb.SYSTEM: judged}, "results": {fb.SYSTEM: {}}}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("decide_on", ["raw", "calibrated"])
def test_fit_softens_the_overconfident_group(tmp_path, decide_on):
    base = tmp_path / "base.json"
    calibrate.save(base, calibrate.Identity(), {"drop_below": 0.05, "accept_at": 0.9},
                   {"prompt": J.PROMPT_VERSION, "model": "q.gguf"})
    out, rep = tmp_path / "cal.json", tmp_path / "numbers.json"
    fb.main(["--train", str(_report(tmp_path, "a")), "--test", str(_report(tmp_path, "b")),
             "--base", str(base), "--out", str(out), "--report", str(rep), "--min-n", "20",
             "--decide-on", decide_on])
    cal, th, meta = calibrate.load(out)
    assert isinstance(cal, calibrate.Grouped) and meta["prompt"] == J.PROMPT_VERSION
    assert cal(0.99, "shi_other") == pytest.approx(1 / 3, abs=0.02)
    assert cal(0.99, "identifier") > 0.98
    assert th["space"] == decide_on
    numbers = json.loads(rep.read_text(encoding="utf-8"))["sets"]["b"]
    assert numbers["split"] == "test"
    assert numbers["base"]["ece"] > 0.2 > numbers["new"]["ece"]
    assert numbers["new"]["recall"] == numbers["base"]["recall"] == 1.0
    if decide_on == "raw":  # the decisions are untouched; only the confidence moved
        assert numbers["new"]["decisions"] == numbers["base"]["decisions"]
    else:  # the SHI calls are no longer auto-accepted
        assert numbers["new"]["decisions"]["review"] == 45


def test_the_judge_decides_raw_thresholds_on_p_identifier(tmp_path):
    path = tmp_path / "cal.json"
    cal = calibrate.Grouped((("identifier", calibrate.Temperature(8.0)),), calibrate.Identity())
    calibrate.save(path, cal, {"drop_below": 0.05, "accept_at": 0.9, "space": "raw"},
                   {"prompt": J.PROMPT_VERSION})
    from test_judge import TEXT, Fake, cand, script
    j = J.Judge.calibrated(Fake(script({"none": 0.02, "mrn": 0.98})), path)
    r = j.judge(TEXT, cand("S1234567A"))
    assert r["confidence"] < 0.7 and r["decision"] == "identifier"  # raw 0.98 >= accept_at
    with pytest.raises(ValueError, match="space"):
        J.Thresholds(space="logit")


def test_a_replay_that_disagrees_with_the_report_stops_the_fit(tmp_path):
    base = tmp_path / "base.json"
    calibrate.save(base, calibrate.Temperature(3.0), {"drop_below": 0.05, "accept_at": 0.9},
                   {"prompt": J.PROMPT_VERSION})
    with pytest.raises(SystemExit, match="replaying"):
        fb.main(["--train", str(_report(tmp_path, "a")), "--base", str(base),
                 "--out", str(tmp_path / "cal.json")])
