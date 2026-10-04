# 0022: Fail closed on rule-found IDs, sweep rare capitalised words, then a fourth pooled blind gate (P25)

Date: 2026-10-04. Status: **accepted**. Dev replay on notes_v1–v24 and sd20: passes the gate on
every grouping, but these are dev sets. The blind gate on notes_v25–v27 is below.

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
