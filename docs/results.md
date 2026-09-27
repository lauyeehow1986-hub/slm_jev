# Results: slm:jev against structured_deidentification's detectors (P7 to P15)

Date: 2026-09-26. **Synthetic data only.** Every number here comes from invented notes. None of
them says how any system does on real clinical text (see *Caveats*).

## Setup

**Systems.** Each one runs as structured_deidentification (SD) calls it: one `run_engine.py`
process per system, JSON on stdin.

| system | what it is |
|---|---|
| rules only | the ported SG detectors (`slmjev/rules.py`, R-parity tested) |
| Presidio + spaCy NER | SD's `ner` engine, `en_core_web_lg` |
| Privacy Filter | SD's `pf` engine, a token classifier |
| MediPhi-3.8B, Qwen2.5-3B | SD's `llm` engine: llama-cli, Q4_K_M, temperature 0, `n_predict` 1024, `ctx` 4096 |
| **slm:jev** | this repo: proposer, then the P5 judge (Qwen3-1.7B LoRA, Q4_K_M) with calibration `calibration_p5.json`, `PROD` settings |
| rules + X | the union of the rules and system X, as SD merges engines |

- Every system ran on the same CPU laptop.
- For slm:jev, review spans count as predictions: a span sent to the reviewer is flagged, not missed.

**Sets.**
- **`sd20`**: SD's own 20-note LLM A/B set, the one behind "MediPhi F1 0.889", rebuilt exactly
  by `eval/bench/make_sd20.R`. The notes are short, one or two sentences each.
- **`notes_v1`**: 22 hand-written notes in realistic styles: discharge summary, referral letter,
  ED triage in capitals, lab report, radiology, psychiatry, genetics, a death note, a work-permit
  holder, device serials, and 4 notes with no PII.
  - It holds 102 gold spans, 11 of them SHI.
  - **It is not independent of the P2 generator**, because the same author wrote both.

**Scoring.** The scoring is in `eval/bench.py` and `tests/test_bench.py`.
- **Recall** is SD's A/B metric: a gold span counts as found if any prediction overlaps it,
  whatever the type.
- **Covered** is stricter: the predictions together must cover every letter and digit of the
  gold span, which is what redaction needs.
- **Precision** counts a prediction as wrong only if it overlaps no gold span of any kind, so a
  correct date or SHI hit is never counted against a system.
- **Views:**
  - *identifiers* is the 15 SingHealth identifiers.
  - *incl. other dates* adds admission, appointment and procedure dates. They are not among the
    15, and slm:jev leaves them to policy by design.
- **SHI** is scored separately.

**Scorer check.** Applied to SD's saved A/B predictions from 2026-09-06, `bench.py` reproduces
SD's published figures exactly:
- MediPhi: F1 0.889 (recall 0.800, precision 1.000);
- Qwen2.5-3B: F1 0.868.

The re-runs below differ from those by one to three spans, from llama.cpp run-to-run variation.

## Results

### sd20 (SD's A/B set): 20 notes, 1221 characters

28 identifier spans (30 counting other dates), 0 SHI spans, 4 notes with no PII.

| system | recall | covered | precision | F1 | FP (on negative notes) | recall incl. other dates | SHI recall | s per 1k chars |
|---|---|---|---|---|---|---|---|---|
| rules only | 0.429 | 0.429 | 1.000 | 0.600 | 0 (0) | 0.433 | n/a | 0.0 |
| Presidio + spaCy NER | 0.643 | 0.607 | 0.621 | 0.632 | 11 (5) | 0.633 | n/a | 1.8 |
| Privacy Filter | 0.964 | 0.893 | 1.000 | 0.982 | 0 (0) | 0.967 | n/a | 3.9 |
| MediPhi-3.8B | 0.893 | 0.857 | 0.963 | 0.927 | 1 (0) | 0.867 | n/a | 130.5 |
| Qwen2.5-3B | 0.750 | 0.750 | 0.955 | 0.840 | 1 (0) | 0.733 | n/a | 90.9 |
| **slm:jev** | 0.929 | 0.893 | 1.000 | 0.963 | 0 (0) | 0.867 | n/a | 40.6 |
| rules + NER | 0.964 | 0.929 | 0.738 | 0.836 | 11 (5) | 0.967 | n/a | - |
| rules + Privacy Filter | 1.000 | 0.929 | 1.000 | 1.000 | 0 (0) | 1.000 | n/a | - |
| rules + MediPhi | 0.893 | 0.857 | 0.975 | 0.932 | 1 (0) | 0.867 | n/a | - |

slm:jev per note: p50 40 s, p95 114 s per 1k characters.

