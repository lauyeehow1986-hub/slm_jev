# Results: slm:jev against structured_deidentification's detectors (P7)

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

## Caveats

- **Synthetic.** Both sets are invented. notes_v1 was written by the author of the P2 generator,
  so its style is closer to the training data than real notes would be.
- **Small.** Real text, whether a licensed de-identification corpus or governed local data under
  the data controller's approval, is the user's decision and is out of scope here.
- **Found on the benchmark.** The errors above were found on these sets. A fix for any of them
  must be checked on a new, unseen set, or the benchmark becomes a training set. Nothing in
  slm_jev was changed in response to these results.
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

- Use `--reuse <earlier report>` to re-run only some systems.
- `bench.py` stops if a model path does not exist. SD's engines return `[]` on any error, and the
  benchmark must not score that as a real zero.
