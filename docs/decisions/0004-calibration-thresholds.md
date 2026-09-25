# 0004: Calibration, thresholds and the `needs_review` policy

Date: 2026-09-25. Status: accepted for P4; refit whenever the model, prompts or generator change.

## Context
P3 (decision 0003) left the judge with zero-shot thresholds: drop below 0.05, accept at 0.80.
P4 had three jobs:
- fit a calibrator on held-out synthetic data;
- choose the thresholds from recall and precision targets;
- decide when a span needs a human.

All runs used the production configuration (`eval/judge_eval.py --prod`): Qwen3-1.7B Q4_K_M on
llama-server (CPU), the Choice question only, 4 rotations, and the rule-certain fast path.
- Data is synthetic only:
  - **train:** seed 21, 40 notes + 60 cells, 368 candidates;
  - **test:** seed 31, same size, 382 candidates.
- The splits are disjoint by construction (`slmjev.synth` split hygiene).
- Candidates come from the oracle proposer (gold spans + planted decoys), as in P3.

Nothing was tuned on test.

## Method (`slmjev/calibrate.py`, `eval/fit_calibration.py`)
- **Rows fitted.** Only rows the model judged are fitted. Fast-path rows skip the model, and
  failed rows go to review regardless.
- **Calibrators compared:**
  - identity;
  - temperature: `sigmoid(logit(p) / T)`, with T found by golden-section search on log T;
  - isotonic: pool-adjacent-violators, output clipped to [0.005, 0.995].
- **Choosing the kind.** Each kind is scored by 5-fold cross-validated NLL, with folds split by
  document so no note sits on both sides. The simplest kind within 0.005 of the best wins.
- **Choosing the thresholds** (`choose_thresholds`) uses the *out-of-fold* calibrated scores. The
  scores the calibrator was fitted on are optimistic.
  - `drop_below` is the highest cut that keeps ≥ 98% of positives at or above it. It is capped
    at 0.5.
  - `accept_at` is the lowest cut whose accepted set, of at least 20 items, is ≥ 98% positive. It
    is floored at 0.5.
  - With too little data it fails closed: drop nothing, accept nothing, everything to review.
- **What gets saved.** The final calibrator is fitted on all of train. It is saved with the
  thresholds and provenance in `models/calibration.json` (format `slmjev.calibration.v1`,
  gitignored), and loaded with `Judge.calibrated(backend, path)`.

## Findings

### First fit: confident misses make thresholds useless
On the first train run, all 8 gold misses had p ≤ 0.0004, the same score as the decoys. A 98%
recall target then forces `drop_below` under that floor:
- nothing is dropped;
- the review rate jumps from 0.18 to 0.46 (test: 0.16 to 0.43).

No calibrator can separate confident errors. Every miss had a structural cause, so each was fixed
in code, not in the thresholds:

| Miss | Cause | Fix |
|---|---|---|
| DOB as a bare cell (`19781225`, `2 Feb 1940`) | the judge saw no column header | the column header joins the prefix (`Judge.judge(..., column=)`) |
| DOB misplaced into `diagnosis` / `dose` / `serial_no` | no context at all | `cell_review`: a whole cell that is date-shaped outside a date column is never dropped |
| case number / postal code in a `ward` column (after the header fix) | the model trusts the header | `cell_review`: a whole cell with 5+ digits is never dropped |
| postal code after a street, no `Singapore` (`…#17-495, 801556`) | only the keyword form was rule-certain | fast path `postal_after_address`: an SG sector and a street/block/unit ending on the same line |
| `temp IC Y5308811O`, `MCR M97984B` | the model ignored the keyword | fast path `id_keyword`: an ID-shaped token right after NRIC / FIN / IC / passport / MRN / MCR |

- `cell_review` only blocks a **drop**. A confident identifier in such a cell is still accepted.
  An earlier version also blocked accepts, which sent 20 confident gold cells to review for
  nothing.
- `p_identifier` is now kept to 6 decimals, not 4, so low scores keep their order.
- **Fast path:** it caught 74 of 368 train candidates and 71 of 382 test candidates, **all gold**
  on both splits.
  - Caveat: the generator plants no hard decoys next to ID keywords or street names, so this
    precision is partly by construction. P7 must measure it on harder text.

### Second fit (train, 288 model-judged rows, 178 positive)
| Calibrator | CV NLL | CV ECE | CV AUROC |
|---|---|---|---|
| identity | 0.380 | 0.045 | 0.977 |
| **temperature (chosen), T = 3.70** | **0.171** | **0.035** | 0.975 |
| isotonic | 0.213 | 0.042 | 0.966 |

- **Why temperature won.** The zero-shot Choice is badly overconfident: it puts a lot of mass at
  exactly 0 or 1. One parameter fixes most of it. Isotonic overfits at this size.
- **Thresholds:** `drop_below` = 0.0246, `accept_at` = 0.572 (calibrated). In raw `p_identifier`
  terms:
  - a span is dropped only when raw p < ~1e-6, i.e. the model is certain;
  - accept needs raw p ≥ ~0.75 (zero-shot: 0.80).
