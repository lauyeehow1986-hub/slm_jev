# 0015: Read pasted tables by column, propose name columns and dictated numbers (P18)

Date: 2026-09-28. Status: **accepted**, on dev evidence. The notes_v11 blind run **failed** the
release gate on direct-identifier recall (0.972 against 0.98), on HL7 messages and OCR text.

## Context
P17 (0014) failed the notes_v10 blind run on direct-identifier recall (0.969 against 0.98). It had
12 silent misses, mostly in formats no earlier set had:
- **Tables pasted into text.**
  - In a CSV appointment export, MRN and mobile cells in later rows were proposed but judged `none`
    (p ≈ 0). The same columns in row 1 were accepted. The header is outside the judge's
    160-character window for the later rows.
  - In an audit-log export, user names written `ONG_JIAHUI` and `RAJ_KUMAR_N` were never proposed.
  - A quoted CSV name (`"KOH, SOOK LING"`) was only partly covered.
- **Dictated text.** Two phone numbers were spelt out in digit words (`nine one seven …`). One was
  never proposed; the other was proposed by Privacy Filter and sent to review under an SHI category,
  which scores as a miss. An NRIC tail after a spoken `I C ending 447J` was never proposed.
- **Shorthand.** `Husb (Khairul)`: the abbreviated relation cued nothing, and `Husb` itself was
  proposed and kept as a name (an FP).

## Decision
The judge's prompt wording, its thresholds and the calibration are unchanged.

**In `slmjev/judge.py`:**
- **Tables in text.** `table_cell` / `table_column` find the column header of a span in a table
  pasted into free text:
  - a row with 3+ cells split by tab, `|`, `,` or `;` (double quotes protect a delimiter, except
    in a pipe table);
  - a header row above it with the same number of cells, reached through rows of that shape; a
    header is 3+ short labels with no long numbers;
  - nothing for the header row itself, or for prose with commas.
- **The header joins the prefix** (`Column: MRN`), as it already did for a structured cell.
- **An ID-shaped whole table cell is never dropped.** On the dev run the judge still answered
  `none` (p ≈ 0) for those MRN and mobile cells with `Column: MRN` in its prefix. So
  `cell_review`, which already sent context-free structured cells to review instead of dropping
  them, now covers a whole cell of a pasted table: a value with 5+ digits goes to review, and a
  date outside a date column goes to review. This decides in code, in the fail-closed direction.
- **Spoken numbers.** `spoken_digits` turns digit words into digits (`oh` is 0, `double` and
  `triple` repeat the next digit, `plus` is `+`). A spoken number that reads as a Singapore phone
  number (`[3689]` + 7 digits, optionally `+65`) is on the rule fast path as `phone`. Other spoken
  numbers go to the model.
- **`I C`** (IC spoken as two letters) leads an NRIC tail like `IC` and `NRIC`.

**In `slmjev/propose.py`:**
- **Name columns.** A name-like cell (letters, spaces, `.,'_/-`) under a header with a name word
  (`name`, `patient`, `user`, `staff`, `doctor`, `nok`, `caregiver`, …; `PatientName` and
  `user_name` are split into words) is proposed as a name.
- **Spoken numbers** of 7+ digits are proposed as `shape:spoken_number`.
- **Shorthand relations.** `husb`, `hubby`, `bro`, `sis` and `dtr` cue a name after them, and are
  stop words, so they are not names themselves.

The lower-case sign-off `/chiew yl` is still not proposed. A rule for lower-case initials after a
slash would match ward shorthand far more often than names.

## Evidence (dev)
**The proposal diff against P17**, over sd20 and notes_v1–v10:
- 1 candidate removed: `Husb`, a notes_v10 FP.
- 11 added: 8 are gold (`Khairul`, `KOH, SOOK LING`, `DE SOUZA, Mark Anthony`, `447J`, a spoken
  phone number, `ONG_JIAHUI` ×2, `RAJ_KUMAR_N`) and 3 are not (`svc_ris` and `SYSTEM` in an
  audit log; `Kelvin` on notes_v5, arguably a missing gold name).
- No gold span loses its cover.
- `table_column` finds a header in 4 notes, all real tables: v9_n06, v10_n06, v10_n26, v10_n32.

**The model run.** The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was
re-run on every dev set. notes_v10 is compared with its P17 blind run. The table-cell review rule
was added after this run; the v10 row is from a re-run with it. A replay of the rule on the other
sets' judgments changes no span there.