| label | n | rules only | Presidio + spaCy NER | Privacy Filter | MediPhi-3.8B | Qwen2.5-3B | slm:jev |
|---|---|---|---|---|---|---|---|
| address | 2 | 0.000 | 0.500 | 1.000 | 1.000 | 0.500 | 0.500 |
| dob | 1 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| email | 2 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| fax | 1 | 1.000 | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 |
| name | 14 | 0.000 | 1.000 | 1.000 | 0.786 | 0.571 | 0.929 |
| national_id | 3 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| phone | 4 | 1.000 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |
| postal_code | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

#### SD's LLM backend on sd20: as shipped vs parser fixed

| system | recall | covered | precision | F1 | FP (on negative notes) | recall incl. other dates | SHI recall | s per 1k chars |
|---|---|---|---|---|---|---|---|---|
| MediPhi, 4 notes per call | 0.893 | 0.857 | 0.962 | 0.926 | 1 (0) | 0.867 | n/a | 57.6 |
| Qwen2.5-3B, 4 notes per call | 0.750 | 0.750 | 1.000 | 0.857 | 0 (0) | 0.733 | n/a | 42.0 |
| MediPhi, 1 note per call | 0.893 | 0.857 | 0.963 | 0.927 | 1 (0) | 0.867 | n/a | 147.2 |
| Qwen2.5-3B, 1 note per call | 0.750 | 0.750 | 0.955 | 0.840 | 1 (0) | 0.733 | n/a | 91.0 |
| MediPhi, 1 per call, parser fixed | 0.893 | 0.857 | 0.963 | 0.927 | 1 (0) | 0.867 | n/a | 130.5 |
| Qwen2.5-3B, 1 per call, parser fixed | 0.750 | 0.750 | 0.955 | 0.840 | 1 (0) | 0.733 | n/a | 90.9 |

### notes_v1: 22 notes, 6018 characters

84 identifier spans (93 counting other dates), 11 SHI spans, 4 notes with no PII.

| system | recall | covered | precision | F1 | FP (on negative notes) | recall incl. other dates | SHI recall | s per 1k chars |
|---|---|---|---|---|---|---|---|---|
| rules only | 0.488 | 0.452 | 1.000 | 0.656 | 0 (0) | 0.516 | 0.000 | 0.0 |
| Presidio + spaCy NER | 0.571 | 0.500 | 0.459 | 0.509 | 59 (7) | 0.559 | 0.000 | 0.4 |
| Privacy Filter | 0.893 | 0.845 | 0.894 | 0.893 | 9 (0) | 0.903 | 0.000 | 1.4 |
| MediPhi-3.8B | 0.881 | 0.869 | 0.900 | 0.890 | 8 (1) | 0.806 | 0.000 | 45.3 |
| Qwen2.5-3B | 0.881 | 0.881 | 0.709 | 0.786 | 34 (0) | 0.871 | 0.000 | 45.1 |
| **slm:jev** | 0.952 | 0.952 | 0.976 | 0.964 | 2 (0) | 0.860 | 0.818 | 42.6 |
| rules + NER | 0.905 | 0.809 | 0.636 | 0.747 | 59 (7) | 0.914 | 0.000 | - |
| rules + Privacy Filter | 0.964 | 0.917 | 0.935 | 0.949 | 9 (0) | 0.968 | 0.000 | - |
| rules + MediPhi | 0.941 | 0.905 | 0.940 | 0.940 | 8 (1) | 0.925 | 0.000 | - |

slm:jev per note: p50 41 s, p95 79 s per 1k characters.

| label | n | rules only | Presidio + spaCy NER | Privacy Filter | MediPhi-3.8B | Qwen2.5-3B | slm:jev |
|---|---|---|---|---|---|---|---|
| address | 5 | 0.000 | 0.600 | 1.000 | 1.000 | 1.000 | 1.000 |
| case_visit | 2 | 1.000 | 0.500 | 0.500 | 0.000 | 1.000 | 1.000 |
| date_of_death | 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| device | 3 | 0.667 | 0.333 | 1.000 | 0.667 | 1.000 | 1.000 |
| dob | 4 | 0.500 | 0.750 | 1.000 | 1.000 | 1.000 | 1.000 |
| email | 4 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| fax | 1 | 1.000 | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 |
| mrn | 3 | 1.000 | 0.000 | 0.667 | 1.000 | 1.000 | 1.000 |
| name | 33 | 0.000 | 0.909 | 0.909 | 0.879 | 0.818 | 0.939 |
| national_id | 9 | 1.000 | 0.000 | 0.889 | 1.000 | 1.000 | 1.000 |
| other_id | 3 | 0.333 | 0.000 | 0.667 | 0.667 | 0.667 | 0.333 |
| phone | 11 | 1.000 | 0.455 | 0.909 | 0.909 | 0.909 | 1.000 |
| postal_code | 5 | 1.000 | 0.800 | 1.000 | 0.800 | 0.800 | 1.000 |

