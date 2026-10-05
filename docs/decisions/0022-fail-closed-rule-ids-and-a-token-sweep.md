# 0022: Fail closed on rule-found IDs, sweep rare capitalised words, then a fourth pooled blind gate (P25)

Date: 2026-10-04. Status: **accepted**; the token sweep is **not** a release candidate. Dev replay
on notes_v1–v24 and sd20: passes on every grouping. Blind on notes_v25–v27 (pooled): **FAIL** on
precision (0.879) and ECE (0.064); direct recall (0.984) and F1 pass.

## Context
P24 (0021) failed the pooled blind gate on notes_v22–v24 on direct-identifier recall alone:
0.977 (904 of 925; 907 needed). Precision (0.951), ECE and F1 passed with room to spare.

The 21 direct misses had two causes that four rounds of new shapes had not closed:
- **6 were proposed and then dropped by the judge.** Three were 7-digit MRNs in an MDT table
  column (p ≈ 0), one was an NRIC with a transposed checksum letter, and two were sign-off
  initials. A rule or an ID shape had already found each of them.
- **14 were never proposed.** Most were names in places no cue covers: a sentence-initial given
  name, a single name in a table cell, given names in a group chat, a name on a name tag.

Each blind round so far added shapes for the last round's misses, and the next writers brought
new places. So P25 makes two structural changes instead of more shapes. Neither keys on a missed
string.

## Decision

### 1. Fail closed on rule-found IDs (`slmjev/judge.py`)
A candidate found by an identifier rule or an ID-column or person-slot shape is never dropped:
- `rule:nric`, `rule:temp_ic`, `rule:mrn`, `rule:phone`, `rule:email`, `rule:case`,
  `rule:passport`, `rule:postal`;
- `shape:slot`, `shape:initials`, `shape:name_column`, `shape:id_column`.

When the judge calls such a candidate "not an identifier", the decision becomes `review` with the
reason `fail_closed:<source>`. The judge can still label it, but only a reviewer can drop it.
This follows the project's fail-closed rule: a missed identifier costs more than an extra flag.

### 2. A token sweep over the judge's own vocabulary (`slmjev/vocab.py`, `slmjev/propose.py`)
A byte-level BPE vocabulary of about 150,000 tokens holds most common English words as one
lower-case token after a space. A capitalised word that is *not* such a token is rarer: a name, a
place, a drug, an acronym.
- `slmjev/vocab.py` reads the token list from the GGUF header (stdlib, no model run).
- `slmjev/propose.py` proposes each capitalised word (`Sharmila`, `NICU`) as `shape:token`
  unless any of these hold:
  - it is a known heading or function word;
  - the same word appears in lower case in the text;
  - it is in the vocabulary, or derived from a word in it (`Pharmacists`);
  - it overlaps another proposal;
  - it starts an organisation's name (`Kallang Riverside Clinic`).
- The engine and the bench read the vocabulary from the judge model. Without a model the sweep
  is off.

A swept word is kept only as a `name` or an `address`. Anything else the judge says about it
becomes "not an identifier" (`token_not_name`). On the dev sets the judge kept 820 swept words
that were no identifier, against 24 that were. Most were acronyms labelled as SHI, which the
identifier view of the bench does not count as false positives but a reviewer would still see.
Keeping only names and places drops 575 of them and loses one gold span (`Tues`, a date).
Keeping only names would also lose a name, a town and a village.

Calibration (`models/calibration_p22.json`), the thresholds and the gate are unchanged.

## Evidence (dev): an offline replay on all 25 sets
The replay uses each set's latest judged run and changes nothing in it except the two P25 rules:
- fail-closed flips are applied to the judged candidates;
- every swept word was judged once by the model (token-only runs);
- the result is written in the bench's format and scored with `eval/pool.py`.

It is a merged replay, not a sequential run, and the latency it reports leaves out the sweep.
The token-only runs took 7.0 s per 1k characters on top of a full run's ~60.

| | P24 latest runs | P25 replay |
|---|---|---|
| direct-identifier misses, 25 sets | 40 (of 6,759) | 26 |
| false positives, 25 sets | 380 | 636 |
| pooled direct recall, 25 sets | 0.994 | 0.996 |
| pooled precision, 25 sets | 0.953 | 0.923 |
| pooled ECE, 25 sets | 0.015 | 0.026 |
| **notes_v22–v24 pooled:** direct recall | 0.977 (904 of 925) | **0.989** (915 of 925) |
| precision | 0.951 | **0.929** (CI 0.912–0.943) |
| ECE | 0.021 | **0.030** |
| F1 | 0.963 | **0.957** |

- Fail-closed flipped 22 judged candidates to review. 5 of them were direct gold.
- The judge kept 23 swept words that were gold and that nothing else had proposed.
- No gold was lost on any set.
- On notes_v22–v24 the replay passes every check. notes_v24's own ECE is 0.060, above 0.05;
  the gate uses the pooled value.

The cost is precision. Every rule-found span the judge used to drop now goes to a reviewer, and
some swept words are judged names. Precision fell by 0.02–0.03 on every grouping and stays above
0.90. The filters were tuned on these sets, so these numbers are in-sample. Only a blind round
can show whether the change generalises.

