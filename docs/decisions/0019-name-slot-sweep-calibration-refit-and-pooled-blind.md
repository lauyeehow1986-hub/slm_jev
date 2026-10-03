# 0019: A name-slot sweep, a pooled calibration refit and a pooled blind gate (P22)

Date: 2026-10-03. Status: **accepted**. Blind on notes_v16–v18 (pooled): **FAIL** on
direct-identifier recall (0.970; 0.98 needed). Precision, ECE and F1 pass (see the last section).

## Context
P21 (0018) failed blind on notes_v15 by thin margins on three checks:
- direct-identifier recall 0.976 (249 of 255; 250 needed);
- precision 0.894;
- ECE 0.052.

None of its 6 direct misses had been proposed at all. Each P-step since P16 has fixed the last
blind set's misses and then met new shapes on the next set, and a single set of about 30 notes
decides the gate by a span or two. Pooled over notes_v13–v15, P21 would have scored:
- direct recall 0.980 (774 of 790; 95% CI 0.967–0.988);
- precision 0.893 (CI 0.872–0.911);
- ECE 0.044.

The per-set point estimates move around those values by more than the margins that decided them.

So P22 changes three things together, instead of fixing the last set's misses one at a time:
1. **A general name-slot sweep** in the proposer: the places where a person's name sits, not the
   strings that were missed.
2. **A calibration refit** on every dev set, with sets held out.
3. **A larger blind test:** three sets by three writers, about 96 notes, with a fixed annotation
   convention. The gate is decided on their pooled counts and the thresholds are unchanged.

## Decision

### 1. The name-slot sweep (`slmjev/propose.py`)
New candidates, all typed `name` and sourced `shape:slot` unless stated:
- **A staff role followed by initials or one capitalised word** (`RN AFR`). The role
  and the token must sit one space or tab apart, not across a fixed-width column gap
  (`RN     EDIT`).
- **A token before a bracketed ID** (`LTS (MRN 71-440-9921)`, `H.K.L. (EG0999812H)`).
- **Initials in brackets after a bed or room number** (`52-01 (KH)`). A phone number's caption
  (`6225 8814 (DID)`) is not a bed number.
- **A name wrapped onto the next line** after an honorific (`Dr Ananda` / `Ravindran,`).
- **A dash sign-off** (`-sn fatimah`, `-- Dr Lim`).
- **Dotted initials** (`H.K.L.`), except dosing and prose abbreviations (`P.O.`, `Q.I.D.`, `N.B.M.`).
- **Name cells in a fixed-width table with a title-case header** (`No  Name   ID   Relation`;
  sourced `shape:name_column`).
- **In-laws** as relation cues (`daughter-in-law Deepa`).
- **Quoted names with initials before a slash and an ID** (`"TAN B L / S1234567D"`).

A slot token is skipped when it is:
- a stop word or a staff role;
- an organisation's start;
- inside a longer name run already proposed.

Two non-name shapes from the notes_v15 misses are general enough to add:
- **An NRIC/FIN split by spaces or hyphens** (`S 7709 506 C`, `T-0312-844-B`), with or without a
  dotted leader after the caption. It is proposed (`shape:id`), and it is on the rule fast path
  (`rule_certain`) only when the digits pass the checksum.
- **A DNA (STR) profile**: two or more loci with their alleles (`D3S1358 15/16, vWA 17/18`).
  It is proposed whole as biometric (`shape:dna`).

### 2. Calibration (`models/calibration_p22.json`)
**A lookup bug.** `calibrate.Isotonic` was fitted on report scores, which keep `p_identifier` to
6 places, but at run time it looked up the unrounded score.
- A block edge at exactly 0.0 then sent every near-zero score into the next block. "none" calls
  on dates and bare numbers reported about 0.30 instead of about 0.04. The same happened at every
  other edge.
- Decisions are unaffected, because the thresholds are in raw space.
- The lookup now rounds the score to 6 places first.

**The fitter (`eval/fit_bench_calibration.py`) now:**
- accepts a raw-space base (P13's);
- keeps the recorded decision when the thresholds are in raw space, since a calibrator cannot
  move it there. The report does not keep the judge's reasons (a cell review, a Noul
  disagreement), so re-deciding without them was wrong on notes_v10;
- tolerates the pre-P22 lookup's confidences at a block edge, and only those.

**The refit.** Same grouping (`calibrate.group_of`) and the same raw thresholds
(drop below 0.05, accept at 0.967), so every decision is unchanged.
- **Held-out check:** fitted on notes_v1–v12 (4,122 judged candidates) and tested on notes_v13–v15.
  - pooled ECE over 1,187 candidates: 0.042 with P13's map, 0.038 refitted;
  - per set: v13 0.047, v14 0.038, v15 0.061.
- **Finer "none" groups were tried and rejected.** Splitting off date-proposed or rule-proposed
  "none" calls moved the held-out pooled ECE by less than 0.004 either way. The leave-one-set-out
  mean stayed at about 0.036 under every grouping.