#### SD's LLM backend on notes_v1: as shipped vs parser fixed

| system | recall | covered | precision | F1 | FP (on negative notes) | recall incl. other dates | SHI recall | s per 1k chars |
|---|---|---|---|---|---|---|---|---|
| MediPhi, 4 notes per call | 0.048 | 0.048 | 1.000 | 0.091 | 0 (0) | 0.043 | 0.000 | 28.7 |
| Qwen2.5-3B, 4 notes per call | 0.048 | 0.048 | 1.000 | 0.091 | 0 (0) | 0.043 | 0.000 | 30.1 |
| MediPhi, 1 note per call | 0.679 | 0.667 | 0.889 | 0.770 | 7 (1) | 0.624 | 0.000 | 46.6 |
| Qwen2.5-3B, 1 note per call | 0.691 | 0.691 | 0.741 | 0.715 | 21 (0) | 0.667 | 0.000 | 45.6 |
| MediPhi, 4 per call, parser fixed | 0.619 | 0.595 | 0.812 | 0.703 | 12 (3) | 0.581 | 0.000 | 27.6 |
| Qwen2.5-3B, 4 per call, parser fixed | 0.679 | 0.667 | 0.790 | 0.730 | 17 (2) | 0.634 | 0.000 | 30.3 |
| MediPhi, 1 per call, parser fixed | 0.881 | 0.869 | 0.900 | 0.890 | 8 (1) | 0.806 | 0.000 | 45.3 |
| Qwen2.5-3B, 1 per call, parser fixed | 0.881 | 0.881 | 0.709 | 0.786 | 34 (0) | 0.871 | 0.000 | 45.1 |

## What the errors are

### slm:jev

**Identifier misses: 3 on sd20 and 4 on notes_v1, 7 in total.** Each was checked against the
proposer's output.

| note | missed span | cause |
|---|---|---|
| sd20-05 | `Blk 123 Tampines St 11 #05-432` | Proposer gap. The street pattern does not know the abbreviation `St`, so only the unit `05-432` was proposed and flagged. The span was overlapped but not covered. |
| sd20-13 | `Woodlands Ave 6` | Proposer gap. A street with no block number or unit is not proposed. |
| sd20-15 | `Lim` in "Nurse Lim" | Proposer gap. `Nurse` is not an honorific cue, and a single capitalised word is not a name run. |
| n04 | `VIJAY` in "NOK: SON VIJAY" | Proposer gap. A single capitalised given name after a relation word. |
| n07 | `Aisyah` in "Caller: Aisyah (daughter)" | Proposer gap, the same shape. |
| n06 | lab number `24L0339127` | Judge drop. Proposed as an ID-like token, then judged not an identifier. |
| n10 | lab reference `GX-7781-24` | Judge drop, the same case. Specimen and lab reference numbers are absent from the synthetic training data. |

- Five of the seven are proposer gaps. The judge never saw those spans, so no amount of judge
  training fixes them.
- All seven were dropped with no review flag. That is **fail-open**, the failure mode this
  project exists to prevent. On the synthetic test sets (P6) there were none.

**False positives: 2 names on notes_v1.** Both are a name run that crossed a line break:
- `LABORATORY REPORT\nPatient Name`;
- `Kian Huat Construction\nPte Ltd`.

The proposer should not join words across a newline. Neither note is one of the no-PII notes.

**SHI: recall 0.818, precision 0.600 on notes_v1.**
- **Misses (2 of 11):** `12 weeks pregnant` and `MSM`. Neither is in the lexicon, and neither is
  a name shape.
- **False positives (6):**
  - `BROUGHT IN BY SCDF AMBULANCE` (p 1.00), `WORSE ON EXERTION` and `SOC ENDO` came from
    upper-case name runs that the Choice question put in `other_sensitive`. Whole-caps text is
    out of distribution for the judge.
  - `Medtronic Azure XT` was placed in `substance_use` with a flat distribution: 0.25 across four
    options.
  - `overdose` and `dementia` came from the SHI lexicon. `overdose` was placed in
    `substance_use`, while the gold marks the whole phrase `suicide attempt` as mental health.
    `dementia` is arguable, and the gold leaves it out.
- None of these six reveals an identifier, but each one sends a reviewer a wrong SHI flag.

### SD's LLM backend drops long notes (a bug in SD)

**What happened.** On notes_v1, SD's llama.cpp backend, as shipped, silently lost most of its
output.
- With 4 notes per call, both models found 4 of 84 identifier spans. Only the last batch, which
  held 2 notes, came back.
- With 1 note per call, the longest notes still came back empty. Recall was 0.68 for MediPhi and
  0.69 for Qwen.

