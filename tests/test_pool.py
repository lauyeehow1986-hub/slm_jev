import importlib.util
import sys
from pathlib import Path

import pytest

_EVAL = Path(__file__).resolve().parents[1] / "eval"
sys.path.insert(0, str(_EVAL))
_spec = importlib.util.spec_from_file_location("pool", _EVAL / "pool.py")
pool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pool)


def _set(name, gold, missed, gold_direct, missed_direct, tp, fp, notes, secs=60.0, chars=1000):
    return {"set": name, "notes": len(notes), "chars": chars, "gold": gold, "missed": missed,
            "gold_direct": gold_direct, "missed_direct": missed_direct, "tp": tp, "fp": fp,
            "fp_on_negative_notes": 0, "secs": secs, "p95_per_note": 90.0, "candidates": notes}


def test_wilson_matches_the_textbook_interval():
    lo, hi = pool.wilson(249, 255)
    assert lo == pytest.approx(0.9496, abs=1e-4) and hi == pytest.approx(0.9892, abs=1e-4)
    assert pool.wilson(0, 0) is None
    assert pool.wilson(10, 10)[1] == 1.0


def test_pooled_counts_are_summed_not_averaged():
    # two sets: 99 of 100 and 48 of 50 direct; the pooled rate is 147/150, not the mean of rates
    a = _set("a", 120, 1, 100, 1, 95, 5, [[(0.9, True), (0.1, False)]])
    b = _set("b", 60, 2, 50, 2, 45, 5, [[(0.9, True)]])
    s = pool.summary([a, b], reps=50)
    assert (s["direct_hits"], s["direct_gold"]) == (147, 150)
    assert s["direct_recall"] == pytest.approx(0.98)
    assert s["precision"] == pytest.approx(140 / 150, abs=1e-4)
    assert s["candidates"] == 3
    assert s["secs_per_1k_chars"] == 60.0


def test_the_verdict_fails_on_any_check_and_is_never_pass_without_data():
    good = _set("a", 100, 0, 100, 1, 95, 5, [[(0.95, True)] * 19 + [(0.05, False)]])
    v = pool.verdict(pool.summary([good], reps=20))
    assert v["overall"] == "PASS"
    low = _set("a", 100, 0, 100, 3, 95, 5, [[(0.95, True)]])
    assert pool.verdict(pool.summary([low], reps=20))["direct_recall"] == "FAIL"
    untimed = _set("a", 100, 0, 100, 1, 95, 5, [[(0.95, True)]], secs=None)
    v = pool.verdict(pool.summary([untimed], reps=20))
    assert v["latency"] == "INCOMPLETE" and v["overall"] == "INCOMPLETE"
