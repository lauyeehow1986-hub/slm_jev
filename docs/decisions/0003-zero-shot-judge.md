# 0003: Zero-shot judge: the Choice carries the decision

Date: 2026-09-25. Status: accepted for P3; P4 recalibrates the thresholds.

## Context
P3 built the System One judge (`slmjev/judge.py`) on Qwen3-1.7B Q4_K_M, served by llama-server
(decision 0002), and asked three things of it:
- how the wording of the questions affects the answers;
- how much option-order bias there is, and how many orders are needed to remove it;
- how fast it is, batched over one shared prefix and uncached.

All runs used synthetic dev data only (`slmjev.synth`, seed 11). Hardware: CPU only (`-ngl 0`), a
13th-gen i7 with 14 threads. Candidates came from an **oracle proposer**: the gold spans plus
the corpus's decoys, from `eval/judge_eval.py`. The numbers therefore measure the judge, not the
end-to-end pipeline.

## Design
- **One shared prefix per candidate:** the text window with the span marked `[[...]]`. Every
  question is a suffix to it, so llama-server reuses the prefix's KV cache.
- **Option families from surface shape.** Code chooses the options from what the span looks like,
  never from a label:
  - `date`: date of birth, date of death, other date.
  - `code`: the 9 code-like identifiers, plus `none`.
  - `numeric`: codes and dates, plus `none`.
  - `alnum` and `text`: name, address and the SHI categories.
  - `all`: every option.
  - Each family has its own wording for `none`, e.g. "lab value, dose, … duration like 3/7".
- **Readout:** `p_identifier = 1 − P(none)`, read from the Choice's option-letter logprobs.
  - It is renormalised over the letters and averaged over cyclic rotations of the options.
  - If less than half the probability mass falls on the option letters, the answer is off-format
    and the span goes to review.
- **Context for the policy's `by` rules:**
  - `date_role` is read from the Choice itself.
  - `property_kind` / `property_type` come from one extra question, asked only when
    P(address) + P(postal_code) ≥ 0.2.

## Findings

### Wording (70 dev candidates, 4 rotations)
| Variant | Category acc. (gold) | AUROC | ECE | Mean p on decoys | s / candidate |
|---|---|---|---|---|---|
| v0: marker only, one-line definitions | 0.66 | 0.851 | 0.151 | 0.602 | 3.36 |
| v1: span named in the stem | 0.76 | 0.935 | 0.115 | 0.210 | 3.09 |
| v2: named span, short options | 0.84 | 0.945 | 0.058 | 0.265 | 2.02 |
| **v2b: v2 + sharper `none` wording (adopted)** | **0.88** | **0.987** | **0.040** | **0.069** | **1.92** |
| v2c: v2b + span repeated in the prefix | 0.90 | 0.986 | 0.030 | 0.121 | 2.22 |
| v3: unmarked context, span named last | 0.80 | 0.972 | 0.110 | 0.425 | 2.23 |

- Naming the span in the question mattered most. With only the `[[ ]]` marker, the model latched
  onto other PII in the window, for example answering "email" for a date.
- Short option texts help twice over. They are faster, because every rotation re-reads the list,
  and they are more accurate.
- v2c is marginally better calibrated, but it lets more decoys through. v2b is the better
  trade-off.

### Noul and Score are advisory
- **Yes/no (Noul).** Four stems were tried (n0–n3). They reached AUROC 0.51–0.69, and the forward
  and reversed orders disagreed on 40–59% of spans: the answer follows the position, not the
  question. In the full run, n1 reached AUROC 0.73 with ECE 0.25, still far below the Choice.
  - Noul is therefore off by default (`ask_noul=False`).
  - When asked, it is recorded as `p_noul` and affects decisions only if
    `Thresholds.disagree_at` is set.
- **Sensitivity (Score).** Mean 0.36 on decoys, 0.39 on identifiers and 0.72 on SHI. It separates
  SHI but not identifiers from decoys, and it went off-format 4 times in 176 answers.
  - It is off by default (`ask_score=False`) and never triggers review.