**Cause.**
1. llama-cli echoes a long prompt cut off with "... (truncated)", which leaves a JSON quote open.
2. `detect_llm._parse_spans` tracks whether it is inside a string, so after the open quote it
   reads everything that follows as string content, including the model's correct answer.
   - In one traced note (n01), MediPhi's reply held 7 correct spans, and the parser returned 0.
3. Nothing is logged, and the note looks clean. This is fail-open.
4. SD's own A/B set has short notes whose echo is not truncated, so it never hit this. SD's
   default batch, `se.llm_batch` = 16, makes every prompt long, so in the app the bug would hit
   short notes too.

**Fix.** A JSON string cannot hold a raw newline, so the parser now resets its string state at
each newline. The fix is on SD branch `claude/slm-jev-backend`, uncommitted and awaiting the
owner's approval.
- With the fix and 1 note per call, MediPhi reaches recall 0.881 and Qwen 0.881 on notes_v1.
- With 4 notes per call, recall is still only 0.62 and 0.68. A batch of long notes needs more
  output than `n_predict` = 1024 allows, so the reply is cut off. One note per call avoids that,
  but on short notes each model load then dominates the time: 130 s per 1k characters on sd20.

The main tables use the fixed parser with 1 note per call, each LLM's best case. The "as shipped"
rows show what SD users get today.

### Privacy Filter, NER, MediPhi and Qwen

- **Privacy Filter** misses the header block of the discharge summary (name, NRIC, MRN, admission
  number), the radiology accession number, the polyclinic phone and fax, `VIJAY` and `Ong Beng Hock`.
  - Its 9 false positives are clinical fragments: `CD4`, `612`, `F / 58`, a drug dose, `MC`.
- **NER** finds names but has no Singapore ID formats. Its 59 false positives on notes_v1 include
  7 on the no-PII notes.
- **The LLMs** return nothing for SHI. SD's prompt does not ask for it, so their SHI column is not
  a fair comparison of models, only of SD as configured.

## Conclusions

1. **Identifiers.** On realistic-style notes, slm:jev has the best recall and precision of any
   single system. Rules + Privacy Filter overlaps slightly more gold spans but covers fewer of
   them in full, and it has more false positives. On SD's short-note set, Privacy Filter alone is
   as good or better.
   - slm:jev's remaining misses are mostly proposer gaps, and they are silent. The release gate
     says **any** recall regression on direct identifiers blocks release. Names are direct
     identifiers, so the gate stays closed.
2. **SHI.** slm:jev is the only system that finds SHI at all. Its precision of 0.6 means about
   one SHI flag in three would be wrong for a reviewer.
3. **Latency.** slm:jev takes about 40 s per 1k characters, the same order as SD's LLM backend,
   and about 10 to 30 times slower than Privacy Filter. On the 16 GB target this is usable only
   for batch jobs.
4. **Combining.** Adding Privacy Filter's spans as extra candidates (`extra=`, which the P6 engine
   already accepts) would propose every gap span on these sets except `VIJAY`; the judge would
   still have to accept them. That is the cheapest next step, not a model change.

## After P7: unseen-set rounds (P8 to P15)

Each round fixed the misses on the sets already seen, froze the code (`results/*_freeze.sha256`)
and ran once on a new set by a separate writer that nobody had read. Decision records:
`docs/decisions/0008-realistic-notes-fixes.md` (P8 to P10), `0009-ensemble-proposer.md`
(P11, P12), `0010-grouped-calibration.md` (P13), `0011-proposer-gaps-v6.md` (P14) and
`0012-precision-filters.md` (P15). Only the blind rows count for the gate.

### The blind series

| round | code | blind set | system | recall | precision | F1 | silent identifier misses |
|---|---|---|---|---|---|---|---|
| P8 | proposer and fast-path shapes | notes_v2 | slm:jev | 0.931 | 0.903 | 0.917 | 18 |
| P9 | more shapes | notes_v3 | slm:jev | 0.959 | 0.959 | 0.959 | 12 |
| P10 | more shapes | notes_v4 | slm:jev | 0.934 | 0.953 | 0.943 | 24 |
| P12 | engine spans as candidates | notes_v5 | slm:jev + PF + Presidio persons | **0.983** | **0.927** | **0.954** | 5 |
| P13 | P12 + a calibration map per kind of call | notes_v6 | slm:jev + PF + Presidio persons | **0.990** | **0.932** | **0.960** | 3 |
| P14 | P13 + the notes_v6 proposer gaps (0011) | notes_v7 | slm:jev + PF + Presidio persons | **0.993** | 0.897 | 0.943 | 2 |
| P15 | P14 + precision filters on name candidates (0012) | notes_v8 | slm:jev + PF + Presidio persons | **0.990** | **0.954** | **0.972** | 3 |

The shape-only rounds (P8 to P10) each failed on the next set, because each new writer brought
shapes nobody had listed. P12 keeps the judge in charge but lets Privacy Filter and Presidio
`person` spans in as candidates.

