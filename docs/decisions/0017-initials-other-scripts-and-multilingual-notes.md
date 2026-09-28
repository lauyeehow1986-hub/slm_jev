# 0017: Initials, other scripts, dictation and multilingual notes (P20)

Date: 2026-09-28. Status: **accepted**. Blind on notes_v13: **PASS**; repeated on notes_v14: **FAIL** (see the last two sections).

## Context
P19 (0016) failed the notes_v12 blind run on direct-identifier recall (0.907 against 0.98) and on
precision (0.884 against 0.90). Of its 42 silent misses, 18 were initials labelled as names; the
rest were dictated lower-case names, lone given names, Tamil script and a few IDs. Of its 48 false
positives, about 25 were words of Tagalog, Malay and Indonesian notes, kin terms and obituary
words, called names by the English NER engine and Privacy Filter.

## Decision
The judge's prompt wording, its thresholds and the calibration are unchanged. All changes are in
`slmjev/propose.py`, plus one shared pattern (`SPOKEN_REF`) in `slmjev/judge.py`.

**New candidates:**
- **Initials given after a name** (`Dr Mona Teo [MT]`, `wife Kamala [K]`, `Benjamin Tan (BT)`):
  the letters must be initials of the name's words, in order, and only a role in brackets may
  stand between. Those initials are then proposed wherever else they stand as a whole token
  (`MT: explained ...`, `Minutes taken by MT`). One letter is taken only in square brackets
  (`(M)` is a sex) and only as a speaker's label at the start of a line.
- **Undotted initials after a check or sign word** (`Checked: QLX/RTY`, `Minutes taken by BT`),
  each part of a `/` pair on its own.
- **Speaker initials** that open two lines or more (`JW: ...`); not a stop word or a staff
  abbreviation (`HR:`, `PT:`).
- **Name columns** also by headers that say who signed or did the work (`Sign`, `Initials`,
  `Checked`, `Surgeon`, `Anaesthetist`, `Scrub`, ...).
- **Names in other scripts**: Tamil, Devanagari, Bengali, Thai or Myanmar script glossed in Latin
  letters in brackets (`ரேவதி (Revathi)`: both), or after a relation word or name label of that
  language (`மகள்`, `நோயாளி:`, `नाम`, `নাম`, `คุณ`).
- **Accented Latin letters in names** (`Nguyễn Thị Hoa`, `José Peña`): the name shapes took ASCII
  letters only.
- **Lower-case names after a lower-case honorific**, as dictation and quick phone notes write
  them (`mister lim ah beng`, `doctor anand rao`): each run of one to three words up to a stop
  word or a common verb (`doctor say ...`) is proposed; the judge picks.
- **A reference dictated with a slash** (`two six slash four four one two nine zero`).
- **Key=value exports**: an underscore-joined name after `by=`/`name=`/`dr=` (`order.by=DR_NAIR_
  SUNIL`), and a login after `by=`/`user=`.
- **Malay and Indonesian terms of address and form labels as cues** (`Jururawat: Zarina`, `Puan`,
  `Encik`, `Ibu`, `Pak`), and Tagalog relations.

**Fewer false candidates:**
- An engine's "name" holding a lower-case Tagalog, Malay or Indonesian function word (`po`, `sa`,
  `ako`, `minta`, `nomor`, ...) is a phrase of the note, not a name, unless a cue stands before it
  (`Mr ng`: a surname).
- Form labels of those languages (`Tarikh`, `Penjaga`, `Paspor`, `Majikan`, `Dokter`, ...) and
  obituary words (`Obituary`, `Cortege`, `Wake`) are stop words; `Crematorium`, `Columbarium`,
  `Parlour`, `Branch` end an organisation's name.
- Kin terms of Tamil, Hindi and Tagalog (`Amma`, `Appa`, `Lola`, `Nanay`, `Kuya`, ...) are terms
  of address, as `Ah Ma` already was.

**Not done:**
- **Three-digit codes with a letter prefix** (`HF-017`). Over sd20 and notes_v1–v12 it added 25
  decoys (`VP-300`, `IPC-WI-014`, device models and policy numbers) for 6 subject codes.
- **Lower-case names after lower-case relation words** (`son marcus`). It added 29 candidates
  (`son lives alone`, `wife works shifts`) for 1 name.
- **Asking proposed initials only "name or none"** (a new option family for 1-3 capitals from
  `shape:initials` / `shape:name_column`). On the first replay the judge was sure `TYS`, `NS`,
  `CY` and one `SLK` identified someone but called them drug use or another sensitive condition.
  Re-run on notes_v12 with the two options, it answered `none` for 11 of the 17 (`CMX`, `CY`, `NS`,
  `SLK`, `TYS`, a `BT`), with a none option worded "abbreviation, unit, code": direct recall
  0.964 → 0.953. Reverted; the fine-tuned judge never saw that family.
- Lone given names with no cue (`Remarks: Aryan fainted`), `TAN B L`, a relative date of death in
  Chinese (`去年十一月`) and SHI words of other languages are left as known residuals.

