"""Tests for the judge finetune data (slmjev.sft) and its builder."""

import random
import sys
from collections import Counter
from pathlib import Path

from slmjev import judge as J
from slmjev import sft, synth

ROOT = Path(__file__).resolve().parents[1]


class Recorder:
    """A backend that records every prompt the judge sends and always answers A."""

    def __init__(self):
        self.prompts = []

    def first_token(self, prefix, question):
        self.prompts.append(prefix + question)
        return {"A": 1.0}


def _docs():
    return (synth.generate("dev", n_notes=6, n_cells=10, seed=5)
            + synth.generate_labs("dev", 6, seed=5) + synth.generate_hard("dev", 4, seed=5))


def test_every_training_prompt_is_one_the_judge_sends():
    for doc in _docs():
        rows = sft.doc_examples(doc, random.Random(0), k=3)
        rec = Recorder()
        j = J.Judge(rec, choice_rotations=None)  # every rotation, so every order is sent
        for cand, _ in sft.oracle_candidates(doc):
            j.judge(doc["text"], cand, doc.get("column"))
        sent = set(rec.prompts)
        for r in rows:
            assert r["messages"][0]["content"] == J.SYSTEM
            assert r["messages"][1]["content"] in sent, r["doc"]


def test_answer_letter_labels_the_gold_option():
    doc = synth.generate("train", n_notes=3, n_cells=0, seed=2)[0]
    for r in sft.doc_examples(doc, random.Random(1), k=4):
        user = r["messages"][1]["content"]
        line = next(x for x in user.split("\n") if x.startswith(r["answer"] + ") "))
        text = line[3:]
        want = (J.NONE_TEXT[r["family"]] if r["label"] == "none" else J.OPTION_TEXT[r["label"]])
        assert text == want
        assert r["messages"][2]["content"] == r["answer"]
        assert r["n_options"] == len(J.FAMILIES[r["family"]])


def test_rotations_are_distinct_and_fast_path_spans_are_skipped():
    stats = Counter()
    docs = synth.generate("dev", n_notes=10, n_cells=0, seed=8)
    rows = [r for d in docs for r in sft.doc_examples(d, random.Random(2), k=2, stats=stats)]
    by_cand = Counter((r["doc"], r["start"]) for r in rows)
    assert set(by_cand.values()) <= {1, 2}
    for (doc_id, start), n in by_cand.items():
        users = {r["messages"][1]["content"] for r in rows
                 if (r["doc"], r["start"]) == (doc_id, start)}
        assert len(users) == n
    assert stats["skip_fast_path"] > 0
    fast = {(d["id"], s["start"]) for d in docs for s in d["spans"]
            if J.rule_certain(d["text"], J.Candidate(s["start"], s["end"]))}
    assert fast and not fast & set(by_cand)


def test_build_excludes_held_out_texts_and_is_deterministic():
    docs = _docs()
    held = {docs[0]["text"], docs[-1]["text"]}
    rows, stats = sft.build(docs, seed=3, exclude_texts=held)
    assert stats["skip_excluded_doc"] == 2
    assert not {r["doc"] for r in rows} & {docs[0]["id"], docs[-1]["id"]}
    again, _ = sft.build(docs, seed=3, exclude_texts=held)
    assert rows == again


def test_eval_uses_the_same_oracle_proposer():
    sys.path.insert(0, str(ROOT / "eval"))
    try:
        import judge_eval
    finally:
        sys.path.pop(0)
    assert judge_eval.candidates is sft.oracle_candidates


def test_builder_never_trains_on_an_eval_or_calibration_seed():
    sys.path.insert(0, str(ROOT / "finetune"))
    try:
        import build_data
    finally:
        sys.path.pop(0)
    held = {(kind, split, seed) for kind, split, _, seed in build_data.HELD_OUT}
    used = {(kind, split, seed) for kind, split, _, seed in build_data.TRAIN + build_data.VAL}
    assert not held & used
    assert all(split != "test" for _, split, _, _ in build_data.TRAIN + build_data.VAL)
