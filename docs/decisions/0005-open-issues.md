# 0005: Fixing P4's open issues in code

Date: 2026-09-25. Status: accepted. The prompt is now version 2 (`judge.PROMPT_VERSION`), and
the calibration was refit for it.

## Context
P4 (decision 0004) left six open issues, most of them earmarked for the P5 finetune:
1. compact dates (`20230602`) accepted as `case_visit`;
2. a duration (`5/7`) accepted as `case_visit`;
3. off-format answers on durations, ICD codes and BP (12 on test, all sent to review);
4. two DOBs misplaced into a `procedure_date` column, dropped;
5. fast-path precision unproven on hard decoys;
6. `Judge.calibrated` accepted a calibration fitted for another model or prompt.

Each had a structural cause that code can fix, so they were fixed before any finetune. The
setup is P4's (`eval/judge_eval.py --prod`, Qwen3-1.7B Q4_K_M, train seed 21, test seed 31, oracle
proposer, synthetic data only). The generator change adds a hard-decoy set without moving a
single draw of the main corpus: train, test and dev are byte-identical to P4's.

## Fixes

| Issue | Cause | Fix |
|---|---|---|
| Off-format answers | With `none` at letter E, the model answered the token `Answer` after a bare `Answer:` | The question ends with `Answer with one letter:` (`ANSWER_CUE`). On the dev durations and ICD codes, letter mass went to 1.00 in every rotation |
| `5/7` → `case_visit` | Durations were offered the ID options | New `fraction` family for `n/m` spans: the date options and `none` only |
| Compact dates → `case_visit` | An 8-digit date looks numeric, so the ID options were offered | `family_in_context`: an 8-digit compact date after a date word (`on`, `dated`, `DOB`, …) or as a whole cell of a date column is asked as a date |
| DOB in `procedure_date` | One cell cannot show it; structured_deidentification's profiler only flags *shape* outliers, so 0004's reliance on it was wrong | `slmjev.column.date_outliers`: robust median/MAD over the column's dates, at least 2 years out. An outlier cell is never dropped (`cell_review`, reason `column_outlier`) |
| Fast-path false fires | `postal_after_address` let any text follow a street word | The text between a street word or unit and the code may hold only address-like tokens (numbers, units, Title-case words) |
| Stale calibration | The file recorded the model but nothing checked it | The calibration's `meta` records `prompt`; `Judge.calibrated` refuses another prompt version or model file (by basename). `fit_calibration.py` refuses train/test reports from different prompts, and hard-decoy reports |

- **One more confident miss.** Prompt v2 made the model surer, and one gold span went from
  review (p = 0.22) to a certain drop: the bank account `250-03851-0` after "Refund to bank
  account" (train).
  - Fix: the fast path gains `account_keyword`, an account number (8+ digits, optionally
    grouped) right after `account` / `a/c` / `acct`, accepted as `other_id`.
  - On train, test, dev and 600 hard docs it fires on 4 gold spans and no decoy.
  - It landed after the uncalibrated train and test runs, which is why their fast-path counts
    are 74 and 71. The calibrated and hard runs include it. The fit only sees model-judged rows,
    so the account row is in it as a confident miss, and the thresholds already absorb it.
