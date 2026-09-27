# 0014: Propose every mention of a found name and names after kinship and bilingual cues; fast-path photo file names and blood-unit numbers (P17)

Date: 2026-09-28. Status: **accepted**, on dev evidence. The notes_v10 blind run **failed**
the release gate on direct-identifier recall (0.969 against 0.98), on CSV exports and dictated
numbers.

## Context
P16 (0013) failed the notes_v9 blind run on direct-identifier recall (0.979 against 0.98). It had
11 silent misses.
- **6 names were never proposed**, by the rules or by either engine:
  - `(Sannasi, ?UGIB)`: a bracketed surname on a ward list;
  - `name "ARUMUGAM V"`: an upper-case name in quotes, ending in an initial;
  - `Anak perempuan Salmah`: a name after a Malay kinship phrase;
  - `医师 Physician: 梁国栋`: a Chinese-script name after a bilingual role label;
  - `Marivic`: a chat speaker's third mention, where the first two were found;
  - `PN/HO`: sign-off initials with a staff tag.
- **4 spans were proposed, but the judge rejected them:**
  - two photo file names (`IMG_4410.JPG`), judged `none`;
  - two spaced blood-unit numbers (`W0412 26 118730 A`), judged `none` with p ≈ 0.

  On the dev sets the judge had accepted both shapes, so the judge is not reliable on them.
- **A vehicle plate** (`FBQ 8812 H`) was proposed but called SHI.

Kinship phrases were also causing false positives: `Anak perempuan` and `- Mak Cik's` were
proposed and kept as names.

## Decision
The judge's prompt, its thresholds and the calibration are unchanged.

**In `slmjev/propose.py`:**
- **Repeat mentions.** A name proposed once in a note is also proposed wherever else the same text
  appears, at word boundaries and not inside a longer proposed name. Each mention is judged on its
  own context. Some text is never repeated:
  - fragments and stop words;
  - text with no capital letter (`po ako`, `thiazide`); Chinese-script names are still repeated;
  - a term of address on its own (`Ah`).
- **Malay kinship cues.** These words cue a name after them:
  - `anak` (with `perempuan` / `lelaki` / `laki-laki`), `cucu`, `isteri` / `istri`, `suami`;
  - `ibu`, `bapa`, `ayah`, `abang`, `kakak`, `adik`, `menantu`, `sepupu`.

  They are also stop words, so none of them is proposed as a name. `Mak`, `Cik`, `Pak`,
  `Makcik` and `Pakcik` are terms of address. A span made only of such terms, including a
  possessive (`Mak Cik's`), is not a name.
- **Quoted names** may end in an initial: `"ARUMUGAM V"`.
- **Initials on a line of their own** may carry a staff tag: `PN/HO`.
- **A Chinese-script name after a colon** (`Physician: 梁国栋`, `主诊医生: 林志强`) is proposed:
  - the first character must be a common surname;
  - the name must end at a bracket, a space, punctuation or the end of the line.

  This keeps TCM terms such as `证: 肝郁脾虚` out.

**In `slmjev/judge.py`, the rule fast path** (accepting is the fail-closed direction):
- An image or DICOM file name (`IMG_4410.JPG`, `.png`, `.dcm`, `.mp4`, …) is `photo`.
- A spaced blood-unit donation number (`W0412 26 118730 A`) is `other_id`.
- Both are also proposed as shapes.

  Before any model run, each fast path was counted over sd20 and notes_v1–v9:
  - 16 of 16 image file names are gold `photo`;
  - 9 of 9 donation numbers are gold `other_id`.

A bracketed surname alone (`(Sannasi, ?UGIB)`) is still not proposed. A rule narrow enough to
exclude the ward shorthand around it would only match this one note.

## Evidence (dev)
**The proposal diff against P16**, over sd20 and notes_v1–v9:
- 4 candidates removed, all kin-only non-gold: `Ah Ma's`, `Anak perempuan`, `Mak Cik`,
  `Mak Cik's`.