### notes_v5 (blind for P12): 32 notes, 24,431 characters

297 identifier spans (353 counting other dates), 24 SHI spans, 5 notes with no PII. Written by a
separate agent from a brief, in 32 styles: ED triage, pathology, a WhatsApp transcript, an
ambulance run sheet, coroner and deputyship letters, and others. Invented and marked synthetic.

| system | recall | covered | precision | F1 | FP (on negative notes) | recall incl. other dates | SHI recall | SHI precision | s per 1k chars |
|---|---|---|---|---|---|---|---|---|---|
| rules only | 0.391 | 0.276 | 1.000 | 0.562 | 0 (0) | 0.357 | 0.000 | n/a | 0.0 |
| Presidio + spaCy NER | 0.505 | 0.421 | 0.400 | 0.446 | 231 (46) | 0.496 | 0.000 | n/a | 0.1 |
| Privacy Filter | 0.845 | 0.781 | 0.951 | 0.895 | 13 (0) | 0.830 | 0.000 | n/a | 0.8 |
| MediPhi-3.8B | 0.609 | 0.596 | 0.785 | 0.686 | 46 (7) | 0.552 | 0.000 | n/a | 27.5 |
| Qwen2.5-3B | 0.643 | 0.636 | 0.761 | 0.697 | 72 (0) | 0.592 | 0.000 | n/a | 22.8 |
| slm:jev | 0.970 | 0.946 | 0.942 | 0.956 | 18 (6) | 0.816 | 0.583 | 0.341 | 44.0 |
| **slm:jev + PF + Presidio persons** | 0.983 | 0.976 | 0.927 | 0.954 | 23 (7) | 0.847 | 0.583 | 0.300 | 63.4 |
| rules + NER | 0.818 | 0.653 | 0.566 | 0.669 | 231 (46) | 0.776 | 0.000 | n/a | - |
| rules + Privacy Filter | 0.899 | 0.822 | 0.969 | 0.932 | 13 (0) | 0.878 | 0.000 | n/a | - |
| rules + MediPhi | 0.694 | 0.623 | 0.873 | 0.773 | 46 (7) | 0.643 | 0.000 | n/a | - |
| rules + Qwen2.5-3B | 0.734 | 0.667 | 0.839 | 0.783 | 72 (0) | 0.683 | 0.000 | n/a | - |

Per note, in s per 1k characters: slm:jev p50 50.6, p95 79.0; slm:jev + PF + Presidio persons
p50 63.9, p95 103.0. The time for the combined system includes the Privacy Filter and Presidio
runs.

| label | n | Privacy Filter | slm:jev | slm:jev + PF + Presidio persons |
|---|---|---|---|---|
| address | 13 | 1.000 | 1.000 | 1.000 |
| biometric | 2 | 0.500 | 1.000 | 1.000 |
| case_visit | 7 | 0.571 | 1.000 | 1.000 |
| date_of_death | 3 | 1.000 | 1.000 | 1.000 |
| device | 3 | 1.000 | 1.000 | 1.000 |
| dob | 8 | 1.000 | 1.000 | 1.000 |
| email | 11 | 0.818 | 1.000 | 1.000 |
| fax | 3 | 0.667 | 1.000 | 1.000 |
| mrn | 13 | 0.923 | 1.000 | 1.000 |
| name | 125 | 0.896 | 0.968 | 0.992 |
| national_id | 26 | 0.885 | 1.000 | 1.000 |
| other_id | 36 | 0.667 | 0.861 | 0.889 |
| phone | 32 | 0.844 | 1.000 | 1.000 |
| photo | 4 | 0.000 | 1.000 | 1.000 |
| postal_code | 11 | 0.909 | 1.000 | 1.000 |

**What the combined system still gets wrong on notes_v5.**
- **5 silent misses.** Four are reference numbers (`other_id`): a prescription number, a
  year-coded form number, a blood-bag number, and a spaced donation number. One is a single given
  name. Recall passes with one miss to spare: a sixth miss would make it 0.980.
- **Two partial covers.** A dormitory address is only partly covered, and a spaced donation
  number is only partly covered. Recall counts both as found; covered does not.
- **23 false positives.** Most are upper-case headings, organisation names, a clinician's surname
  and its possessive (`Dr Seet`, `Seet's`, which the gold does not label), and a one-letter
  answer (`Y`). 7 fall on the 5 negative notes.
- **SHI** stays weak: recall 0.583, precision 0.300. The SHI list is provisional and not
  DAFA-derived, and its gold is interpretive.

### Calibration on notes_v5

