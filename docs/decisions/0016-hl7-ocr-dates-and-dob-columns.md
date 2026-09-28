# 0016: HL7 person fields, OCR look-alike dates, DOB columns and Chinese role words (P19)

Date: 2026-09-28. Status: **accepted**, on dev evidence. The notes_v12 blind run **failed** the
release gate on direct-identifier recall (0.907 against 0.98) and on precision (0.884 against
0.90), on initials, voice-to-text and multilingual notes.

## Context
P18 (0015) failed the notes_v11 blind run on direct-identifier recall (0.972 against 0.98). It had
10 silent misses (9 direct), mostly in formats no earlier set had:
- **HL7 v2 messages.** The surnames before a `^` (`TAN^MEI LING`, `ONG^BOON HOCK`, `RAJAN^PRIYA`)
  and the doctor code `M71234A` were never proposed.
- **OCR text.** A DOB written `l4.O2.l95l` was never proposed.
- **A DOB column.** A date cell under `DOB` in a pasted table was judged `none` (p ≈ 1e-6), with
  `Column: DOB` in the prefix.
- **Names.** `医师 李建国` (a Chinese-script name after a role word and a space); `Sri's` (a first
  name alone, where only `Sri Wahyuni` was proposed); a repeated nickname `Fir` judged SHI.

## Decision
The judge's prompt wording, its thresholds and the calibration are unchanged.

**In `slmjev/judge.py`:**
- **HL7 person fields.** `hl7_people` reads each pasted HL7 v2 segment (a line starting with a
  3-character segment name and `|`) by field position. Only fields that hold a person are read:
  - name fields (XPN: family^given^middle): PID-5, PID-6, PID-9, NK1-2, NK1-30, GT1-3, IN1-16,
    PRD-2. Components 1–3 are names;
  - clinician fields (XCN: id^family^given^middle): PV1-7/8/9/17/52, ORC-10/11/12, OBR-16/28,
    EVN-5, PD1-4, TXA-5/9/10/11/22, ROL-4. The ID is `other_id` when it has a digit; components
    2–4 are names.
  - Repetitions (`~`) are read one by one. Assigning authorities, codes and prefixes (`KRGH`, `MS`,
    `DR`) are never person components.
  - A candidate that is exactly one of these components is on the rule fast path
    (`hl7_person`). The field position is the evidence, as a checksum is for an NRIC.
- **OCR look-alike dates.** `ocr_date` reads `l`/`I` as 1 and `O`/`o` as 0 in a date shape with at
  least one look-alike and three real digits, and checks the day and month. `family_of` puts such a
  span in the date family.
- **A date in a birth or death column.** A whole cell (a structured cell, or a whole cell of a
  table pasted into text) in the date family, under a header that names birth (`DOB`, `D.O.B.`,
  `DateOfBirth`, `born`) or death (`DOD`, `death`, `died`, `deceased`), is on the rule fast path
  as `dob` or `date_of_death` (`date_column`). An appointment or admission date column is still
  judged, and so is part of a cell.

**In `slmjev/propose.py`:**
- HL7 person components are proposed (`shape:hl7`).
- OCR look-alike dates are proposed (`shape:ocr_date`).
- A Chinese-script name after a role word (`医师`, `医生`, `护士`, `主任`, `患者`, `家属`,
  `联系人`, `签名`, `姓名`, …, simplified or traditional), with or without a space or colon, is
  proposed when it starts with a common surname (`_HAN_AFTER_ROLE`).

**Not done:**
- **Repeating single parts of a longer name** (`Sri` of `Sri Wahyuni`). Over sd20 and
  notes_v1–v11 it added 36 non-identifiers (`Bedok`, `Jurong`, `SMS`, `Pass`, `Data`, …) for 1
  gold name. Left out.
- **A possessive after a repeat** (`Tan's`). A cued possessive is already proposed by the name
  shapes; the change added nothing on the dev sets. Left out.
- **Coercing a repeat of a name to `name`.** Of 5 repeats judged SHI on the dev sets, 1 was a gold
  name (`Fir`), 2 were not identifiers and 2 were gold SHI (`TPPA`, `RPR`). Left out; `Fir` stays
  a known residual.

## Evidence (dev)
**The proposal diff against P18**, over sd20 and notes_v1–v11:
- 6 candidates added, all gold: `TAN`, `ONG`, `RAJAN`, `PRIYA` (HL7), `l4.O2.l95l`, `李建国`. The
  other HL7 parts (`MEI LING`, `BOON HOCK`, `M71234A`) were already proposed and now skip the
  model.