## Evidence (dev)
**The proposal diff against P19**, over sd20 and notes_v1–v12: 38 candidates added, 37 of them on
gold; 35 removed. 27 gold spans newly covered, all on notes_v12 (the 18 initials, the 4 dictated
names and case number, `SLK` ×2 and `Aini` in the `Sign` column, `NAIR_SUNIL`, `lwchong`,
`Zarina`, the 2 Tamil-script names and `Revathi`). No identifier gold span loses its cover. Two
engine spans that happened to sit on SHI gold were removed (`po ako` in `buntis po ako`, notes_v9,
judged `review`/reproductive; `seorang diri dan nampak`, notes_v12, judged `none`).

Proposals changed only on notes_v9–v12, so only those were re-run; the other sets' proposals
are identical and so are their judgments.

**The model re-run** (`jev+pf+ner.person`, `calibration_p13.json`; notes_v12 against its P19 blind
run, the others against their P19 dev runs):

| set | recall | direct recall | precision | F1 | FPs | ECE | SHI recall |
|---|---|---|---|---|---|---|---|
| notes_v12 | 0.899 → 0.952 | 0.907 → **0.964** | 0.884 → **0.937** | 0.891 → 0.944 | 48 → 26 | 0.042 | 0.444 → 0.407 |
| notes_v11 | 0.995 → 0.995 | 0.994 → 0.994 | 0.939 → 0.941 | 0.966 → 0.967 | 24 → 23 | 0.035 | 0.714 → 0.714 |
| notes_v9 | 0.994 → 0.994 | 0.997 → 0.997 | 0.940 → 0.940 | 0.966 → 0.966 | 21 → 21 | 0.032 | 0.514 → 0.486 |
| notes_v10 | 0.995 → 0.995 | 0.997 → 0.997 | 0.952 → 0.952 | 0.973 → 0.973 | 20 → 20 | 0.019 | 0.538 → 0.538 |

No new miss on any set. On notes_v12 the fixed misses are the dictated names (`lim ah beng`,
`harpreet core`, `oh kay lin`, `limb`), the legend initials `BT`, `K`, `P`, `VR`, `Aini`,
`NAIR_SUNIL`, `lwchong`, `Zarina` and the three Tamil-script and glossed names. Still missed there
(direct recall 0.964, under the gate): initials the judge accepted under an SHI category (`TYS`,
`NS`, `CY`, `SLK`; see *Not done*), `SG` (a stop word), `HAZIQ` and three other lone given names,
`CHUA, Wen Jie`, `TAN B L`, the spoken case number (sent to review under an SHI category), a
spoken NRIC, a room address, the date of death in Chinese, and the other_id codes. The two SHI
drops are the two incidental engine spans above. notes_v12 p95 per note 180.0 → 174.7 s (the
runs were sequential).

## Blind (notes_v13)
notes_v13 is 32 notes by a separate writer: 24,740 characters, 294 identifier gold (128 names, 265
direct), 34 SHI, 5 notes with no PII. Only its header was read before the run. The code and
`models/calibration_p13.json` were frozen (`results/notes_v13_blind_freeze.sha256`); the hashes
matched before and after the run.

| check | threshold | notes_v13 | |
|---|---|---|---|
| direct-identifier recall | ≥ 0.98 | **0.985** (261 of 265) | pass (260 needed) |
| precision | ≥ 0.90 | **0.917** | pass |
| ECE | ≤ 0.05 | **0.047** (403 candidates) | pass |
| F1 against MediPhi's 0.889 | > 0.889 | **0.949** on notes_v13; sd20 (dev) 1.000 | pass |
| p95 latency | recorded | 68.6 s per 1k chars (mean); per note p50 63.7 s, **p95 108.0 s** | recorded |

**PASS**, the first blind pass since P15 (notes_v8). The margins are thin: two fewer direct hits,
or an ECE 0.004 higher, would fail.

- Recall 0.983, F1 0.949. `national_id`, `mrn`, `case_visit`, `phone`, `fax`, `email`, `dob`,
  `date_of_death`, `postal_code`, `address`, `device` and `photo` reach 1.000; `name` 0.969 on
  128; `other_id` 0.952 on 21.
- **5 silent misses:**
  - 4 names: the initials `LTS` and `KH` (a later `KH` after `TEO K H`, which is covered in
    part), and the lower-case given names `jun` and `fatimah`;
  - an other_id, `L26-093-004871`.
- **Covered in part**, which counts as found: inverted names with a comma (`ONG, Kok Leong`,
  `GANESAN, Thangam`, `RAJAGOPAL, Meena d/o Krishnan`), `koh chin huat` and `ah huat` in lower
  case, a Thai name, and two addresses.