- **Hard decoys** (`synth.generate_hard`) are clinic locations ("Block 4 Level 3 clinic", "Tower
  Block", "#05-12 clinic") or an ID keyword ("NRIC verified;"), then a lab or bill value in a
  valid postal sector, then a real address with a gold postal code.
  - On 400 test docs (seed 41), the old rule fired on **275 of 400 decoys**; the new one fires on
    **0**. Both catch all 400 gold postal codes.
  - `id_keyword` needs the ID right after the keyword, so its traps did not fire even before.

## Results

### Zero-shot thresholds, before calibration
| Metric | P4 test (0004's zero-shot column) | **v2 test** | v2 train |
|---|---|---|---|
| Off-format answers | 12 | **0** | 0 (P4: 6) |
| Recall, flagged (direct identifiers) | 0.992 | **1.000** | 0.996 |
| Precision, auto-accepted | 0.987 | **1.000** | 1.000 |
| Decoys dropped without review | 0.785 | **0.954** | 0.991 |
| Review rate | 0.134 | **0.076** | 0.095 |
| False accepts | 3 | **0** | 0 |
| Misses | 2 (the column DOBs) | **0** | 1 (the account, fixed above) |
| AUROC | 0.979 | **0.991** | 0.983 |
| Candidate latency p50 | 2.2 s | 1.8 s | 1.9 s |

P4's train run predates its last cell fixes, so only its off-format count is compared.

### Calibration refit
Train has 294 model-judged rows, 178 of them positive.

| Calibrator | CV NLL | CV ECE |
|---|---|---|
| identity | 0.323 | 0.039 |
| **temperature (chosen), T = 3.58** | **0.139** | 0.048 |
| isotonic | 0.139 | 0.022 |

- **Why temperature again.** It ties isotonic on NLL, and the simplest kind within 0.005 wins.
- **Thresholds.** `drop_below` = 0.0217, `accept_at` = 0.501.
- **Out-of-fold results.** Recall at the drop cut 0.983; precision at the accept cut 1.000.

### Test, end to end with the calibration
382 candidates: 252 gold, 130 decoys.

| Metric | P4 (0004) | **v2** |
|---|---|---|
| Recall, flagged (all / direct identifiers) | 0.992 / 0.992 | **1.000 / 1.000** |
| Recall, auto-accepted | 0.889 | **0.921** |
| Precision, auto-accepted | 0.978 | **1.000** |
| Precision, flagged (accepted + review) | 0.877 | **0.973** |
| Decoys dropped without review | 0.731 | **0.946** |
| Review rate | 0.147 | **0.071** |
| Off-format answers | 12 | **0** |
| False accepts / misses | 5 / 2 | **0 / 0** |
| ECE / NLL of the confidence | 0.025 / 0.144 | **0.020 / 0.090** |
| AUROC | 0.982 | **0.991** |
| Category accuracy (gold) | 0.873 | 0.893 |
| `date_role` accuracy | 0.983 | 0.957 |
| Fast path | 71 (all gold) | 74 (all gold; 3 `account_keyword`) |
| Candidate latency p50 / p95 | 2.2 / 3.4 s | 1.8 / 3.5 s |
| Per 1k characters, p50 | 82 s | 76 s |

- **Review load.** 27 items: 20 gold and 7 decoys, down from 56. Most of the gold items are
  `category_uncertain`, i.e. the model knows it is PII but not which kind.
- **Column checks at work.** Both P4 misses (`19830112`, `20001207` in `procedure_date`) now go to
  review as `column_outlier`.
- **`date_role` looks lower (0.983 → 0.957), but it now scores 69 dates, not 58.** The compact
  dates are now asked as dates, so they carry a role. All 3 errors are misplaced DOBs in cells
  (`19830112`, `20001207`, `09-Apr-2000`) that the model calls `other`. All 3 go to review
  (`column_outlier` / `misplaced_date`), so the reviewer, not the role, decides them.

### Hard decoys, end to end (test seed 41, 60 docs)
180 candidates: per doc, one decoy, one address and one postal code.

- **Fast path.** It took 60 candidates, **all gold** postal codes: 48 `postal_keyword`, 12
  `postal_after_address`. It took no decoy, so its precision holds on this set.
- **Gold.** All 120 gold spans (addresses and postal codes) were accepted.
- **Decoys are hard for the model.** Of 60: 25 dropped, 30 to review, **5 accepted**. That gives
  auto-accept precision 0.96 on this set, and every error is in the safe direction.
  - By lead word: `platelet count` 19/20 dropped and `WBC count` 6/6 dropped, but `platelets`
    (0/10), `PLT` (0/11) and `total bill` (0/13) never are. The 5 false accepts are `PLT` and
    `total bill` values, which the model reads as a phone, MRN or case number.
  - Leading zeros are not the cause (3 of 5 accepted values have none).
  - The confidence is not calibrated here: ECE 0.142. Calibration fitted on the main corpus
    does not transfer to a shifted decoy mix. P7 must refit on realistic text.
  - This is a model weakness, not a rule one, and it is the first P5 target alongside category
    confusions.

## Limits
- **DOBs close to the column.** A DOB within about two years of a column's dates (a newborn in
  a procedure-date column) cannot be told apart by value. Only the model, or a reviewer, can
  catch it.
- **Column checks need columns.** The eval stands clean column neighbours
  (`synth.column_peers`) around each synthetic cell. In P6 the R side must pass whole columns to
  the judge.
- **Address rule gaps.** A capitalised non-address word between a street and a lab value
  ("Block 4 Platelets 245000") would still fire. The hard set lowercases or abbreviates lab
  names, as notes do.
- **Oracle proposer.** It is still an oracle, so the release gate stays INCOMPLETE.

## Next
- **P5 (QLoRA)** now has a much smaller target. It is no longer needed for the P4 false accepts:
  - lab and bill values after a bare lab word (`PLT`, `platelets`, `total bill`), the hard-set
    false accepts;
  - category confusions, since `category_uncertain` is most of the gold review load.
- **P6:** pass whole columns so `column.date_outliers` runs in production.
- **P7:** a real proposer, the baseline F1, and harder decoys.