- 10 added: 7 are gold names; 3 are repeats of words already proposed as names and not gold
  (`Hokkien`, `HEP`, `MM`).
- No gold span loses its cover.

**The model run.** The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was
re-run on every dev set. notes_v9 is compared with its P16 blind run.

| set (dev) | recall | precision | FPs | ECE |
|---|---|---|---|---|
| sd20 | 1.000 (same) | 1.000 (same) | 0 → 0 | 0.065 |
| notes_v1 | 1.000 (same) | 0.965 (same) | 3 → 3 | 0.042 |
| notes_v2 | 0.992 (same) | 0.942 → 0.938 | 16 → 17 | 0.053 |
| notes_v3 | 1.000 (same) | 0.963 (same) | 11 → 11 | 0.026 |
| notes_v4 | 0.989 → 0.992 | 0.973 (same) | 10 → 10 | 0.042 |
| notes_v5 | 0.987 → 0.990 | 0.970 (same) | 9 → 9 | 0.026 |
| notes_v6 | 1.000 (same) | 0.968 (same) | 10 → 10 | 0.032 |
| notes_v7 | 0.997 (same) | 0.983 (same) | 5 → 5 | 0.043 |
| notes_v8 | 1.000 (same) | 0.960 (same) | 13 → 13 | 0.043 |
| notes_v9 | 0.967 → 0.994 (direct 0.979 → 0.997) | 0.936 → 0.941 | 22 → 21 | 0.034 |

- **Fixed on notes_v9:** `PN`, `Marivic`, `ARUMUGAM V`, `Salmah`, `梁国栋`, both photo file names
  and both blood-unit numbers.
- **Also fixed:** one blood-unit number each on notes_v4 and notes_v5.
- **Gone:** the `Anak perempuan` FP.
- **Added:** one FP, a second `Hokkien` on notes_v2. No new misses on any set.
- **Still missed on notes_v9:** `Sannasi` and `FBQ 8812 H`.
- **ECE:** notes_v2 is now 0.053, just over 0.05 (0.049 under P16). sd20 stays at 0.065.

## Blind (notes_v10)
The code and calibration were frozen (`results/notes_v10_blind_freeze.sha256`, 17 files; the
hashes still matched after the run). The frozen code was then run once on notes_v10: 32 notes the
system had never seen, from a separate writer.
- **The set:** 26,810 characters; 401 identifier gold, 39 SHI, 5 negative notes.
- **Style:** the brief asked for dictated notes, spreadsheet and log exports, minutes, and bilingual
  text.

**The result:**
- recall 0.970;
- direct-identifier recall **0.969** (343 of 354). This **fails** the 0.98 gate by 11 spans;
- precision 0.951; F1 0.960;
- ECE 0.020 over 483 candidates;
- per note p95 98.5 s per 1k chars.

**The P17 changes fired:**
- the one photo file name was found;
- names after cues and the repeated mentions were found: name recall is 0.975 on 201 names, the
  most in any set;
- no blood-unit number appears in this set.

**The 12 silent misses** come from formats P17 did not target:
- **CSV exports.** In an appointment export, the MRN and mobile columns of rows 2 and 3 were
  proposed but judged `none` with p ≈ 0. The same columns in row 1 were accepted. The column
  header is outside the judge's context window for the later rows.
  - A sample number in a table (`LAB-SK-26-01944`) was also judged `none`.
  - In an audit-log export, user names written as `ONG_JIAHUI` and `RAJ_KUMAR_N` were never
    proposed.
- **Dictated text.** Both phone numbers and an NRIC tail are spoken:
  - `nine one seven …` was proposed by Privacy Filter and sent to review, but under an SHI
    category, so it scores as a miss;
  - `six five three …` and `I C ending 447J` (IC spoken as two letters) were never proposed.
- **Shorthand.** Two names were never proposed:
  - `Husb (Khairul)`: an abbreviated relation word;
  - `/chiew yl`: a lower-case sign-off.

A post-hoc look does not change the gate result. notes_v10 is a dev set from here on.
