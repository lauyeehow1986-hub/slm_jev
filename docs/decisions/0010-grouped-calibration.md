# 0010: A calibration map per kind of call, fitted on whole notes (P13)

Date: 2026-09-27. Status: **accepted** for synthetic data, on the notes_v6 blind run (below).

## Context
The notes_v5 blind run of the release configuration (`jev+pf+ner.person`, 0009) passed recall,
precision and F1, and failed calibration: ECE **0.143** against the gate's 0.05. It was measured
at the candidate level, over every candidate the judge scored, dropped ones included. A
candidate counts as right when it overlaps an identifier or SHI gold span.

`calibration_p5.json` is the identity map. It was fitted on single-span judge rows (P5), where
the finetuned judge's scores already looked calibrated. On whole notes they are not:

| kind of call | what goes wrong |
|---|---|
| **SHI** | Mentions that are negated, ordered or discussed but not about the patient come out near 0.99. About half the SHI calls are right. |
| **none** | Most judged near 0. But about a third overlap gold, nearly all of them *fragments* of an identifier that another candidate already covers (`118203` out of `W0417 26 118203 X`). |
| **identifier** | Close to calibrated (ECE 0.048 on notes_v4). |
| rule fast path | Always right at 0.99. |

One map cannot fix this. The same raw 0.99 means something different for a name call and for an
HIV call.

## Decision
- **`calibrate.Grouped`** holds one map per call, plus a default for small groups. The judge
  passes the call to a grouped calibrator. Rule fast-path spans keep `judge.RULE_CONFIDENCE`.
  The calls (`calibrate.group_of`) are `identifier`, `none`, and SHI split by what proposed the
  span:
  - `shi_lexicon`: the SHI lexicon proposed it. Right about 0.6 to 0.85 of the time on the dev
    notes.
  - `shi_other`: only a heading, a name shape or an engine span proposed it. Right about 0 to 0.2
    of the time.

  Splitting `none` by proposer (ID-shaped rules versus the rest) was tried too. It helped one
  held-out set and hurt the other two, so it was left out.
- The map is fitted only on sets with SHI gold. sd20 has none: every SHI call there would count
  as wrong.
- **Decisions stay where they were validated.** An honest map for `none` lifts near-zero scores
  to the fragment rate, about 0.25. Thresholds in calibrated space would then drop nothing, and
  every date and ward number would go to review. So the thresholds get a *space*
  (`Thresholds.space`):
  - `raw` compares `drop_below` and `accept_at` with `p_identifier`, as before;
  - the reported `confidence` is the calibrated probability.

  With the P5 thresholds in raw space, every decision is exactly what the notes_v5 blind run
  scored. Only the probability reported next to it changes.
- `eval/fit_bench_calibration.py` fits the map on the `judged` records of bench reports.
  - It picks the kind (identity, temperature or isotonic per group) by out-of-fold NLL, with
    folds by note.
  - It replays every report without the model and refuses to go on unless replaying the base
    calibration reproduces the report's decisions, confidences, recall and precision.
- A fitting bug fixed on the way: `Isotonic.fit` did not pool tied scores, so a run of equal
  scores split into separate 0 and 1 blocks, and a lookup found only the first. The judge's
  saturated scores are full of ties. P5 chose the identity map, so no shipped calibration was
  affected.

## Consequences
- `confidence` means "probability that this candidate overlaps an identifier or SHI span", on
  whole notes. That is what SD shows the reviewer. SD redacts review spans too (fail closed), so
  redaction does not change.
- An SHI call can still be auto-accepted with a calibrated confidence around 0.5, as before.
  Moving decisions to calibrated space would send those calls to review. But the refit
  thresholds would also accept nothing at the 0.98 precision target, which means every span
  goes to review. That is left as an open choice, not taken here.

## Evidence
The fit and the tests are replays of the release configuration's `judged` records. Every
decision is unchanged, so recall and precision are unchanged too.

| fitted on | tested on | ECE before | ECE after |
|---|---|---|---|
| v2, v3, v4 | v1 (leave one out) | 0.075 | 0.034 |
| v1, v3, v4 | v2 | 0.162 | **0.074** |
| v1, v2, v4 | v3 | 0.123 | 0.028 |
| v1, v2, v3 | v4 | 0.109 | 0.039 |
| v1 to v4 | v5 (seen before) | 0.143 | 0.042 |

- v2 misses the gate because its "none" calls overlap gold far less often: 3 % of them, against
  22–25 % on v3 and v4. How often a writer leaves fragments is a property of the set, and no
  feature seen at runtime explained it.
- The final map, `models/calibration_p13.json`, is fitted on v1 to v5: 1,540 candidates, isotonic
  per group, chosen by 5-fold NLL (ECE 0.026 out of fold).

**Blind (notes_v6).** The code and the map were frozen, then run once on 32 unseen notes:
- ECE **0.024** over 408 candidates: identifier 0.012, none 0.051, SHI lexicon 0.067, SHI other
  0.087.
- Recall 0.990, precision 0.932 and F1 0.960 pass too. See `docs/results.md` and the eval-log row.

Open:
- SHI calls are still auto-accepted at raw ≥ 0.967, even where the calibrated confidence is low.
- The groups with little data (the SHI groups, about 25 candidates per set) carry the largest
  error.