### Full run: 20 notes, 180 candidates (121 gold, 59 decoys), all rotations
| Metric | Value |
|---|---|
| Gold flagged, `identifier` + `review` (recall) | 0.984; direct identifiers 0.983 |
| Gold auto-accepted | 0.802, at precision 0.980 |
| Decoys dropped without review | 0.763 |
| Review rate | 0.189 |
| `p_identifier`: AUROC / ECE / Brier | 0.982 / 0.042 / 0.041 |
| Category accuracy on gold | 0.826 |
| `date_role` accuracy | 1.00 (n = 29) |
| `property_kind` accuracy | **0.00** (n = 12; 11 of 12 answered `unknown`, which fails closed) |
| Off-format Choice answers, sent to review | 4 of 180 (`103/86`, `I72.6`, `1/7`, and one NRIC) |

- **Two gold spans were dropped:**
  - a bare postal code, `133321` (p = 0.02);
  - a device serial, `15-5643-71` (p = 0.004).

  Both are shapes that rules can propose with certainty, so they should not depend on the judge
  (see *Next*).
- **Category confusions don't change what gets flagged, but they do route the policy.** Examples:
  - temp-IC / FIN shapes, like `X0812616551K`, were read as `mrn`;
  - `M…` codes (`other_id`) were read as `national_id`;
  - bare postal codes were read as `phone` or `none`.

  Before release, the category needs rule evidence, e.g. a checksum, a keyword, or the family.

### Order bias
Per candidate, the largest single-order swing on any option averages **0.34**: letter position
still moves the answer a lot. Averaging over rotations removes most of it:

| Orders | Argmax flips vs all rotations | Mean \|ΔP(none)\| |
|---|---|---|
| 1 | 6.8% | 0.049 |
| 4, evenly spaced | 2.8% | 0.021 |

The default is **4 evenly spaced rotations** (`choice_rotations=4`). It costs about 30–40% of the
all-rotations calls, for about 3% argmax flips. P4 should check whether those flips fall on spans
that go to review anyway.

### Latency and memory
- **Per call:** p50 0.58 s, p95 1.12 s.
- **Prefix reuse (A/B, 37 calls):**

  | | cached | uncached |
  |---|---|---|
  | Mean call | 0.58 s | 1.20 s |
  | Prompt tokens evaluated | 103 | 231 |

  Batching the questions over one prefix halves the cost.
- **Production defaults** (Choice only, 4 rotations): about **2.5 s per candidate**. That is 0.40
  candidates/s, or about 93 s per 1k characters of dense synthetic notes (38 candidates per 1k).
  This is **far too slow for bulk use**.
- **`-np 2` with two clients:** 0.40 → 0.40 candidates/s, no gain. The CPU is saturated by one
  slot.
- **Memory:** llama-server's default host prompt cache (`--cache-ram` 8 GiB) grew to 6.5 GB of
  private memory, over the 4 GB model share. With `--cache-ram 256`, which is now the default in
  `slmjev.server.command`, the peak is **1.5 GB** and cached-call latency is unchanged.

## Decision
1. **The Choice carries the decision.**
   - `p_identifier = 1 − P(none)`, using the v2b wording.
   - 4 evenly spaced rotations.
   - Decision thresholds in code: drop < 0.05; accept ≥ 0.80 with category ≥ 0.60; otherwise
     review.
2. **Noul and Score are opt-in measurements** and never make a decision on their own.
3. **Fail closed, unchanged:**
   - a backend error or an off-format Choice sends the span to review;
   - an uncertain `date_role` or `property_kind` becomes `unknown`, so the policy's
     `unknown` case applies.
4. **llama-server runs with a bounded `--cache-ram`.**

## Release gate
This run is **INCOMPLETE**. The proposer is an oracle, precision is not measured end to end, there
is no 20-note baseline F1 yet, and no calibration was fitted. See `eval-log.md`.

## Next
- **P4: calibration and thresholds.**
  - Fit temperature or isotonic calibration on a held-out synthetic split.
  - Pick `drop_below` / `accept_at` to keep direct-identifier recall ≥ 0.98, then measure the
    review load.
- **Skip the judge for rule-certain candidates.** Examples: a valid NRIC/FIN checksum, an email,
  a `Singapore NNNNNN` postal code.
  - This removes most of the latency and both misses above.
  - The judge stays for ambiguous shapes and free text.
- **Get `property_kind` from address rules** (`Blk`/`Block`, a `#NN-NN` unit, `Jalan`/`Lorong`
  with no unit), not the model. Anything else stays `unknown`.
- **Proposers.** P2 found gaps, and the judge cannot pick a span nobody proposed:
  - named-month dates;
  - names, addresses and SHI.
- **Speed.** A P5 finetune should allow one order and a shorter option list. Keeping the option
  legend in the cached system prompt, or a GPU tier, would need its own decision record.