The blind run failed the calibration gate: ECE **0.143** against 0.05. It is measured at the
candidate level, over 389 candidates judged by the model (dropped ones included). A candidate
counts as right when it overlaps identifier or SHI gold. The judge's scores are saturated, and
three kinds of call were overconfident:
- SHI calls on negated, ordered-test or not-about-the-patient mentions came out near 0.99;
- some headings were called names;
- "none" calls on fragments of identifiers that another candidate already covered came out
  near 0.

| kind of call | ECE on notes_v5 (identity map) |
|---|---|
| identifier | 0.069 |
| none | 0.361 |
| SHI, lexicon-proposed | 0.423 |
| SHI, proposed by something else | 0.900 |
| rule fast path | 0.010 |

P13 (`docs/decisions/0010`) refits the reported confidence with one map per kind of call and
leaves every decision as it was. Fitted on notes_v1 to v4 and tested on notes_v5, ECE falls to
**0.042**, with recall, precision and every decision unchanged. notes_v5 was then added to the
fit, so notes_v6 is the blind check (below).

### notes_v6 (blind for P13): 32 notes, 24,568 characters

300 identifier spans, 28 SHI spans and 5 notes with no PII, from a separate writer working from
a new brief. Invented and marked synthetic. The P13 code and `models/calibration_p13.json` were
frozen (`results/notes_v6_blind_freeze.sha256`) before the set was first read, and the hashes
still matched after the run.

| system | recall | covered | precision | F1 | FP (on negative notes) | silent misses | SHI recall | SHI precision |
|---|---|---|---|---|---|---|---|---|
| **slm:jev + PF + Presidio persons** | **0.990** | 0.983 | **0.932** | **0.960** | 22 (4) | 3 | 0.679 | 0.352 |
| slm:jev alone | 0.957 | 0.930 | 0.951 | 0.954 | 15 (4) | 13 | 0.679 | 0.413 |
| rules + Privacy Filter | 0.920 | 0.837 | 0.967 | 0.943 | 14 (1) | 24 | 0.000 | n/a |
| Privacy Filter | 0.883 | 0.793 | 0.950 | 0.915 | 14 (1) | 35 | 0.000 | n/a |
| rules + NER | 0.830 | 0.663 | 0.554 | 0.665 | 250 (40) | 51 | 0.000 | n/a |
| MediPhi-3.8B | 0.373 | 0.363 | 0.910 | 0.529 | 19 (8) | 188 | 0.000 | n/a |
| Qwen2.5-3B | 0.343 | 0.333 | 0.802 | 0.481 | 34 (0) | 197 | 0.000 | n/a |

- **Calibration passes: ECE 0.024** over 408 candidates. By kind of call:

  | kind of call | n | ECE |
  |---|---|---|
  | identifier | 281 | 0.012 |
  | none | 43 | 0.051 |
  | SHI, lexicon-proposed | 25 | 0.067 |
  | SHI, proposed by something else | 22 | 0.087 |
  | rule fast path | 37 | 0.010 |

- **Latency:** 61.9 s per 1k chars (mean); per note p50 68.8 s, p95 101.2 s. slm:jev alone: p50
  45.0 s, p95 62.5 s.
- **Recall by label** is 1.000 everywhere except `name` 0.992 (131), `other_id` 0.972 (36) and
  `national_id` 0.960 (25).
- **3 silent misses:**
  - a name in Chinese script, which no proposer covers;
  - a reference number of a new shape;
  - a bare NRIC tail (`412D`, the last four characters), which no proposer covers.
- **2 partial covers:** an MRN with a space and a hyphen, and a long condominium address.
- **22 false positives.** Most are headings and phrases called names (`ADULT INPATIENTS`,
  `Original Message`), kinship terms (`Ah Ma`, `Papa`) and two protocol numbers called visit
  numbers.
- **The release configuration first ran separately.** The frozen run used the benchmark's default
  system list, which leaves out the release configuration. It ran straight afterwards, on the same
  frozen code, and was merged in with `--reuse` (`results/bench_notes_v6_blind.frozen.json`). By
  then only the other systems' aggregate scores had been seen, and nothing was changed.

### notes_v7 (blind for P14): 32 notes, 24,824 characters

295 identifier spans, 36 SHI spans and 5 notes with no PII, from a separate writer working from
the notes_v6 brief. Invented and marked synthetic. The P14 code and `models/calibration_p13.json`
were frozen (`results/notes_v7_blind_freeze.sha256`) before the set was first read, and the hashes
still matched after the run. This time the release configuration was in the system list.

