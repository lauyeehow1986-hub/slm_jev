import importlib.util
import random
from pathlib import Path

from slmjev import judge as J
from slmjev import synth
from slmjev.labels import load_labels

_spec = importlib.util.spec_from_file_location(
    "judge_eval", Path(__file__).resolve().parents[1] / "eval" / "judge_eval.py")
judge_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(judge_eval)


class Noisy:
    """Random letter distributions, with a timing record like LlamaServer's."""

    def __init__(self, seed=0):
        self.rng, self.calls = random.Random(seed), []

    def first_token(self, prefix, question):
        n = sum(1 for line in question.split("\n") if line[:1] in J.LETTERS and line[1:2] == ")")
        w = [self.rng.random() for _ in range(n)]
        self.calls.append({"secs": 0.01, "prompt_n": 5, "cache_n": 95})
        return {J.LETTERS[i]: v / sum(w) for i, v in enumerate(w)}


def test_summary_runs_end_to_end_on_a_fake_backend():
    docs = synth.generate("dev", n_notes=4, n_cells=3, seed=1)
    j = J.Judge(Noisy(), trace=True)
    rows = [r for d in docs for r in judge_eval.judge_doc(j, d)]
    s = judge_eval.summarize(rows, load_labels()["identifiers"])
    assert s["n"] == len(rows) == s["n_gold"] + s["n_decoy"]
    assert s["n_failed"] == 0 and 0 <= s["recall_flagged"] <= 1
    assert s["latency"]["cache_share"] == 0.95
    assert s["order_bias"]["n"] == len(rows)
    assert sum(v["n"] for v in s["per_label"].values()) == s["n_gold"]
