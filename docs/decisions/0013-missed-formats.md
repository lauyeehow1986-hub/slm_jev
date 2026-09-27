# 0013: Propose dotted reference numbers, 7-digit phone numbers and Chinese-format dates; stop a cue proposing an organisation's first word (P16)

Date: 2026-09-28. Status: **accepted**, on dev evidence. The notes_v9 blind run **failed** the
release gate on direct-identifier recall (0.979 against 0.98), on names that nothing proposed.

## Context
P15 (0012) passed every gate blind on notes_v8, but it still missed 3 identifiers outright and
covered 2 more only partly. All five are shapes the proposer never offered, so the judge could not
pick them:

| missed on notes_v8 | label | why it was not proposed |
|---|---|---|
| `CT.26.0914.00382` | `other_id` | a dotted reference number; no shape for it |
| `9888 012` | `phone` | a 7-digit phone number; the phone rules need 8 digits |
| `2004年3月8日` | `dob` | a Chinese-format date; the date rules are Latin-script only |
| `W0417 26 318857 K` | `other_id` | a spaced blood-unit number; only engine pieces of it were proposed |
| `FC/OSM 1482/2026` | `other_id` | a case number with a two-part prefix; only `1482/2026` was proposed |

Two of its FPs were proposer faults too. The sign-off rule proposed the lone word `Kallang` from
`Kallang Bridge Law LLC` once P15 stopped proposing the firm's name as a name. An engine name span
running over a line break was proposed whole (`L.\nSon`).

## Decision
All in `slmjev/propose.py`, except the date family in `slmjev/judge.py`. The judge, its prompt,
its thresholds and the calibration are unchanged.
- **Chinese-format dates.** `YYYY年M月D日`, spaces allowed between the parts, is a date shape. The
  judge gives it the date family (so `dob`, `date_of_death` or `none`, like any other date).
- **7-digit phone numbers.** `NNNN NNN` or `NNNN-NNN` whose first digit is 3, 6, 8 or 9 (the
  Singapore number ranges), with no digit, `+` or `-` next to it, is a phone shape.
- **Dotted reference numbers.** 1–4 letters, then at least two dot-separated groups of 2+ digits
  (`MR.25.1103.00417`), is an id shape if it holds at least 5 digits. The length floor and the
  lookarounds keep ICD codes (`K21.9`), version numbers (`v1.2.3`) and dotted dates out.
- **Spaced blood-unit numbers.** A letter and 4 digits, 2 digits, 6 digits, then an optional check
  character (`W0512 25 441219 C`), is an id shape.
- **Case numbers with a two-part prefix.** The number/year shape and the id-field rule accept a
  prefix like `FC/OSM` as well as `FC`. `no.`/`nos`/`number` count as id field words, and a field
  value may hold dots if it has at least 5 digits (so `E16.2` after a field word is not an id).
- **A cue before an organisation proposes no name.** The role and sign-off cues (`Yours
  faithfully,`, `Case owner:`, …) skip a following run that holds an organisation word (`Marina
  Crest Law LLC`, `Goh & Sons Plumbing`).
- **Engine name spans over a line break keep only the first line**, trimmed as in 0012. The first
  line is dropped if it is a fragment or holds no letter.

## Evidence (dev)
Before any model run, every new shape was counted over all nine dev sets (sd20, notes_v1–v8): each
hit a gold span, except the Chinese date, which also hits 5 `date_other` visit dates on the TCM
notes (v3–v7). Those need the judge to say `none`. The proposal diff against P15: 28 candidates
removed, 15 added, and no gold span loses its cover.

The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was then re-run on the
model on every dev set. notes_v8 is compared with its P15 blind run.

| set (dev) | recall | precision | FPs | ECE |
|---|---|---|---|---|
| sd20 | 1.000 (same) | 1.000 (same) | 0 → 0 | 0.065 |
| notes_v1 | 1.000 (same) | 0.966 → 0.965 | 3 → 3 | 0.042 |
| notes_v2 | 0.992 (same) | 0.942 (same) | 16 → 16 | 0.049 |
| notes_v3 | 1.000 (same) | 0.960 → 0.963 | 12 → 11 | 0.022 |
| notes_v4 | 0.989 (same) | 0.973 (same) | 10 → 10 | 0.034 |
| notes_v5 | 0.987 (same) | 0.970 (same) | 9 → 9 | 0.035 |
| notes_v6 | 1.000 (same) | 0.968 (same) | 10 → 10 | 0.029 |
| notes_v7 | 0.993 → 0.997 | 0.983 (same) | 5 → 5 | 0.042 |
| notes_v8 | 0.990 → 1.000 | 0.954 → 0.960 | 15 → 13 | 0.036 |

- **Fixed:** all 5 notes_v8 misses, plus one missed or part-covered id on each of v4
  (`FC/OSM 214/2026`), v5 (`W0426 26 118452 K`) and v7 (`W1234 26 012345`).
- **FPs removed:** `Kallang` and `L.\nSon` (v8), `11:40\nPatient` (v3). None added. The 5 TCM visit
  dates the new date shape proposes were all judged `none`.
- No new misses; direct-identifier recall is the same or higher on every set.

## Blind (notes_v9)
The code and calibration were frozen (`results/notes_v9_blind_freeze.sha256`, 17 files, intact
after the run), then run once on 32 unseen notes by a separate writer (25,069 characters, 329
identifier gold, 35 SHI, 5 negative notes).

- recall 0.967; direct identifiers **0.979** (285 of 291), which **fails** the 0.98 gate by one
  span;
- precision 0.936; F1 0.951;
- ECE 0.025 over 427 candidates;
- per note p95 121.2 s per 1k chars.

The new shapes did fire, so this run tests them:
- `FC/OSM 312/2026` (two-part prefix) and a 7-digit phone number were found;
- a Chinese-format visit date was proposed and judged `none`, as it should be;
- two spaced blood-unit numbers (`W0412 26 118730 A`) were proposed, but the judge called both
  `none` with p ≈ 0. On the dev sets it accepted the same shape. So the proposer did its part and
  the judge did not.

The failure is 6 names nobody proposed: neither the rules nor Privacy Filter nor Presidio offered
them.
- A surname alone in brackets on a ward list (`(Sannasi, ?UGIB)`).
- A name in upper case inside quotes (`name "ARUMUGAM V"`).
- A name after a Malay kinship phrase (`Anak perempuan Salmah`).
- A Chinese-script name after a bilingual role (`医师 Physician: 梁国栋`).
- The third mention of a chat speaker, where the first two were found.
- Initials in a sign-off (`PN/HO`); the gold for this one is arguable.

A post-hoc look does not change the gate result. notes_v9 is a dev set from here on.