| system | recall | covered | precision | F1 | FP (on negative notes) | silent misses | SHI recall | SHI precision |
|---|---|---|---|---|---|---|---|---|
| **slm:jev + PF + Presidio persons** | **0.993** | 0.986 | 0.897 | 0.943 | 33 (1) | 2 | 0.667 | 0.353 |
| slm:jev alone | 0.963 | 0.946 | 0.935 | 0.949 | 20 (1) | 11 | 0.667 | 0.407 |
| rules + Privacy Filter | 0.925 | 0.868 | 0.928 | 0.926 | 31 (1) | 22 | 0.000 | n/a |
| Privacy Filter | 0.885 | 0.844 | 0.897 | 0.891 | 30 (1) | 34 | 0.000 | n/a |
| rules + NER | 0.841 | 0.681 | 0.545 | 0.661 | 254 (41) | 47 | 0.000 | n/a |
| MediPhi-3.8B | 0.366 | 0.359 | 0.734 | 0.489 | 64 (2) | 187 | 0.000 | n/a |
| Qwen2.5-3B | 0.339 | 0.325 | 0.882 | 0.490 | 33 (0) | 195 | 0.000 | n/a |

- **The P14 rules did not fire on this set.** It has no Chinese-script name after a romanised
  one, no NRIC tail and no "sample" code, so it tests P13 in effect and says nothing about P14's
  fixes.
- **Precision fails the gate: 0.897 against 0.90.** One fewer false positive would not be
  enough: it takes 32 or fewer.
- **Direct-identifier recall 1.000** (250 spans). Recall is 1.000 on every label except
  `other_id`, 0.941 (34).
- **Calibration passes: ECE 0.042** over 414 candidates. By kind of call: identifier 0.035 (268),
  none 0.096 (38), SHI lexicon-proposed 0.073 (30), SHI other 0.067 (36), rule fast path 0.010
  (42).
- **Latency:** 73.8 s per 1k chars (mean); per note p50 74.8 s, p95 128.3 s, slower than
  notes_v6 (101.2 s). slm:jev alone: p50 50.7 s, p95 75.9 s.
