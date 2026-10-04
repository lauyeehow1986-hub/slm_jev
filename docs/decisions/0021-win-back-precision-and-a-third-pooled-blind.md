# 0021: Win back precision, propose the general shapes, then a third pooled blind gate (P24)

Date: 2026-10-04. Status: **accepted**. Blind on notes_v22–v24 (pooled): **FAIL** on direct recall alone (0.977, 904 of 925;
907 needed). Precision, ECE and F1 pass.

## Context
P23 (0020) failed the pooled blind gate on notes_v19–v21 on direct-identifier recall alone:
- 0.979 (826 of 844; 828 needed);
- precision 0.900 sat exactly at the gate, so there was no margin left to spend on proposing
  more.

17 of the 18 direct misses were never proposed. They fell into a few general shapes: bare NRIC
tails after "last 4", sign-off initials in lower case or with hyphens, names in CSV and XML
fields, and overseas phones and addresses. Of the 104 false positives, most were organisation
names, overseas places, headings, Malay, Indonesian and Tamil words, and protocol or incident
codes called case numbers.

So P24 does two things, in this order:
1. Win back precision with filters in the proposer. Precision counts a span sent to review as a
   prediction, and only a "not an identifier" call drops a span. A filter before the judge is
   therefore the lever that moves precision.
2. Then propose the general shapes behind the misses.

Each change is a general form, not the missed string. It must lose no gold and add no false
positive on any dev set in the offline replay (below).

## Decision

### 1. Precision filters (`slmjev/propose.py`)
- **Organisation names.** More organisation words: restaurant, optometrist, maids, shipping, club,
  childcare, takaful, berhad, surgery, klinik, puskesmas, and others. A name-shaped run before
  an organisation word is not proposed as a person.
- **A place or an organisation before or after a name.** A name-shaped span is not a person when:
  - an eponym word follows it (`Murphy sign`, `Hartmann's procedure`, `Hinchey` grades);
  - an organisation word comes right before it (`RSUD`, `Klinik`, `Puskesmas`, `Hospital`, `MV`);
  - it starts an organisation's name.
  This applies to the name shapes, the cue shapes and the engines' names alike.
- **More headings and stop words.** These include:
  - Malay, Indonesian and Tamil function words and greetings;
  - Indonesian and Malay honorifics on their own (`Tn`, `Ny`, `Bpk`, `Hj`);
  - compass words in place names (`Utara`, `Selatan`);
  - captions such as `Cohort`, `Section` and `Registration`.
- **Codebook columns.** A column whose header names a dataset's columns (`var_name`, `Field`) is
  no name column. A lone `No` or `#` header numbers rows and names no ID.
- **Codes named by what they number.** A code an ID shape or an engine proposed is dropped when
  the word before it names a thing that is no person's:
  - an ethics board (`CIRB`, `DSRB`), a protocol, a query, a shipment;
  - a model, a lot or batch, a version, a leaflet, a room, a rack, an analyser.

  It is also dropped when the code itself starts with `CIRB`, `DSRB` or `protocol`. A rule's
  span is never dropped this way. Incident numbers were tried and kept, because EMS incident
  numbers are gold on three dev sets.
- **Engine spans.**
  - A name that cuts a Latin word is widened to the whole word (`[Tan T]ock` → `Tan Tock`), so
    the organisation and stop-word checks see the real word.
  - A lone clock time is dropped (`9:20 am`).

### 2. New proposals (`slmjev/propose.py`, `slmjev/judge.py`)
- **NRIC tails.** A bare tail after "last 4", with a bracket or a verb before it (`NRIC (last 4):
  962E`, `IC last 4 is 815J`). This is a rule-certain span.
- **Initials.**
  - hyphenated, after `Initials:` (`N-A-R`);
  - two or three letters of any case after a dash, at the end of a line, a quote, a bracket or a
    sentence (`- KX.`, `- jpm"]`). Dosing and short words are excluded (`- bd`, `- ok`, `- nil`).
- **Columns and fields.**
  - Agent headers name people: screener, abstractor, reviewer, recorder, interviewer,
    coordinator, collector, approver, requester, reporter, assessor, rater, observer and
    investigator. A lower-case login in a CSV's `screener` column is proposed.
  - An XML element whose tag names a person (`<Abstractor>`), with a name-like value.
  - A subject, participant, donor, sample or aliquot header names an ID (`Subj` over `TTSH-017`).
  - A fixed-width header may join two labels with an arrow (`Complaint -> action`). Before this,
    that one header cell hid the whole header row.
