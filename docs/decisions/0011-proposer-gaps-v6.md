# 0011: Close the proposer gaps found on notes_v6 (P14)

Date: 2026-09-27. Status: **accepted**, on dev evidence only. The notes_v7 blind run has none of
these shapes, so it neither confirms nor refutes the fixes. That run failed the release gate on
precision (0.897), and none of these rules fired on it (below).

## Context
The notes_v6 blind run (P13, 0010) passed every gate but left 3 silent misses, all of them
direct identifiers:

| miss | why |
|---|---|
| a name in Chinese script straight after the romanised name (`Fong Siew Lan 方秀兰`) | Han names were only proposed in brackets or after a name field |
| a bare NRIC tail (`NRIC ending 412D`) | nothing proposed an NRIC fragment on its own |
| a sample code after "Group & screen sample" (`GS-26-091837`) | proposed, but the judge scored it 0.0 |

A candidate nobody proposes cannot be picked, and a miss on a direct identifier blocks release,
so all three are proposer or fast-path fixes. None of them is left to the model.

## Decision
- **NRIC tail** (`judge.NRIC_TAIL`, shared with the proposer). An `NRIC`/`IC`/`FIN` lead, up to 24
  characters holding no digits and no `;` or `,`, then `ending` / `ends in|with` / `last 4
  (digits)`, then a tail `[A-Z]?\d{3,4}[A-Z]`. It is proposed as `shape:id` and takes the fast path
  as `national_id` (`nric_tail`). The gap bars a full NRIC and a clause break, so
  `NRIC S1234567D; bed ending 412D` still goes to the model.
- **Sample codes.** `sample` before a code that holds a letter and at least 4 digits takes the
  `ref_keyword` fast path as `other_id`. "sample" also comes before dates and volumes, which is
  why the code needs a letter.
- **Chinese-script names after a romanised word** (`propose._HAN_AFTER_ROMAN`). This is a separate
  rule from `_HAN_NAME`: a Han run of 2–4 characters, straight after a *capitalised* romanised
  word, whose first character is a common Chinese surname (simplified or traditional).
  - The first version needed only a preceding romanised word. It proposed 23 runs on the dev sets:
    18 names, plus 5 TCM terms and clinic words (herbs after `plus`, an acupoint after
    `acupuncture`, `中医诊所` after `Clinic`, `医师` after a doctor's name). The judge let 3 of them
    through (2 as SHI, 1 to review), so it was narrowed.
  - The narrowed rule fires 3 times on the dev sets, all names.

## Evidence (dev)
The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was re-run on the model,
not replayed, because new candidates need judging. The final code was checked two ways:
- **Proposals.** Against the committed proposer, candidates differ only on notes_v6 (4 notes). On
  v1–v5 and sd20 they are identical.
- **Re-runs.** v4, v5 and v6 were re-run on the final code. v1–v3 and sd20 were re-run on the first
  (wider) version, and nothing changed.

| set (dev) | recall | precision | F1 | FPs | ECE |
|---|---|---|---|---|---|
| notes_v6 | 0.990 → **1.000** | 0.932 → 0.933 | 0.960 → 0.965 | 22 → 22 | 0.027 |
| notes_v5 | 0.983 → 0.987 | 0.927 → 0.927 | 0.954 → 0.956 | 23 → 23 | 0.028 |
| notes_v4 | 0.989 (same) | 0.940 (same) | 0.964 | 23 → 23 | 0.026 |
| notes_v3 | 1.000 (same) | 0.935 (same) | 0.966 | 20 → 20 | 0.026 |
| notes_v2 | 0.992 (same) | 0.912 (same) | 0.951 | 25 → 25 | 0.054 |
| notes_v1 | 1.000 (same) | 0.955 (same) | 0.977 | 4 → 4 | 0.044 |
| sd20 | 1.000 (same) | 1.000 (same) | 1.000 | 0 → 0 | 0.065 |

- Misses fixed: the 3 on v6, and v5's blood-bag number `BB26-0918-0117`, a sample code too.
- No new misses. SHI recall and precision are unchanged on every set.
- ECE on v2 and sd20 is above 0.05 as before. v2 is the known writer-dependent fragment rate
  (0010). sd20 has no SHI gold, so the map was not fitted on it and every SHI call there counts as
  wrong.

## Blind (notes_v7)
The code was frozen (`results/notes_v7_blind_freeze.sha256`), then run once on 32 unseen notes:
- recall 0.993, and 1.000 on the 250 direct-identifier spans;
- precision **0.897**, which fails the 0.90 gate;
- ECE 0.042, and F1 0.943.

None of the three rules fired: the set's Chinese-script names sit in brackets or after
`Patient:`, where the older rules already propose them, and it has no NRIC tail or "sample" code.
So the run says nothing about these fixes, and the precision failure is not theirs.

The false positives are the same classes as on earlier sets, see `docs/results.md`. They are mostly
headings, organisation names and roles called names, often proposed by the engines. 7 of them are
people the writer left unmarked; that is a post-hoc audit, and the gate result stays FAIL.
