import importlib.util
from pathlib import Path

from slmjev import calibrate, engine, synth
from slmjev import judge as J
from slmjev.labels import load_labels

_spec = importlib.util.spec_from_file_location(
    "e2e_eval", Path(__file__).resolve().parents[1] / "eval" / "e2e_eval.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


def _doc(text, spans):
    return {"id": "d", "kind": "note", "column": None, "text": text, "decoys": [],
            "spans": [{"start": text.index(m) + 1, "end": text.index(m) + len(m), "match": m,
                       "label": lab} for m, lab in spans]}


def _rec(text, m, decision, ident):
    s = text.index(m) + 1
    return {"start": s, "end": s + len(m) - 1, "match": m, "decision": decision,
            "identifier": ident, "sources": ["x"], "reasons": [], "confidence": 0.5}


def test_score_counts_coverage_partials_and_false_positives():
    text = "Mr Tan Ah Kow, tel 6123-4567, Ward 5."
    doc = _doc(text, [("Tan Ah Kow", "name"), ("6123-4567", "phone")])
    recs = [_rec(text, "Mr Tan Ah Kow", "identifier", "name"),  # covers a gold span
            _rec(text, "6123", "review", "phone"),  # only part of a gold span
            _rec(text, "Ward 5", "review", "other_id"),  # overlaps no gold
            _rec(text, "tel", "not_identifier", None)]
    s = e2e.score_doc(doc, recs)
    name, phone = s["gold"]
    assert name["flagged"] and name["auto"] and name["label_ok"]
    assert not phone["flagged"] and phone["partial"]
    assert [r["gold"] for r in s["returned"]] == [True, True, False] and s["n_dropped"] == 1
    summ = e2e.summarize([doc], [s], [1.0], load_labels()["identifiers"])
    assert summ["recall_flagged"] == 0.5 and summ["precision_flagged"] == round(2 / 3, 4)
    assert summ["false_accepts"] == 0 and summ["false_review"] == 1 and summ["partial"] == 1
    assert [m["match"] for m in summ["misses"]] == ["6123-4567"]


def test_covered_ignores_punctuation_at_the_edges():
    text = "addr: (Blk 5 Lorong 3 Geylang)"
    g = {"start": text.index("(") + 1, "end": len(text)}
    r = {"start": text.index("Blk") + 1, "end": len(text) - 1}
    assert e2e.covers(r, text, g)


class NoneFake:
    """Says ``none`` to everything: only the rules' fast path accepts anything."""

    def first_token(self, prefix, question):
        letters = [ln[0] for ln in question.split("\n") if ln[1:3] == ") "]
        return {letter: 0.99 if i == len(letters) - 1 else 0.01 / len(letters)
                for i, letter in enumerate(letters)}


def test_scan_doc_runs_the_engine_on_synthetic_docs():
    th = J.Thresholds(drop_below=0.05, accept_at=0.9)
    judge = J.Judge(NoneFake(), thresholds=th, calibrator=calibrate.Identity(), **engine.PROD)
    docs = synth.generate("dev", n_notes=3, n_cells=3, seed=5)
    scored = [e2e.score_doc(d, e2e.scan_doc(judge, d)) for d in docs]
    summ = e2e.summarize(docs, scored, [0.1] * len(docs), load_labels()["identifiers"])
    assert summ["gold"] == sum(len(d["spans"]) for d in docs)
    assert summ["per_label"]["national_id"]["recall_auto"] == 1.0  # NRIC checksum fast path
    assert summ["false_accepts"] == 0
