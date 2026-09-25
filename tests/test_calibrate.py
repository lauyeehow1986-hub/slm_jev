import json
import random

import pytest

from slmjev import calibrate


def _overconfident(n=400, seed=0):
    """True rate q, reported as a sharper p (the zero-shot judge's failure mode)."""
    rng = random.Random(seed)
    p, y = [], []
    for _ in range(n):
        q = rng.random()
        p.append(calibrate._sigmoid(3 * calibrate._logit(q)))
        y.append(rng.random() < q)
    return p, y


def test_temperature_softens_overconfident_scores():
    p, y = _overconfident()
    t = calibrate.Temperature.fit(p, y)
    assert 2.0 < t.t < 4.5
    assert calibrate.nll([t(v) for v in p], y) < calibrate.nll(p, y)
    assert calibrate.ece([t(v) for v in p], y) < calibrate.ece(p, y)


def test_temperature_is_monotone_and_keeps_half():
    t = calibrate.Temperature(3.0)
    xs = [0.001, 0.1, 0.4, 0.5, 0.6, 0.9, 0.999]
    assert [t(x) for x in xs] == sorted(t(x) for x in xs)
    assert t(0.5) == pytest.approx(0.5)


def test_isotonic_is_monotone_floored_and_fits_steps():
    p = [0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9]
    y = [0, 0, 1, 0, 1, 1, 1, 1]
    iso = calibrate.Isotonic.fit(p, y)
    out = [iso(x) for x in [0.0, *p, 1.0]]
    assert out == sorted(out)
    assert iso(0.0) == 0.005 and iso(1.0) == 0.995  # no hard 0 or 1
    assert iso(0.3) == iso(0.4) == pytest.approx(0.5)  # the pooled violator block


def test_fit_dispatch_and_round_trip(tmp_path):
    p, y = _overconfident(200)
    for kind in ("identity", "temperature", "isotonic"):
        cal = calibrate.fit(kind, p, y)
        back = calibrate.from_dict(json.loads(json.dumps(cal.to_dict())))
        assert [back(v) for v in p] == pytest.approx([cal(v) for v in p])
    with pytest.raises(ValueError):
        calibrate.fit("platt", p, y)
    with pytest.raises(ValueError):
        calibrate.from_dict({"kind": "isotonic", "xs": [0.5, 0.2], "ys": [0.1, 0.9]})


def test_save_load_validates_thresholds(tmp_path):
    path = tmp_path / "cal.json"
    calibrate.save(path, calibrate.Temperature(2.0), {"drop_below": 0.1, "accept_at": 0.9},
                   {"n": 3})
    cal, th, meta = calibrate.load(path)
    assert cal == calibrate.Temperature(2.0) and th["accept_at"] == 0.9 and meta == {"n": 3}
    calibrate.save(path, calibrate.Identity(), {"drop_below": 0.9, "accept_at": 0.1}, {})
    with pytest.raises(ValueError, match="drop_below <= accept_at"):
        calibrate.load(path)
    path.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a"):
        calibrate.load(path)


def test_choose_thresholds_meets_targets_on_the_fitting_data():
    p, y = _overconfident(1000, seed=3)
    ch = calibrate.choose_thresholds(p, y, recall_target=0.98, precision_target=0.95)
    pos = [v for v, t in zip(p, y, strict=True) if t]
    assert sum(v >= ch.drop_below for v in pos) / len(pos) >= 0.98
    acc = [t for v, t in zip(p, y, strict=True) if v >= ch.accept_at]
    assert len(acc) >= 20 and sum(acc) / len(acc) >= 0.95
    assert 0 <= ch.drop_below <= 0.5 <= ch.accept_at <= 1


def test_choose_thresholds_caps_drop_and_floors_accept():
    # perfectly separated: a greedy search would drop below 0.9 and accept at 0.9
    p = [0.1] * 50 + [0.9] * 50
    y = [0] * 50 + [1] * 50
    ch = calibrate.choose_thresholds(p, y)
    assert ch.drop_below <= 0.5 and ch.accept_at == 0.9 and ch.precision_at_accept == 1.0


def test_choose_thresholds_fails_closed():
    few = calibrate.choose_thresholds([0.9] * 5, [1] * 5)
    assert (few.drop_below, few.accept_at) == (0.0, 1.0) and few.notes
    # all positives and negatives interleaved: no cut reaches 98 % precision
    p = [i / 100 for i in range(100)]
    y = [i % 2 for i in range(100)]
    ch = calibrate.choose_thresholds(p, y)
    assert ch.accept_at == 1.0 and ch.precision_at_accept is None
    assert any("precision" in n for n in ch.notes)