- The "none" calls stay the worst-calibrated kind. How often the model's "none" is wrong drifts
  from set to set (about 4% to 40%), and by the proposer:
  - about 6% for dates;
  - about 10% for name shapes;
  - about 20% for feeder-only spans;
  - about 60% for rule-proposed non-dates.

  The groups are too small to fit one map each.
- **The final map** is fitted on notes_v1–v15 (5,171 candidates; out-of-fold ECE 0.008).

### 3. A pooled blind gate, decided before the run
**The sets.**
- notes_v16, notes_v17 and notes_v18: 32 notes each, by three separate writers with different
  briefs:
  - clinical documentation;
  - administrative, legal and community correspondence;
  - messages, forms, tables and machine output.
- Each has 5 negative-control notes.
- The writers did not read the repository, the code or the other sets.
- No set's content is read before the run. Only the header is checked, and the check stops at
  the first note.

**A fixed annotation convention** for all three, given in each brief:
- initials used for a person are names;
- every phone, fax, e-mail, street address and postal code is marked, including in letterheads,
  footers, switchboards, hotlines and generic mailboxes;
- organisation registration numbers (UEN) are other_id;
- organisation names are never marked;
- field captions are outside spans;
- every mention of a person is marked.

Earlier writers chose their own letterhead convention. Unmarked letterhead lines were most of
notes_v14's and a quarter of notes_v15's false positives.

**Frozen:** before the run, the P22 code, `models/calibration_p22.json` and the three sets are
hashed (`results/notes_v16_v18_blind_freeze.sha256`). The runs are sequential, so the latency is
valid.

**The gate** (`eval/pool.py`) compares pooled point estimates with the unchanged thresholds:

| check | threshold | pooled as |
|---|---|---|
| direct-identifier recall | ≥ 0.98 | direct gold overlapped by a prediction, over all direct gold, summed over the three sets |
| precision | ≥ 0.90 | true-positive over all predicted spans, summed |
| ECE | ≤ 0.05 | over every judged candidate of the three sets together |
| F1 against MediPhi's 0.889 | > 0.889 | from pooled recall and precision |
| latency | recorded | mean s per 1k chars; each set's per-note p95 |

- Direct labels: `name`, `national_id`, `mrn`, `phone`, `email`, `case_visit`, `fax`, `address`,
  `postal_code`, `dob`, `date_of_death`.
- Each set's own row and 95% intervals are reported but do not gate:
  - Wilson intervals for the proportions;
  - a bootstrap over notes for ECE (2,000 resamples, seed 0).
- A check with no data is INCOMPLETE, and so is the verdict.
- A post-hoc audit never changes the row.
- The three sets become dev sets after the run.

## Evidence (dev)
**Proposals, against P21**, over sd20 and notes_v1–v15, using the feeders' saved spans:
- 0 candidates removed and 17 added, all 17 on gold. The cost is about 17 candidates on 10k.
- **11 gold spans newly covered:**
  - `Deepa` (v5), `TAN B L` (v12);
  - `LTS`, `KH`, `fatimah` (v13);
  - `AFR` twice, `Ravindran`, `S 7709 506 C`, `Thant` and `H.K.L.` (v15);
  - the whole DNA profile (v15).
- **Gold never proposed** went from 28 to 17 of 4,656 identifier spans (21 to 10 direct).
- **What is still never proposed:**
  - a lone `SG` (a stop word);
  - lower-case initials (`chiew yl`);
  - possessives (`Sri's`);
  - a Chinese-script date;
  - a thumbprint mention;
  - study and subject codes as other_id (`HF-017`, `SCR-118`);
  - a few lone given names in prose.

**The model re-run** on notes_v15, v13 and v12 (`calibration_p22.json`), against each set's
previous run with P21:

| set | recall | direct recall | precision | ECE |
|---|---|---|---|---|
| notes_v15 (P21 blind) | 0.977 → 0.997 | 0.976 → 1.000 (255 of 255) | 0.894 → 0.896 | 0.052 → 0.068 |
| notes_v13 (P21 dev) | 0.983 → 0.990 | 0.992 (263 of 265) | 0.923 → 0.924 | 0.046 → 0.040 |
| notes_v12 (P21 dev) | 0.952 → 0.954 | 0.967 (352 of 364) | 0.942 → 0.942 | 0.044 → 0.044 |
| pooled, P22 | 0.977 | 0.984 (870 of 884) | 0.922 | 0.028 |

- **The sweep added no false positive** on any of the three sets; the false positives are the
  same spans as before.
- **Fixed:** `AFR` twice, `Ravindran`, `S 7709 506 C`, `Thant`, `H.K.L.` (v15); `fatimah`,
  `KH` (v13); `TAN B L` (v12).
- **Proposed but labelled SHI:** the DNA profile (v15) and `LTS` (v13) are kept as identifiers
  (Noul p ≈ 1.0) and so are masked, but the Choice call picks `genetic`. The identifier view
  counts them as misses. This is a known weakness of the Choice call, left for later.
