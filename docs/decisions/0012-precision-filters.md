# 0012: Stop headings, organisations, roles, kinship terms and engine fragments being proposed as names (P15)

Date: 2026-09-27. Status: **accepted**. Validated blind on notes_v8, which passed every gate.

## Context
The notes_v7 blind run (P14, 0011) failed the release gate on precision, 0.897 against 0.90. Its
false positives are the classes seen on every earlier set, only more of them, because the engine
spans (Privacy Filter, Presidio `person`) enter as candidates since P12 (0009). Across the dev sets
(v1–v7 and sd20, v7 on its corrected gold) the P14 configuration had 150 FPs. Most fall into five
classes:

| class | examples |
|---|---|
| headings and organisation names | `Pass Division`, `Specialist Appointments`, `STUDY ENROLMENT`, `Justice Courts`, `Kallang Bridge Law LLC` |
| roles and qualifications | `Coroner`, `Victim`, `Clinical Geneticist`, `MBBS`, `FRCPA` |
| engine spans that start or end on those words | `ED Dr`, `Adm IP`, `Outpatient Pharmacy` |
| number or letter fragments from engine spans | `110` from a time, `67` (an age), `02` |
| kinship terms of address | `Ah Ma`, `Papa`, `Ma` |

A precision failure is the cheaper kind (an extra flag, not a missed identifier), but the gate is
0.90 and these classes are cheap to remove in code. So this is again a proposer change, and the
judge and its thresholds are unchanged.

## Decision
All in `slmjev/propose.py`. None of it changes a span a rule or shape proposes for an identifier
other than a name.
- **More stop and heading words.** Roles (`coroner`, `victim`, `client`, `carer`, `physician`,
  `endoscopist`, …), qualifications (`mbbs`, `frcpa`, `mrcp`, `mmed`, `phd`, …), clinical words
  (`adm`, `resus`, `cbg`, `care`, …) and greetings are stop words. A new `_HEADING` word list
  (`division`, `courts`, `llc`, `therapy`, `enrolment`, `notice`, `sheet`, `scale`, …) joins the
  organisation words, so a capitalised run that holds one is not a name.
- **Greeting cues.** `Hi`, `Hello`, `Hey` and `(Good) morning/afternoon/evening` cue one name, as
  `Dear` did, so `Hi Joyce` proposes `Joyce` and not `Hi Joyce`.
- **Engine name spans are trimmed** (`_trim_engine_name`). If a `name`/`person` span from an engine
  holds an organisation or heading word, only the words after the last one are kept, less leading
  stop words. If nothing is left but stop words, the span is dropped. Otherwise it is unchanged.
- **Engine fragments are dropped** unless a rule or shape also proposes the same span. A fragment is
  1–3 digits or a single letter, with punctuation around it. One next to another engine span
  (within 2 characters) is kept, because the engines split addresses (`Blk 12` + `04`).
- **Kinship terms are not names** (`_kin_only`). A name candidate made only of kinship words and
  stop words (`Ah Ma`, `Papa`, `Mummy`, `Nenek`), with at least one kinship word other than `Ah`, is
  dropped unless a title or cue comes first (`Dr Ma`). `Ma.` before a capitalised word is kept,
  because Filipino names abbreviate Maria (`Ma. Lourdes Bautista`).

## Evidence (dev)
The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was re-run on the model on
every dev set. notes_v7 is scored on its corrected gold, before and after.

- **Proposals.** Against P14, the final code removes 165 candidates across the dev sets and adds
  none, and no identifier gold span loses its cover.
- **Two regressions found on the way and fixed before the freeze:**
  - A first fragment rule (up to 3 alphanumerics) dropped the gold names `Ros`, `黄美玲` and
    `黄建国`. It was narrowed to digits or one letter.
  - The narrowed rule then dropped Privacy Filter's `04` from a split address on notes_v6, a partial
    miss. Fragments next to another engine span are now kept. That brought back 2 FPs on v2
    (`10`, `S$`).
  - The kinship filter first dropped `Ma` in `SN Ma. Lourdes Bautista`; hence the cue check and the
    `Ma.` exception.

| set (dev) | recall | precision | FPs | ECE |
|---|---|---|---|---|
| sd20 | 1.000 (same) | 1.000 (same) | 0 → 0 | 0.065 |
| notes_v1 | 1.000 (same) | 0.955 → 0.966 | 4 → 3 | 0.042 |
| notes_v2 | 0.992 (same) | 0.912 → 0.942 | 25 → 16 | 0.049 |
| notes_v3 | 1.000 (same) | 0.935 → 0.960 | 20 → 12 | 0.023 |
| notes_v4 | 0.989 (same) | 0.940 → 0.973 | 23 → 10 | 0.034 |
| notes_v5 | 0.987 (same) | 0.927 → 0.970 | 23 → 9 | 0.031 |
| notes_v6 | 1.000 (same) | 0.933 → 0.968 | 22 → 10 | 0.031 |
| notes_v7 (corrected gold) | 0.993 (same) | 0.919 → 0.983 | 33 → 5 | 0.045 |

- No new misses, and direct-identifier recall is unchanged on every set. SHI recall is unchanged;
  SHI precision rises on most sets.
- sd20's ECE is above 0.05 as before: it has no SHI gold, so every SHI call there counts as wrong.

## Blind (notes_v8)
The code and calibration were frozen (`results/notes_v8_blind_freeze.sha256`, 17 files, intact
after the run), then run once on 32 unseen notes by a separate writer (23,512 characters, 315
identifier gold, 33 SHI, 5 negative notes).

- recall **0.990** (direct identifiers 0.993, 274 of 276);
- precision **0.954**; F1 0.972;
- ECE **0.031** over 399 candidates;
- per note p95 119.0 s per 1k chars.

Every gate passes. Unlike P14's run, the new filters did fire: 20 candidates were removed on this
set (`Adm IP`, `ED Dr`, `Outpatient Pharmacy`, `Justice Courts`, `Kallang Bridge Law LLC`, `Ma`
twice, `Hi Siew Ping`, digit fragments, …) and none of them was gold.

One of the 15 FPs comes from P15: the sign-off rule proposes the single word after `Yours
faithfully,` when it is not already inside a proposed run. With `Kallang Bridge Law LLC` no longer
proposed as a name, the rule proposes `Kallang` alone, and the judge sends it to review. That is a
dev finding from here on, like the rest of notes_v8.