- **Out-of-fold results:** recall at the drop cut 0.983; precision at the accept cut 0.982.

### Test, end to end with the calibration (`--calibration models/calibration.json`)
382 candidates: 252 gold, 130 decoys.

| Metric | Zero-shot thresholds (replayed, same answers) | **Calibrated (end to end)** |
|---|---|---|
| Recall, flagged (all / direct identifiers) | 0.992 / 0.992 | **0.992 / 0.992** |
| Recall, auto-accepted | 0.889 | 0.889 |
| Precision, auto-accepted | 0.987 | 0.978 |
| Precision, flagged (accepted + review) | — | 0.877 |
| Decoys dropped without review | 0.785 | 0.731 |
| Review rate | 0.134 | **0.147** |
| ECE / NLL of the confidence | 0.035 / 0.305 | **0.025 / 0.144** |
| AUROC | 0.985 | 0.982 |
| Category accuracy (gold) | | 0.873 (P3: 0.826) |
| `date_role` accuracy | | 0.983 |

- **What calibration bought.** The judge's `confidence` is now an honest probability: ECE 0.025
  and NLL halved. The reviewer and the R side can read it as one.
- **What it did not buy.** It did not improve the decisions. The raw scores are so bimodal that
  any cut between ~1e-6 and ~0.8 gives almost the same split.
- **Remaining misses (2).** Both are DOBs misplaced into a `procedure_date` column (`19830112`,
  `20001207`). In a date column the judge cannot tell a birth date from a procedure date.
  - This is a column-level signal: a 1983 value among recent procedure dates.
  - ~~structured_deidentification's profiler (`profile.R`, misplaced-PII and outlier detection)
    covers it, and P6 must keep that profiler in front of the judge.~~ **Corrected in 0005:**
    that profiler flags only *shape* outliers, so it cannot see `19830112` among `20230602`.
    slm_jev now checks the values itself (`slmjev.column`).
- **False accepts (5).**
  - Four are compact dates (`20230602`) and one is a duration (`5/7`), all read as `case_visit`.
  - They over-remove, the safe direction, but they cost utility. They are the P5 finetune's first
    target.
- **Off-format answers.** 12 on test, 11 of them decoys (`1/7`-style durations, ICD codes). They
  go to review (fail closed), and they are a quarter of the decoy review load.
- **Property.** `property_type`, from rules only: 47% correct, 53% `unknown`, **0% wrong**.
  `unknown` routes to the policy's `unknown` case.
- **Latency is unchanged** at 2.2 s per model-judged candidate (p95 3.4 s): about 82 s per 1k
  characters (p50) of dense synthetic notes. The fast path takes 19% of candidates off the
  model.
- **Memory:** llama-server private memory peaked at 1.5 GB.

## Decision: the `needs_review` policy
A span's `decision` is one of `identifier`, `not_identifier` or `review`. `needs_review` is true
exactly when the decision is `review`.

1. **Accept, without the model:** a rule-certain span (`rule_certain`, confidence 0.99).
2. **Review (fail closed)**, when any of these holds:
   - the backend errored, or the Choice was off-format;
   - the calibrated confidence is in `[drop_below, accept_at)`;
   - the top category is < 0.60, even when the confidence is high: the category routes the
     policy;
   - a context-free cell that would otherwise be dropped (`cell_review`);
   - Choice and Noul disagree, but only when `disagree_at` is set. It is off by default.
3. **Drop:** calibrated confidence < `drop_below`, and no review reason.
4. **Accept:** calibrated confidence ≥ `accept_at`, top category ≥ 0.60, and no review reason.

The thresholds come from the calibration file. Without one, the zero-shot defaults (0.05 / 0.80)
apply; they measured almost the same decisions. The decision rule itself is one pure function,
`judge.decide`, so the fitting script replays exactly what the judge does.

## Release gate
**INCOMPLETE:**
- The proposer is still an oracle.
- There is no 20-note baseline F1 against MediPhi yet (P7).
- Fast-path precision is unproven on hard decoys.

Passing so far: direct-identifier recall 0.992 (≥ 0.98), auto-accept precision 0.978
(≥ 0.90) and ECE 0.025 (≤ 0.05). Flagged precision is 0.877. It counts review items as positives, so it is not
the gated number, but it is reported. See `eval-log.md`.

## Next
Superseded in part by 0005, which fixed the open issues below without a finetune.

- **P5 (QLoRA):**
  - the targets are compact-date → `case_visit` false accepts, off-format answers on `n/7` and ICD
    codes, and category confusions (`device`, `other_id`);
  - then re-run P4. The calibration must be refit for the new model.
- **P6:**
  - `Judge.calibrated` should refuse a calibration fitted for a different model or prompt version;
    the file's `meta` already records the model and generator;
  - keep the R column profiler in front of the judge for misplaced values in same-type columns.
- **P7:** hard decoys next to ID keywords and street names, to measure the fast path's precision.