- **ECE on notes_v15 got worse** (0.052 → 0.068). Under the old lookup bug, its "none" calls sat
  at about 0.30, which happened to match v15's high "none" error rate. Under the fixed lookup
  they sit at 0.08: the DNA profile's fragments are on gold, and v15's dates are not. This is
  the drift by set described above.
- **All three sets are in the final map's fit,** so these ECEs are in-sample. The held-out
  numbers are in section 2.
- These are dev numbers; they do not gate anything.

## Evidence (blind): notes_v16–v18, pooled
**Run.** The freeze (`results/notes_v16_v18_blind_freeze.sha256`) matched before the run and
after each set. The three sets ran one after another (09:08–12:08 on 2026-10-03), with all
systems, so the latency is valid. Only the headers were read before the run. `eval/pool.py`
wrote `results/pooled_v16_v18_blind.json`.

| set | recall | direct recall (95% CI) | precision (95% CI) | F1 | ECE (95% CI) |
|---|---|---|---|---|---|
| notes_v16 | 0.984 | 0.982 (278 of 283; 0.959–0.992) | 0.953 (303 of 318; 0.924–0.971) | 0.968 | 0.017 (388; 0.014–0.046) |
| notes_v17 | 0.974 | 0.984 (360 of 366; 0.965–0.993) | 0.955 (422 of 442; 0.931–0.971) | 0.965 | 0.020 (489; 0.014–0.044) |
| notes_v18 | 0.943 | 0.939 (248 of 264; 0.904–0.962) | 0.923 (289 of 313; 0.888–0.948) | 0.933 | 0.024 (397; 0.018–0.059) |
| **pooled** | 0.968 | **0.970** (886 of 913; 0.957–0.980) | **0.945** (1,014 of 1,073; 0.930–0.957) | **0.956** | **0.014** (1,274; 0.011–0.031) |

**Verdict: FAIL.**
- Direct-identifier recall is 0.970. The gate needs 0.98, which is 895 of 913, so it fails by 9
  spans. The whole 95% interval (0.957–0.980) sits at or below the threshold.
- Precision 0.945, ECE 0.014 and F1 0.956 (MediPhi: 0.889) pass with room to spare.
- Latency: 59.1 s per 1k characters (mean). The per-note p95 is 101.5 s, 106.0 s and 98.7 s.

**v16 and v17 each pass on their own; notes_v18 fails.** That set's brief was messages,
forms, tables and machine output. Its 16 direct misses, with the identifier labels' own gold
counts:
- **dob, 6 of 15.** Its convention labels a year of birth alone as dob, and four of the misses
  are those (`M/1961` in a trial screening table). Two full DOBs in an immunisation table were
  proposed and judged `none`.
- **name, 7.**
  - initials in a table cell and a pharmacy check box (`CWS`, `WLH`);
  - log-in names in an audit log (`NG_JIAHUI`, `angkw`);
  - a name in Tamil script;
  - two lone given names in a chat (`Sri`, `Agus`).
- **national_id, 2.** "Last 4" NRIC fragments (`*842H`) in a visitor log.
- **phone, 1.** An OCR-corrupted mobile number (`9l38 2O47`).

Only the two DOBs were proposed; the other 14 never reached the judge.

**v16 and v17 misses.**
- v16: initials (`BS`, `JA`, `SMN`, `A-M`) and an MRN fragment (`118-73`).
- v17: initials (`HdS`, `NTH`), a dotted `N.A.`, a lower-case `farhan`, a free-phone number
  (`1800 555 0192`), a street address (`2 Riverside Medical Dr`), and five study screening codes
  (`SCR-031`…`035`, other_id).

**False positives.** There were 59 over the three sets, 6 of them on negative notes:
- headings and form captions called names;
- protocol, form and workstation codes called IDs;
- Singlish and Malay words;
- food and place words called addresses.

Under the fixed letterhead convention, no false positive was a letterhead contact line; one
clinic name in a letterhead was called a name.

**Calibration.** The pooled ECE is 0.014 on 1,274 candidates. The worst kinds of call are still
"none" (0.062, 0.132 and 0.085 on 34, 21 and 40 calls) and SHI other (0.101, 0.124 and 0.080).

**What this means.** The pooled gate removed the single-set luck, and the answer is clear:
- Precision and calibration are at the release level.
- Direct recall is about 0.97, not 0.98.
- The misses are still mostly spans nobody proposed: initials, log-ins, scripts, fragments
  and partial IDs. Each new writer finds new places to put them.

**Post-hoc (does not change the row).** Without notes_v18's year-only DOBs and "last 4" NRIC
fragments, the pooled count would be 886 of 907 (0.977), still under the gate. Both are fair
gold anyway: a year of birth and an NRIC tail are identifying.

notes_v16, notes_v17 and notes_v18 are dev sets from here on.