- None removed; no gold span loses its cover.

**The model run.** The release configuration (`jev+pf+ner.person`, `calibration_p13.json`) was
re-run on every dev set. notes_v11 is compared with its P18 blind run.

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
| notes_v9 | 0.994 (same) | 0.940 (same) | 21 → 21 | 0.034 |
| notes_v10 | 0.995 (same) | 0.952 (same) | 20 → 20 | 0.019 |
| notes_v11 | 0.973 → 0.995 (direct 0.972 → 0.994) | 0.938 → 0.939 | 24 → 24 | 0.037 |

- **Fixed on notes_v11:** 7 of the 9 direct misses (the 4 HL7 surnames and given names, the OCR
  DOB, the DOB cell, `李建国`), plus the HL7 doctor code (`other_id`).
- **No new misses** on any set.
- **Still missed on notes_v11:** `Sri's` and `Fir`.

## Blind (notes_v12)
The code and calibration were frozen (`results/notes_v12_blind_freeze.sha256`, 17 files; the
hashes still matched after the run). The frozen code was then run once on notes_v12: 32 notes the
system had never seen, from a separate writer.
- **The set:** 24,355 characters; 416 identifier gold (218 of them names), 27 SHI, 67 other dates,
  5 negative notes.
- **Style:** the brief asked for labels and wristbands, dispensing labels, fax sheets, repeated
  page headers, portal messages, voice-to-text drafts, minutes with many speakers, flowsheets,
  markdown tables, key=value exports, research sheets, captions, pedigrees, obituaries, police
  and court letters, and short notes in Chinese, Malay, Tamil, Tagalog and Bahasa Indonesia. The
  writer's header labels initials used for a person (minute-taker and speaker initials) as names.
- **Seen before the run:** the set's header only. No code was changed after the header was read.

**The result: FAIL, on recall and precision.**
- recall 0.899; direct-identifier recall **0.907** (330 of 364) against 0.98;
- precision **0.884** against 0.90;
- ECE 0.043 over 497 candidates (passes; SHI calls not proposed by the lexicon 0.179 on 37);
- F1 0.891 on notes_v12;
- per note p95 180.0 s per 1k chars.

Every system fell on this set: Privacy Filter alone went from 0.851 recall on notes_v11 to 0.767,
and slm:jev alone from 0.911 to 0.815. `mrn`, `phone`, `national_id`, `dob`, `email`, `fax`,
`postal_code`, `device` and `address` still reach 1.000 recall; names reach 0.853 on 218.

**The P19 changes** fired only once in kind on this set: the DOB-column fast path accepted 4
DOB cells of a research sheet, all gold. The set had no HL7, no OCR look-alike dates and no
Chinese name after a role word. All 16 DOBs were found.

**The 42 silent misses** (34 direct):
- **Initials as names (18).** Speaker and minute-taker initials in a family-conference record
  (`CY`, `BT`, `NS`, `VR`, `K`, `P`, `SG`, each twice), dispenser initials on labels (`CMX`,
  `TYS`) and a nurse's initials in a flowsheet (`SLK` ×2). No rule proposes bare 1–3 letter
  upper-case tokens outside a sign-off line.
- **Voice-to-text (4).** Lower-case names mis-heard by speech recognition (`harpreet core`,
  `lim ah beng`, `limb`) and a spoken case number (`two six slash four four …`).
- **Lone given names (6)**: `HAZIQ`, `Aini`, `Kelvin`, `Zarina`, `Aryan`, `Zaw`.
- **Tamil script (3)**: two names in Tamil script and the romanised `Revathi` next to them.
- **Other names (2)**: `NAIR_SUNIL` in a key=value export, and `TAN B L`.
- **A relative date of death** written in Chinese (`去年十一月`, "last November").
- **Other IDs (8, not direct):** research subject codes (`HF-017`…`HF-020`, `BB-017-01/02`), a
  staff login and a biometric enrolment code.

**The 48 false positives** (6 on negative notes): words in the Tagalog, Malay and Indonesian
notes called names (`Lola po`, `sa banyo`, `Tarikh`, `Penjaga`, `Jururawat`, `Paspor`,
`Majikan`, `Penerjemah`, `Dokter`, …); kin terms (`Amma`, `Appa`); obituary words (`Cortege`,
`Mandai Crematorium`); times read as phones; equipment and memo numbers on a negative note; and
a generic departmental mailbox.

A post-hoc look does not change the gate result. notes_v12 is a dev set from here on.