- **A code after a word naming whose it is** (`donor D-7802`, `Pt 0219`): three to six digits,
  maybe with a letter prefix.
- **Malay and Indonesian honorifics** cue the name after them (`Tn. Agus`, `Hjh Rokiah`).
- **Phones.**
  - a local number partly masked (`8xxx 6631`), with a mask character and four digits or more;
  - a number with its area code in brackets (`(061) 845 2210`).
- **Philippine addresses.** A purok or sitio and its barangay (`Purok 4, Brgy. Gun-ob`). Also a
  four-digit postcode after a city or a province, which must not be a year.
- **A part of a capitalised name, repeated.** When a name is written in capitals after a person's
  caption (`Child: MUHAMMAD RAYYAN BIN ISKANDAR`), a part of it written in title case later in
  the prose (`Rayyan slipped`) is proposed. Repeating parts of any capitalised name was tried
  first: it proposed 26 headings for 2 names.

Calibration (`models/calibration_p22.json`), the thresholds and the gate are unchanged.

## Evidence (offline replay, all dev sets)
The replay re-scores each dev set's latest judged run against the new proposals, with no model
call:
- a prediction whose interval is no longer proposed is dropped;
- a new proposal is counted as gold or as a decoy by overlap.
An outer span that gives way to the inner spans still proposed is kept as replaced.

| | before | after |
|---|---|---|
| false positives (21 sets and sd20) | 411 | 294 |
| of which notes_v19 / v20 / v21 | 31 / 34 / 39 | 9 / 10 / 8 |
| true positives | 6,596 | 6,596 |
| new proposals on gold | | 69 |
| new proposals on no gold | | 11 (engine fragments widened to a word) |
| uncovered identifier gold (direct) | 38 (27) | 15 (11) |

No gold was lost on any set. The 11 new decoys come from widening the engines' name fragments.
The judge sees each of them; they are counted here as if all were kept.

## Evidence (dev): model re-run
The release configuration (`jev+pf+ner.person`, `calibration_p22.json`) was re-run on seven dev
sets. Each is compared with its latest judged run: notes_v19–v21 with their P23 blind runs, the
others with their latest dev runs. These are dev sets, and the sets the fixes came from.

| set | recall | direct misses | precision | false positives |
|---|---|---|---|---|
| notes_v19 | 1.000 → 1.000 | 0 → 0 | 0.908 → 0.962 | 31 → 12 |
| notes_v20 | 0.946 → 0.986 | 8 → 0 | 0.911 → 0.973 | 34 → 10 |
| notes_v21 | 0.963 → 0.997 | 10 → 0 | 0.882 → 0.971 | 39 → 9 |
| notes_v18 | 0.997 → 0.997 | 1 → 1 | 0.927 → 0.933 | 24 → 22 |
| notes_v17 | 0.995 → 0.995 | 1 → 1 | 0.956 → 0.960 | 20 → 18 |
| notes_v15 | 0.997 → 0.997 | 0 → 0 | 0.896 → 0.904 | 35 → 32 |
| notes_v12 | 0.971 → 0.974 | 8 → 7 | 0.943 → 0.952 | 24 → 20 |
| all seven | | 28 → 9 (of 2,093) | 0.920 → 0.951 | 207 → 123 |

- **On notes_v19–v21 pooled** (`eval/pool.py`):
  - direct recall is 1.000 (844 of 844);
  - precision is 0.969 (CI 0.956–0.978);
  - ECE is 0.038;
  - F1 is 0.981.
- **No new miss on any set.**
- **Six new false positives**, against 90 that went:
  - headings and fragments judged names (`QUARTERLY`, `Intake`, `Inbound`, `MLT`);
  - an acronym and a letter judged addresses (`CHAS`, `X`).
- **Latency:** per-note p95 on notes_v19–v21 was 96.6 s, 123.4 s and 101.9 s (sequential runs).

These are in-sample numbers. They show that the changes do what they were meant to do, not that
the changes generalise. Only the blind sets below can show that.

### What the writers' reports said, and when
The three blind writers each returned a short report with their set, before the freeze file
was written. The reports described the writers' labelling conventions, for example an ambulance
incident number labelled `case_visit` and a ship's IMO number labelled `device`. The code
changes were complete before the reports arrived. No code, calibration or threshold changed
after that.

