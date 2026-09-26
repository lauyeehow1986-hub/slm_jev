import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import bench  # noqa: E402

V = bench.VIEWS


def _doc(text, *spans, id="d"):
    out = []
    for match, label in spans:
        s = text.index(match) + 1
        out.append({"start": s, "end": s + len(match) - 1, "match": match, "label": label})
    return {"id": id, "text": text, "spans": out}


def _p(text, match, label="name", decision="identifier"):
    s = text.index(match) + 1
    return {"start": s, "end": s + len(match) - 1, "match": match, "label": label,
            "decision": decision}


def test_parse_markup_offsets_and_labels():
    src = ("# comment\n=== a | kind ===\nPt {{name|Tan Ah Kow}}, {{national_id|S1234567D}}.\n"
           "=== b | none ===\nNo PII here.\n")
    docs = bench.parse_markup(src)
    assert [d["id"] for d in docs] == ["a", "b"]
    a = docs[0]
    assert a["text"] == "Pt Tan Ah Kow, S1234567D."
    for g in a["spans"]:
        assert a["text"][g["start"] - 1:g["end"]] == g["match"]
    assert docs[1]["spans"] == []
    # a "# " comment inside a note is dropped; a line starting with a unit number is text
    inner = bench.parse_markup("=== c | k ===\nBlk 1\n#05-432\n# author's note\nend")
    assert inner[0]["text"] == "Blk 1\n#05-432\nend"
    with pytest.raises(ValueError):
        bench.parse_markup("=== a | k ===\n{{nonsense|x}}")


@pytest.mark.parametrize("path", ["eval/bench/sd20.json", "eval/bench/notes_v1.txt",
                                  "eval/bench/notes_v2.txt", "eval/bench/notes_v3.txt",
                                  "eval/bench/notes_v4.txt", "eval/bench/notes_v5.txt"])
def test_shipped_sets_load_with_exact_offsets(path):
    docs = bench.load_set(ROOT / path)
    assert docs and any(not d["spans"] for d in docs)  # negative controls are present


def test_overlap_recall_vs_redaction_coverage():
    t = "Seen Tan Ah Kow today"
    d = _doc(t, ("Tan Ah Kow", "name"))
    part = [[_p(t, "Tan")]]
    s = bench.score([d], part, V["identifiers"])
    assert s["recall"] == 1.0 and s["covered"] == 0.0  # overlap is not redaction
    both = [[_p(t, "Tan"), _p(t, "Ah Kow")]]
    assert bench.score([d], both, V["identifiers"])["covered"] == 1.0  # the union covers it


def test_precision_ignores_out_of_view_gold_and_shi():
    t = "Admitted 04/07/2023, known HIV, call 9123 4567"
    d = _doc(t, ("04/07/2023", "date_other"), ("HIV", "hiv_sti"), ("9123 4567", "phone"))
    preds = [[_p(t, "04/07/2023", "dob"), _p(t, "HIV", "hiv_sti"), _p(t, "9123 4567", "phone"),
              _p(t, "Admitted", "name")]]
    ids = bench.score([d], preds, V["identifiers"])
    assert (ids["gold"], ids["tp"], ids["fp"]) == (1, 1, 1)  # the date is neutral here
    dates = bench.score([d], preds, V["with_dates"])
    assert (dates["gold"], dates["tp"], dates["fp"]) == (2, 2, 1)
    shi = bench.score_shi([d], preds)
    assert shi == {"gold": 1, "recall": 1.0, "category_ok": 1.0, "precision": 1.0, "fp": 0}


def test_fp_on_negative_notes_and_auto_coverage():
    t = "Routine review, stable."
    neg = _doc(t, id="neg")
    t2 = "Call Mary Goh"
    pos = _doc(t2, ("Mary Goh", "name"))
    preds = [[_p(t, "Routine", "name")], [_p(t2, "Mary Goh", decision="review")]]
    s = bench.score([neg, pos], preds, V["identifiers"])
    assert s["fp"] == 1 and s["fp_on_negative_notes"] == 1
    assert s["covered"] == 1.0 and s["auto_covered"] == 0.0  # a review span is not auto
    assert bench.union([[1], [2]], [[3], []]) == [[1, 3], [2]]


def test_sd_engine_with_missing_model_fails_loudly(tmp_path):
    # SD's engines return [] on any error; the bench must not score that as a real zero
    args = argparse.Namespace(sd_root=str(tmp_path), sd_python=sys.executable, ner_python=None,
                              pf_model=None, llama_cli=sys.executable,
                              mediphi=str(tmp_path / "no.gguf"), qwen=None, n_predict=8,
                              ctx=64, batch=1, timeout=5)
    with pytest.raises(SystemExit, match="mediphi: missing"):
        bench.run_sd("mediphi", [{"text": "x"}], args)
    with pytest.raises(SystemExit, match="pf: missing"):
        bench.run_sd("pf", [{"text": "x"}], args)


def test_calibration_scores_every_judged_candidate():
    text = "Tan Ah Kow seen on 3 Mar; HIV; Ward 5"
    docs = [{"id": "d", "text": text, "spans": [
        {"label": "name", "start": 1, "end": 10, "match": "Tan Ah Kow"},
        {"label": "date_other", "start": 20, "end": 24, "match": "3 Mar"},
        {"label": bench.SHI[0], "start": 27, "end": 29, "match": "HIV"}]}]

    def c(s, e, p, cat, sources=()):
        return {"start": s, "end": e, "confidence": p, "category": cat, "sources": sources}
    judged = [[c(1, 10, 0.9, "name"),          # right
               c(20, 24, 0.5, "dob"),          # an other date only: left out
               c(27, 29, 0.8, bench.SHI[0], ["lexicon:hiv_sti"]),  # SHI is not "none": right
               c(32, 37, 0.1, "none"),         # no gold: wrong, and correctly low
               c(32, 37, None, None)]]         # failed judgment: left out
    cal = bench.calibration(docs, judged)
    assert cal["n"] == 3 and cal["positives"] == 2
    assert cal["ece"] == round((0.1 + 0.2 + 0.1) / 3, 4)
    assert {k: v["n"] for k, v in cal["by_call"].items()} == {"identifier": 1, "none": 1,
                                                              "shi_lexicon": 1}
    assert cal["by_call"]["shi_lexicon"]["ece"] == 0.2
