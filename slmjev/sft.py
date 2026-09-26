"""Finetune data for the judge (P5). Synthetic data only.

Each example is one Choice question exactly as :class:`slmjev.judge.Judge` asks it (same system
prompt, prefix, stem, options and answer cue, via :func:`judge.question`) with the gold option's
letter as the answer. The judge reads probabilities from the first answer token, so training
teaches that one token: the trainer puts the loss on the answer letter only.

- Candidates come from the oracle proposer (every gold span and planted decoy), as in the eval.
- Rule-certain spans are skipped: the fast path answers them without the model.
- Each candidate is asked under ``k`` random cyclic rotations of its options, so the model learns
  the option, not the position (P3 measured the zero-shot position bias).
- A candidate whose gold answer is not among its family's options is skipped and counted; it
  cannot be taught, and the eval would score it wrong anyway.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable

from slmjev import judge as J

FORMAT = "slmjev.sft.v1"


def oracle_candidates(doc: dict) -> list[tuple[J.Candidate, dict]]:
    """(candidate, gold) for every gold span and decoy in ``doc``."""
    out = []
    for s in doc["spans"]:
        gold = {"label": s["label"], "is_pii": True, "type": s["type"], **s.get("attrs", {})}
        out.append((J.Candidate(s["start"], s["end"]), gold))
    for s in doc["decoys"]:
        gold = {"label": "none", "is_pii": False, "type": s["type"], **s.get("attrs", {})}
        out.append((J.Candidate(s["start"], s["end"]), gold))
    return out


def doc_examples(doc: dict, rng: random.Random, k: int = 2, width: int = 160,
                 stats: Counter | None = None) -> list[dict]:
    """The Choice examples for one document."""
    stats = stats if stats is not None else Counter()
    text, column = doc["text"], doc.get("column")
    out = []
    for cand, gold in oracle_candidates(doc):
        if J.rule_certain(text, cand):
            stats["skip_fast_path"] += 1
            continue
        family = J.family_in_context(text, cand, column)
        opts = J.choice_options(family)
        keys = list(opts)
        if gold["label"] not in opts:
            stats[f"skip_unanswerable:{gold['label']}@{family}"] += 1
            continue
        prefix = J.prefix_for(text, cand, width, column)
        stem = J.CHOICE_STEM.format(span=J.span_text(text[cand.start - 1:cand.end]))
        for shift in rng.sample(range(len(keys)), min(k, len(keys))):
            shown = keys[shift:] + keys[:shift]
            letter = J.LETTERS[shown.index(gold["label"])]
            out.append({
                "format": FORMAT, "prompt": J.PROMPT_VERSION, "doc": doc["id"],
                "start": cand.start, "end": cand.end, "family": family,
                "label": gold["label"], "type": gold["type"], "answer": letter,
                "n_options": len(shown),
                "messages": [{"role": "system", "content": J.SYSTEM},
                             {"role": "user", "content": prefix + J.question(stem, opts, shown)},
                             {"role": "assistant", "content": letter}]})
            stats["examples"] += 1
        stats["candidates"] += 1
        stats["gold" if gold["is_pii"] else "decoy"] += 1
    return out


def build(docs: Iterable[dict], seed: int, k: int = 2, width: int = 160,
          exclude_texts: set[str] | None = None) -> tuple[list[dict], Counter]:
    """Examples for ``docs``, skipping any document whose text is in ``exclude_texts`` (the eval
    and calibration sets must never be trained on). Deterministic in ``seed``."""
    rng = random.Random(f"{FORMAT}:{seed}")
    stats: Counter = Counter()
    out = []
    for doc in docs:
        if exclude_texts and doc["text"] in exclude_texts:
            stats["skip_excluded_doc"] += 1
            continue
        out.extend(doc_examples(doc, rng, k, width, stats))
    return out, stats