## A third pooled blind gate
Three new sets, notes_v22–v24, 32 notes each, by three new writers. Each writer read only its
brief: the same format, labels and fixed annotation convention as notes_v16–v21, and a domain
that no earlier writer had:
- specialist and sensitive services (psychiatry, addiction, sexual health, fertility, NICU,
  oncology, transplant, child protection);
- hospital operations and support services (billing and claims, occupational health, security,
  transport, records release, IT and device logs, complaints, mortuary);
- public health, pre-hospital, forensic and institutional health (notifications and contact
  tracing, ambulance and triage records, autopsy, prison, military and immigration medicals,
  port health, school health).

The protocol is the same as 0019 and 0020:
- only the headers are read before the run;
- the code, calibration, `eval/pool.py` and the sets are hashed first;
- the sets run one after another;
- `eval/pool.py` decides on pooled counts.

## Evidence (blind): notes_v22–v24, pooled
- **Frozen:** the code, `models/calibration_p22.json`, `eval/pool.py` and the three sets
  (`results/notes_v22_v24_blind_freeze.sha256`, 20 files).
  - The freeze was intact before the run and after each set.
  - The run started after the last dev run had finished.
- **The sets:** 78,029 characters; 1,067 identifier gold (925 direct, 456 names); 15 negative
  notes.

| set | recall | direct recall (95% CI) | precision (95% CI) | F1 | ECE (95% CI) |
|---|---|---|---|---|---|
| notes_v22 | 0.970 | 0.974 (260 of 267; 0.947–0.987) | 0.966 (0.939–0.982) | 0.968 | 0.037 (0.022–0.073) |
| notes_v23 | 0.972 | 0.974 (377 of 387; 0.953–0.986) | 0.942 (0.918–0.960) | 0.957 | 0.035 (0.024–0.060) |
| notes_v24 | 0.983 | 0.985 (267 of 271; 0.963–0.994) | 0.948 (0.917–0.968) | 0.965 | 0.032 (0.022–0.070) |
| **pooled** | 0.975 | **0.977** (904 of 925; 0.966–0.985) | **0.951** (0.936–0.962) | **0.963** | **0.021** (0.015–0.040) |

**The gate fails on direct-identifier recall alone, by 3 spans.** Precision, ECE and F1 pass
with room to spare. notes_v24 passes on its own.

Compared with P23's blind run:
- precision rose from 0.900 to 0.951 (104 false positives to 53);
- direct recall moved from 0.979 to 0.977.

The precision filters generalised. The new proposals fixed the shapes they were made for, but
the new writers brought new places again.

**27 identifier spans missed (9, 13, 5).** 21 of them are direct:
- **14 never proposed:**
  - names:
    - a given name inside a quoted "B/O" phrase;
    - a name on a name tag in quotes;
    - a sentence-initial given name;
    - the second line of a name wrapped in a fixed-width table;
    - a name after "employer rep" and before "of" and a company;
    - a mortuary attendant's given name before a bracketed staff code;
    - a single name in a table cell;
    - two given names and a set of initials in a group chat;
    - initials in a chat line;
    - initials engraved on a ring (`M & J`);
  - a "last-4" NRIC tail after "Verified" with no bracket and no colon;
  - a triage tag number inside "mother of …".
- **6 proposed but judged "not an identifier":**
  - three 7-digit MRNs in an MDT table column (p ≈ 0);
  - a year of death in a letter;
  - an NRIC with a transposed checksum letter;
  - a sign-off pair of initials.
- **1 sent to review** with an SHI label: the same sign-off initials, a second time. The
  identifier view does not count it.

**53 false positives** (7 on negative notes):
- headings and captions called names (`FEEDBACK CARD`, `Coroners Act`, `Child`, `Donor`);
- unit and military words (`Bravo Coy`, `CPT`);
- a ship's name and an overseas town;
- product and implant codes caught by the vehicle-plate shape;
- lost-property and procurement reference numbers called case numbers;
- PF fragments (`07`, `10`, `DID`, `NR`).

**Latency:**
- 66.5 s per 1k characters (mean);
- per-note p95 87.1 s, 161.0 s and 102.9 s.
- notes_v23, the densest set (464 identifier spans), took 83.7 s per 1k characters. The other
  two took 57–58.

**Other systems, F1 on v22/v23/v24:**
- slm:jev alone 0.963/0.930/0.932;
- rules + PF 0.929/0.889/0.907;
- PF 0.869/0.834/0.858;
- MediPhi 0.507/0.314/0.385.

notes_v22–v24 are dev sets from here on.