| set (dev) | recall | precision | FPs | ECE |
|---|---|---|---|---|
| sd20 | 1.000 (same) | 1.000 (same) | 0 → 0 | 0.065 |
| notes_v1 | 1.000 (same) | 0.965 (same) | 3 → 3 | 0.042 |
| notes_v2 | 0.992 (same) | 0.938 (same) | 17 → 17 | 0.053 |
| notes_v3 | 1.000 (same) | 0.963 (same) | 11 → 11 | 0.026 |
| notes_v4 | 0.992 (same) | 0.973 (same) | 10 → 10 | 0.042 |
| notes_v5 | 0.990 (same) | 0.970 (same) | 9 → 9 | 0.027 |
| notes_v6 | 1.000 (same) | 0.968 (same) | 10 → 10 | 0.032 |
| notes_v7 | 0.997 (same) | 0.983 (same) | 5 → 5 | 0.043 |
| notes_v8 | 1.000 (same) | 0.960 (same) | 13 → 13 | 0.043 |
| notes_v9 | 0.994 (same) | 0.941 → 0.940 | 21 → 21 | 0.034 |
| notes_v10 | 0.970 → 0.995 (direct 0.969 → 0.997) | 0.951 → 0.952 | 20 → 20 | 0.019 |

- **Fixed on notes_v10:** 10 of the 11 direct misses: two MRN cells and a mobile cell in the CSV
  export, `Khairul`, `447J`, both spoken phone numbers, and the user names `ONG_JIAHUI` ×2 and
  `RAJ_KUMAR_N`. `KOH, SOOK LING`, partly covered before, is now whole. `mrn` and `phone` recall
  are now 1.000; `name` 0.995 on 201.
- **No new misses** on any set.
- **Still missed on notes_v10:** `chiew yl` (silent) and a sample number in a pipe table
  (`LAB-SK-26-01944`, judged `none`). A spoken DOB and a spaced other_id are only partly covered.
- **Before the table-cell review rule**, the first P18 run on notes_v10 reached direct recall
  0.986 and lost one MRN cell that P17 had sent to review (p 0.23 → 0.02 with the header in the
  prefix): the header alone did not help the judge.

## Blind (notes_v11)
The code and calibration were frozen (`results/notes_v11_blind_freeze.sha256`, 17 files; the
hashes still matched after the run). The frozen code was then run once on notes_v11: 32 notes the
system had never seen, from a separate writer.
- **The set:** 26,035 characters; 370 identifier gold, 28 SHI, 5 negative notes.
- **Style:** the brief asked for forms and templates, pasted HL7/JSON/XML and fixed-width
  reports, OCR'd letters, forwarded e-mail chains, SMS reminders, handwritten-note transcriptions,
  mixed-language phrases, long tables, and people mentioned many times in different forms.
- **Seen before the run:** the set's header only. It lists the deliberately invalid,
  OCR-corrupted, spaced and masked NRICs. No code was changed after the header was read.

**The result:**
- recall 0.973;
- direct-identifier recall **0.972** (316 of 325). This **fails** the 0.98 gate by 3 spans;
- precision 0.938; F1 0.955;
- ECE 0.031 over 475 candidates;
- per note p95 126.2 s per 1k chars.

**The P18 changes fired:**
- `mrn`, `phone`, `national_id` (including the OCR-corrupted, spaced and masked ones), `email`,
  `postal_code` and `case_visit` all reach 1.000;
- tables were read by column; the one table miss is a DOB (below).

**The 10 silent misses** (9 direct):
- **HL7 v2 messages.** Name components split by `^` are gold one by one. The given names were
  found; the surnames before the `^` (`TAN^MEI LING`, `ONG^BOON HOCK`, `RAJAN^PRIYA`) were never
  proposed, nor was the doctor code `M71234A` before them.
- **OCR text.** A DOB written `l4.O2.l95l` (`l` for 1, `O` for 0) was never proposed.
- **A DOB column.** A `27/11/2015` cell under `DOB` in a tab-separated table was judged `none`
  (p ≈ 1e-6), with `Column: DOB` in the prefix. The table-cell review rule exempts dates in a date
  column, so nothing stopped the drop.
- **Names.** A first name alone with a possessive (`Sri's`, where `Sri Wahyuni` was found); a
  repeat of a nickname (`Fir`) judged SHI; a Chinese-script name after a role word and a space
  (`医师 李建国`), where P17 only looks after a colon.

A post-hoc look does not change the gate result. notes_v11 is a dev set from here on.
