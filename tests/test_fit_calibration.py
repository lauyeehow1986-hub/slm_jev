import importlib.util
import json
import random
from pathlib import Path

import pytest

from slmjev import calibrate
from slmjev import judge as J

_spec = importlib.util.spec_from_file_location(
    "fit_calibration", Path(__file__).resolve().parents[1] / "eval" / "fit_calibration.py")
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)


def _row(doc, p, is_pii, label="mrn", fast=False, failed=False):
    cat = label if is_pii else "none"
    rec = {"p_identifier": None if failed else p, "match": "x", "decision": "review",
           "category_probs": {} if failed else {cat: 0.9, "none" if is_pii else "mrn": 0.1},
           "judge": {"reasons": []}}
    if fast:
        rec.update(p_identifier=0.99, decision="identifier", category_probs={label: 1.0})
        rec["judge"]["fast_path"] = "nric_checksum"
    return {"doc": f"d{doc}", "gold": {"label": label if is_pii else "none", "is_pii": is_pii},
            "rec": rec}


def _rows(n=300, seed=0):
    """Overconfident model rows over 30 docs, plus a fast-path and a failed row."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        q = rng.random()
        rows.append(_row(i % 30, calibrate._sigmoid(3 * calibrate._logit(q)), rng.random() < q))
    rows.append(_row(0, None, True, "national_id", fast=True))
    rows.append(_row(1, None, True, failed=True))
    return rows


def test_folds_split_by_document():
    rows = _rows()
    fold = fc.folds(fc.model_rows(rows), 5)
    by_doc = {}
    for r, f in zip(fc.model_rows(rows), fold, strict=True):
        assert by_doc.setdefault(r["doc"], f) == f
    assert set(fold) == set(range(5))
    with pytest.raises(ValueError):
        fc.folds(fc.model_rows(rows)[:3], 5)


def test_choose_kind_prefers_the_simpler_calibrator():
    assert fc.choose_kind({"identity": 0.30, "temperature": 0.298, "isotonic": 0.20}) == "isotonic"
    assert fc.choose_kind({"identity": 0.30, "temperature": 0.25, "isotonic": 0.248}) \
        == "temperature"


def test_fit_excludes_fast_path_and_failed_rows():
    rows = _rows()
    f = fc.fit(rows)
    assert f["n"] == 300
    assert f["kind"] in fc.KINDS and f["cv_nll"][f["kind"]] <= f["cv_nll"]["identity"]
    assert f["cv_nll"]["temperature"] < f["cv_nll"]["identity"]  # overconfidence is fixable


def test_replay_matches_the_judge_rule_and_fails_closed():
    rows = [_row(0, 0.01, False), _row(0, 0.5, True), _row(0, 0.95, True),
            _row(0, None, True, "national_id", fast=True), _row(0, None, True, failed=True)]
    th = J.Thresholds(drop_below=0.05, accept_at=0.8)
    assert fc.replay(rows, None, th) == ["not_identifier", "review", "identifier",
                                         "identifier", "review"]
    # a calibrator that pulls 0.95 below accept_at sends it to review
    assert fc.replay(rows, calibrate.Temperature(10.0), th)[2] == "review"


def test_main_writes_a_loadable_calibration(tmp_path):
    for name, seed in (("train", 1), ("test", 2)):
        doc = {"split": name, "prod": True, "model": "fake", "prompt": 2,
               "rows": _rows(seed=seed)}
        (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "models" / "calibration.json"
    assert fc.main(["--train", str(tmp_path / "train.json"), "--test",
                    str(tmp_path / "test.json"), "--out", str(out),
                    "--report", str(tmp_path / "report.json")]) == 0
    cal, th, meta = calibrate.load(out)
    assert meta["prompt"] == 2 and meta["model"] == "fake"
    assert 0 <= th["drop_below"] <= th["accept_at"] <= 1 and meta["n"] == 300
    rep = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert rep["test"]["calibrated"]["decisions"]["n"] == 302


def test_main_refuses_a_non_prod_or_wrong_split_report(tmp_path):
    doc = {"split": "dev", "prod": True, "rows": _rows()}
    for name in ("train", "test"):
        (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SystemExit):
        fc.main(["--train", str(tmp_path / "train.json"), "--test", str(tmp_path / "test.json"),
                 "--out", str(tmp_path / "c.json")])


@pytest.mark.parametrize(("tr", "te"), [(None, None), (1, 2), (2, None)])
def test_main_refuses_reports_from_different_prompts(tmp_path, tr, te):
    for name, prompt in (("train", tr), ("test", te)):
        doc = {"split": name, "prod": True, "model": "fake", "prompt": prompt, "rows": _rows()}
        (tmp_path / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SystemExit, match="prompt"):
        fc.main(["--train", str(tmp_path / "train.json"), "--test", str(tmp_path / "test.json"),
                 "--out", str(tmp_path / "c.json")])