## A fourth pooled blind gate
Three new sets, notes_v25–v27, 32 notes each, by three new writers. The format, labels and fixed
annotation convention are the same as for notes_v16–v24. Each writer has a mix of domains no
earlier brief used:
- surgery, anaesthesia, dental, TCM and private clinics;
- digital health: telehealth and portal messages, app and wearable exports, HL7 and FHIR
  fragments, OCR'd faxes, dictation, EMR audit trails;
- social care, legal and financial casework: guardianship and LPA, social services, disability
  and eldercare, work-injury claims, insurance underwriting, migrant-worker casework.

The protocol is the same as 0019–0021:
- only the headers are read before the run;
- the code (now with `slmjev/vocab.py`), calibration, `eval/pool.py` and the sets are hashed
  first;
- the sets run one after another;
- `eval/pool.py` decides on pooled counts.

## Evidence (blind): notes_v25–v27, pooled
- **Frozen:** the code (with `slmjev/vocab.py`), `models/calibration_p22.json` and `eval/pool.py`
  were hashed before the writers started (`results/notes_v25_v27_code_freeze.sha256`). The sets
  were added once they arrived (`results/notes_v25_v27_blind_freeze.sha256`, 21 files). The
  freeze was intact before the run and after each set.
- **The sets:** 75,648 characters; 894 identifier gold (771 direct, 391 names); 15 negative notes.

| set | recall | direct recall (95% CI) | precision (95% CI) | F1 | ECE (95% CI) |
|---|---|---|---|---|---|
| notes_v25 | 0.990 | 0.988 (252 of 255; 0.966–0.996) | 0.865 (0.825–0.897) | 0.923 | 0.089 (0.055–0.137) |
| notes_v26 | 0.973 | 0.969 (246 of 254; 0.939–0.984) | 0.866 (0.825–0.898) | 0.916 | 0.067 (0.040–0.112) |
| notes_v27 | 0.990 | 0.996 (261 of 262; 0.979–0.999) | 0.908 (0.871–0.935) | 0.947 | 0.053 (0.027–0.092) |
| **pooled** | 0.984 | **0.984** (759 of 771; 0.973–0.991) | **0.879** (0.857–0.898) | **0.929** | **0.064** (0.046–0.090) |

**The gate fails on precision and ECE.** Direct recall passes for the first time in a pooled
blind run, with 3 spans to spare (756 needed).

**12 direct misses**, 11 never proposed:
- a dictated passage in lower case, with a spaced NRIC and MRN, a spoken date of birth and a
  spoken address;
- an OCR'd phone with letters for digits (`6S38 2O14`) and an OCR'd `S/0` name;
- a lower-case full name, a lone given name, two pairs of initials;
- a three-digit emergency number labelled phone.

One pair of initials was proposed and sent to review with an SHI label.

**124 false positives** (29 on negative notes):
- **37 from the token sweep:** eponyms (`Hartmann`, `Calot`, `Murphy`, `Alvarado`), job titles
  (`Pathologist`, `Podiatrist`, `Caseworker`, `Testator`), demonyms and countries (`Malaysian`,
  `Bangladesh`, `Myanmar`), Singapore towns written alone (`Bedok`, `Tampines`), acronyms and HL7
  field codes (`MCR`, `EMR`, `XPN`, `XAD`).
- **87 from the other proposers**, as in earlier rounds: headings and captions, eponyms found by
  NER in a surgical teaching outline, HL7 and FHIR message IDs and URLs, a chatbot's `BOT` label,
  PF date fragments, organisation and role names.

### What caused the failure (post hoc; the blind row stands)
The judge is badly calibrated on swept words. Over 171 swept candidates, 8 were gold. The mean
calibrated confidence was 0.34 and the ECE 0.29. The calibration map was fitted before the sweep
existed, and the sweep's candidates are a population it never saw.

Re-scoring the blind run without parts of P25 gives (post hoc, so none of these is a result):

| variant | direct recall | precision | ECE | verdict |
|---|---|---|---|---|
| as run (P25) | 0.984 (759) | 0.879 | 0.064 | FAIL (precision, ECE) |
| without the token sweep | 0.979 (755) | 0.913 | 0.034 | FAIL (direct recall, by 1 span) |
| without fail-closed | 0.984 (759) | 0.882 | 0.064 | FAIL (precision, ECE) |
| without both | 0.979 (755) | 0.916 | 0.034 | FAIL (direct recall, by 1 span) |

The sweep bought 4 direct gold spans for 37 false positives and the ECE failure. Fail-closed kept
no direct gold on these sets and cost 7 false positives.

**Latency:** 63.9 s per 1k characters (mean); per-note p95 99.0 s, 131.1 s and 111.3 s.

**Other systems, F1 on v25/v26/v27:**
- slm:jev alone 0.921/0.908/0.953;
- rules + PF 0.884/0.843/0.933;
- PF 0.768/0.788/0.883;
- MediPhi 0.477/0.370/0.485.

notes_v25–v27 are dev sets from here on.

## Where the series stands
Four pooled blind rounds (P22–P25) have each failed a different check, by small margins: direct
recall by 9, 2, 3 spans; then precision and ECE. On about 800 direct gold spans, one span is
0.13 points of recall. Each round's fixes for the last round's misses cost precision on the next
writers' text, or the reverse. More rounds on synthetic sets by new writers mostly measure the
next writer's style. See the eval log and `docs/results.md`.