- **2 silent misses**, both `other_id`: a short screening number (`SCR-118`) and a blood-unit
  number with spaces (`W1234 26 012345`, a shape like notes_v5's spaced donation number).
- **2 partial covers:** two nurses' full names after `SN`, each found only in part.
- **33 false positives**, against 20 for slm:jev alone. 16 of them are not in slm:jev alone's list:
  engine candidates bring them in.
  - 17 are headings, organisation or place names and roles called names: `Pass Division`,
    `Specialist Appointments`, `Eastshore GH`, `STUDY ENROLMENT`, `Clinical Geneticist`,
    `Coroner`, `Victim`.
  - Some are number fragments from the engine spans: `110` from a time, `67` (an age), `02`.
  - A kinship word `Ma` counts twice, and there are two protocol numbers called visit numbers.
- **Gold audit, after scoring.** It does not change the gate result. 7 of the 33 false positives
  are people the writer left unmarked, against the brief's "name every person":
  - `Joyce`, `Hi Joyce`, `Steph` and `Hi Steph`, in an e-mail thread's greetings and sign-offs;
  - `Mdm Rahimah` twice, in subject lines;
  - `LAU AH MOI`, in a forwarded SMS.

  These 7 names were then added to notes_v7's gold, with an errata line in its header. Re-scored
  on the corrected gold, with the same stored predictions and no model run:
  - the release configuration: R 0.993, P **0.919**, F1 0.955 (26 FPs);
  - slm:jev alone: P 0.958;
  - Privacy Filter: P 0.921.

  The blind gate result is the uncorrected one, FAIL. The corrected gold is for dev use from
  here on.

### notes_v8 (blind for P15): 32 notes, 23,512 characters

315 identifier spans, 33 SHI spans, 65 other dates and 5 notes with no PII, from a separate writer
whose brief asks for every person to be named, including in e-mail sign-offs and chat
transcripts. Invented and marked synthetic; one NRIC is invalid on purpose, and the header says so. The
P15 code and `models/calibration_p13.json` were frozen (`results/notes_v8_blind_freeze.sha256`)
before the set was first read, and the hashes still matched after the run.

| system | recall | covered | precision | F1 | FP (on negative notes) | silent misses | SHI recall | SHI precision |
|---|---|---|---|---|---|---|---|---|
| **slm:jev + PF + Presidio persons** | **0.990** | 0.984 | **0.954** | **0.972** | 15 (7) | 3 | 0.636 | 0.511 |
| slm:jev alone | 0.952 | 0.940 | 0.974 | 0.963 | 8 (6) | 15 | 0.636 | 0.657 |
| rules + Privacy Filter | 0.918 | 0.860 | 0.929 | 0.923 | 31 (5) | 26 | 0.000 | n/a |
| Privacy Filter | 0.886 | 0.829 | 0.909 | 0.897 | 28 (2) | 36 | 0.000 | n/a |
| rules + NER | 0.883 | 0.733 | 0.596 | 0.711 | 218 (38) | 37 | 0.000 | n/a |
| MediPhi-3.8B | 0.356 | 0.346 | 0.516 | 0.421 | 108 (26) | 203 | 0.000 | n/a |
| Qwen2.5-3B | 0.349 | 0.343 | 0.786 | 0.484 | 56 (0) | 205 | 0.000 | n/a |

- **Every gate passes.** Precision is back above 0.90 with room: 15 FPs, against 33 on notes_v7.
- **Direct-identifier recall 0.993** (274 of 276). Recall is 1.000 on every label except `dob`
  0.900 (10), `phone` 0.964 (28) and `other_id` 0.968 (31).
- **The P15 filters fired**: 20 candidates removed on this set, none of them gold. Among them are
  engine spans such as `ED Dr`, `Adm IP` and `Outpatient Pharmacy`, the organisation runs
  `Justice Courts` and `Kallang Bridge Law LLC`, a kinship `Ma` twice, and digit fragments.
- **Calibration passes: ECE 0.031** over 399 candidates. By kind of call: identifier 0.028 (276),
  none 0.012 (44), SHI lexicon-proposed 0.092 (28), SHI other 0.066 (15), rule fast path 0.010
  (36).
- **Latency:** 64.7 s per 1k chars (mean); per note p50 71.4 s, p95 119.0 s.
- **3 silent misses**, all shapes no rule proposes: a dotted reference number
  (`CT.26.0914.00382`), a 7-digit phone number written `9888 012`, and a date of birth in Chinese
  format (`2004年3月8日`).
- **2 partial covers**, both `other_id`: a blood-unit number with spaces and a check letter, and a
  family-court case number with a slash and a space.
- **15 false positives**: 12 go to review and 3 are marked as identifiers. 7 are on the negative
  notes.
  - Headings and places called names: `BME Advisory`, `Lecture Theatre`, `Handover`, `Managers`
    (from "Nurse Managers"), `KL tonite`, and a study-title word.
  - Document numbers called IDs: a protocol number, a device model number, a field-notice number
    and an ethics-board reference.
  - `EMR`, `abdo`, a name split across a line break with the next word (`L.` / `Son`), and
    `Clinic 21` as an address.
  - One comes from P15 itself. The sign-off rule proposes the word after `Yours faithfully,`, and
    with `Kallang Bridge Law LLC` no longer proposed as a name, it proposes `Kallang` alone. The
    judge sends it to review.

## Caveats

- **Synthetic.** Every set is invented. notes_v1 was written by the author of the P2 generator,
  so its style is closer to the training data than real notes would be.
- **Small.** Real text, whether a licensed de-identification corpus or governed local data under
  the data controller's approval, is the user's decision and is out of scope here.
- **Found on the benchmark.** A fix for an error found on a set must be checked on a new, unseen
  set, or the benchmark becomes a training set. P8 to P12 changed slm_jev in response to sd20 and
  notes_v1 to notes_v4, so those are dev sets now and their numbers are optimistic. Only each
  round's blind run counts, and notes_v5 to notes_v8 are dev sets from here on.
- **One writer per set.** Each blind set had one writer (an agent working from a brief). Eight
  sets of about 30 notes are still a narrow sample of how people write.
- **Run variation.** The llama.cpp baselines vary by one to three spans from run to run.
- **Different outputs.** slm:jev also outputs category, sensitivity and a review flag, which these
  metrics do not score. The other systems output a type or nothing.

## Reproduce

```
python eval/bench.py --set eval/bench/sd20.json --systems rules,ner,pf,mediphi,qwen,jev \
    --ner-python <SD bundle>/bin/python/python.exe --pf-model <pf model dir> \
    --llama-cli <SD bundle>/bin/llama/llama-cli.exe --mediphi <mediphi gguf> --qwen <qwen gguf> \
    --batch 1 --calibration models/calibration_p5.json --out results/bench_sd20.json
```

The release configuration on notes_v5, reusing the engine spans of the blind run (set the
`SLMJEV_*` paths and `SE_PYTHON` first, as for `jev`):

```
python eval/bench.py --set eval/bench/notes_v5.txt --systems jev+pf+ner.person \
    --calibration models/calibration_p5.json --reuse results/bench_notes_v5_blind.frozen.json \
    --out results/bench_notes_v5_ece.json
```

The P13 calibration is fitted from the release configuration's `judged` records. It is a
replay, so no model runs. The P13 thresholds are compared on the raw score, so the decisions stay
the same:

```
python eval/fit_bench_calibration.py --train results/cal_notes_v1.json results/cal_notes_v2.json     results/cal_notes_v3.json results/cal_notes_v4.json results/cal_notes_v5.json     --base models/calibration_p5.json --out models/calibration_p13.json
```

- Use `--reuse <earlier report>` to re-run only some systems.
- `bench.py` stops if a model path does not exist. SD's engines return `[]` on any error, and the
  benchmark must not score that as a real zero.