- **26 false positives**; 4 are on the negative notes.
  - Headings and phrases called names: `MAIN OT`, `ATTENDEE`, `[LABEL`, `Speaks Cantonese`, a
    Thai province, a Burmese phrase, a Hokkien phrase.
  - Equipment, policy and ethics numbers called IDs (`VP-3200` twice, `BME-POL-011`,
    `DSRB 2026/00418`), and a UUID called a case number.
  - Unit numbers and digits called postal codes or phones.
- **Calibration by kind of call:**
  - rule fast path 0.010 (36 candidates);
  - identifier 0.019 (272);
  - none 0.186 (50);
  - SHI lexicon-proposed 0.201 (19);
  - SHI other 0.033 (26).
- **SHI:** recall 0.529, precision 0.345.
- **Other systems on the same run:**

  | system | recall | precision |
  |---|---|---|
  | slm:jev alone | 0.922 | 0.955 |
  | Privacy Filter | 0.799 | 0.857 |
  | rules + Privacy Filter | 0.864 | 0.903 |
  | Qwen2.5-3B | 0.537 | 0.788 |
  | MediPhi-3.8B | 0.286 | 0.699 |

notes_v13 is a dev set from here on. One set by one writer is a narrow sample, and P16–P19 each
failed on the set after a fix, so this pass needs repeating on another unseen set before it is
relied on.

## Confirmation (notes_v14): FAIL
The pass was repeated with the same frozen code on notes_v14 (the code hashes are identical to
the notes_v13 freeze): 32 notes by another separate writer, 24,740 characters, 305 identifier gold
(139 names, 270 direct), 29 SHI, 5 notes with no PII. The brief asked for OCR'd faxes with names
split across lines, forwarded e-mail threads, audit-trail logs, medication tables, a text
pedigree, sign-in registers, bilingual forms and a Malay and a Tamil letter. The header-only check
also printed two note lines starting with `#` (wrapped unit numbers); nothing else was read before
the run, and no code changed.

| check | threshold | notes_v14 | |
|---|---|---|---|
| direct-identifier recall | ≥ 0.98 | 0.978 (264 of 270) | **fail**, by 1 span |
| precision | ≥ 0.90 | 0.870 | **fail** |
| ECE | ≤ 0.05 | 0.057 (406 candidates) | **fail** |
| F1 against MediPhi's 0.889 | > 0.889 | 0.919 on notes_v14; sd20 (dev) 1.000 | pass |
| p95 latency | recorded | 63.8 s per 1k chars (mean); per note p50 67.0 s, **p95 117.0 s** | recorded |

**FAIL.** The notes_v13 pass did not repeat, so P20 is not a release candidate.

- Recall 0.974, F1 0.919. `mrn`, `phone`, `email`, `dob`, `fax`, `postal_code`, `address`,
  `case_visit`, `device`, `biometric` and `photo` reach 1.000; `name` 0.978 on 139;
  `national_id` 0.966 on 29; `other_id` 0.933 on 30; `date_of_death` 0.5 on 4.
- **6 direct misses:**
  - two year-only dates of death in a text pedigree (`d. 1998`, `Mar 2011`), judged `none`;
  - an NRIC spoken with its letters (`S six seven three two nine six nine D`), sent to review
    under the wrong categories;
  - the initials `KPL`, and the lone given names `Hamidah` and `Suresh`.
- **Other misses:** two lower-case log-in names (`x_lowsm`, `lowjm`).
- **Covered in part:** the names split across lines (`LIM CHOON` / `HOCK`), OCR-corrupted
  digits (`#O7-334`, `9173 552O`), and inverted `SURNAME, Given` names.
- **45 false positives** (1 on a negative note):
  - **A Malay letter (11):** its words and a town, called names by the NER engine or Privacy
    Filter (`Beliau`, `Saya`, `menjaga`, `yang merawat`, `Johor Bahru`, …). P20's function-word
    list (which has `saya` and `yang`) filters only the proposer's own name runs, not the
    feeders' spans.
  - **A Tamil letter (4):** script fragments called names by Privacy Filter (`என்`, `மக`).
  - **Institutional letterheads (12):** this writer leaves letterhead addresses, switchboard
    numbers and company UENs unmarked, where notes_v13's writer labelled every phone number.
    Without these, precision would be about 0.90, still at the gate's edge.
  - **Headings and common words (12):** `RADIOLOGY REQUEST`, `VISITOR LOG`, `BORANG PENDAFTARAN`,
    a log's column header, `Proband` twice, `Grandson`, `July`, `NRIC`.
  - **Codes (6):** document, protocol and form numbers called IDs.
- **Calibration by kind of call:**
  - rule fast path 0.037 (43 candidates);
  - identifier 0.053 (292);
  - none 0.006 (32);
  - SHI lexicon-proposed 0.034 (22);
  - SHI other 0.316 (17).
- **SHI:** recall 0.655, precision 0.553.
- **Other systems:**

  | system | recall | precision |
  |---|---|---|
  | slm:jev alone | 0.918 | 0.922 |
  | Privacy Filter | 0.866 | 0.885 |
  | MediPhi-3.8B | 0.311 | 0.404 |

notes_v14 is a dev set from here on.
