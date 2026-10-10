# slm_jev

This project is an offline extractor for **PII and sensitive health information (SHI)** in
Singapore-context data. It pairs a small local language model with a *Jev-style* judgment layer,
which answers typed questions with calibrated probabilities instead of generating text:
- yes/no: does this span identify a person?
- which of the 15 SingHealth identifiers or SHI categories is it?
- how sensitive is it?

It is a research and governance tool, **not for clinical or diagnostic use**. It runs fully
offline, and this repository holds only code, docs and synthetic data.

## Status (2026-10-06)
- **Built and integrated.** The engine works end to end. It plugs into
  [structured_deidentification](https://github.com/lauyeehow1986-hub/structured_deidentification)
  as an opt-in `slm:jev` backend
  ([PR #2](https://github.com/lauyeehow1986-hub/structured_deidentification/pull/2)).
- **Not released.** No configuration has passed the pooled blind gate: direct-identifier recall
  ≥ 0.98, precision ≥ 0.90, ECE ≤ 0.05, F1 > 0.889. The synthetic blind rounds are now stopped
  ([0023](docs/decisions/0023-release-configuration-and-the-sd-backend.md)).
- **Next:** validation on governed real data, on the target machine, with the data controller's
  approval. The procedure and a portable, no-install kit (`eval/realval.py pack`) are in
  [docs/real_data_validation.md](docs/real_data_validation.md).

See [CLAUDE.md](CLAUDE.md) for the design, constraints and phase history,
[docs/results.md](docs/results.md) for every benchmark, and
[docs/decisions/](docs/decisions/) for the reasoning behind each step.

## How it works
```
rules → candidate spans → batched typed judgments → calibrated spans → reviewer
```
1. **Propose.** Code proposes candidate spans:
   - Singapore rules: NRIC/FIN checksum, phones, postal codes, MRNs, dates and more. They are
     ported from structured_deidentification's R detectors and parity-tested against them.
   - Layout shapes: ID columns, person slots, initials and table cells.
   - Privacy Filter and Presidio person spans.
2. **Judge.** A LoRA-finetuned Qwen3-1.7B (Q4_K_M GGUF, served by a loopback Ollama) reads
   each candidate in context. Probabilities come from option-token logprobs, averaged over 4
   rotated option orders.
3. **Calibrate and decide in code.** A per-call calibration map turns scores into probabilities.
   Thresholds in code then accept, drop or send the span to review. The model never writes a
   value; it only picks among options that code gave it.
4. **Fail closed.**
   - An unsure or failed judgment goes to the reviewer.
   - So does a rule-found ID the judge would drop.
   - A failed scan exits with an error, never with "no spans".

```
echo {"texts": ["Pt Tan Ah Kow, NRIC S1234567D ..."]} | python -m slmjev.engine
```
Settings:
- `SLMJEV_JUDGE_MODEL` and `SLMJEV_CALIBRATION`.
- `SLMJEV_OLLAMA` (a portable `ollama.exe` to start) or `SLMJEV_OLLAMA_URL` (a running Ollama).
- `SLMJEV_BACKEND=llama` with `SLMJEV_LLAMA_SERVER` runs on llama-server instead.

`--probe` checks them.
The models and calibration files are not in git.

## Results at a glance (synthetic only)
Here are the four pooled blind rounds. Each round was 96 notes by three new writers, scored once
on frozen code:

| round | direct recall | precision | ECE | F1 | verdict |
|---|---|---|---|---|---|
| P22, notes_v16–v18 | 0.970 | 0.945 | 0.014 | 0.956 | FAIL: direct recall, 9 spans short |
| P23, notes_v19–v21 | 0.979 | 0.900 | 0.031 | 0.933 | FAIL: direct recall, 2 spans short |
| P24, notes_v22–v24 | 0.977 | 0.951 | 0.021 | 0.963 | FAIL: direct recall, 3 spans short |
| P25, notes_v25–v27 | 0.984 | 0.879 | 0.064 | 0.929 | FAIL: precision and ECE |

Other systems, F1 on the P25 blind sets (v25 / v26 / v27):

| system | v25 | v26 | v27 |
|---|---|---|---|
| slm:jev alone | 0.921 | 0.908 | 0.953 |
| rules + Privacy Filter | 0.884 | 0.843 | 0.933 |
| Privacy Filter | 0.768 | 0.788 | 0.883 |
| MediPhi-3.8B | 0.477 | 0.370 | 0.485 |

Earlier, 3 of 14 single-set blind runs passed every check: notes_v6, notes_v8 and notes_v13. A
repeat on notes_v14 with the same frozen code failed.

## Strengths
- **Accuracy:** it beats the baselines on every blind set. Pooled F1 is 0.93–0.96 across the
  four rounds. On the P25 sets slm:jev alone scored 0.91–0.95, against 0.84–0.93 for SD's
  strongest current combination (rules + Privacy Filter). MediPhi stayed under 0.51 on these
  realistic multi-paragraph notes.
- **Recall on direct identifiers** is within a few spans of 0.98 on 771–925 direct gold spans per
  round, and every miss is listed with its cause.
- **Calibrated probabilities.** Pooled ECE is 0.014–0.031 when the proposer stays in the
  population the map was fitted on. A threshold therefore means what it says.
- **Explainable decisions.** Every span records its proposer, category distribution, sensitivity
  and review flag. Thresholds live in code, not in a prompt.
- **Safe by construction:**
  - air-gapped, with non-loopback sockets forbidden;
  - fails closed;
  - the model never generates replacement values;
  - SHI is labelled as well as the 15 identifiers.
- **Fits the target machine:** a model share under 4 GB, CPU only, 16 GB RAM.
- **Drop-in:** it uses structured_deidentification's span contract and transport. The R side
  needed only an opt-in mode.
- **Governance-ready process:** frozen-code blind runs, an eval log where a skipped check reads
  INCOMPLETE and never PASS, and decision records for every phase.

## Weaknesses
- **Slow:** about 60 s per 1k characters on CPU, with a per-note p95 of about 90–160 s. That suits
  small files and a review queue, not bulk runs.
- **Recall is capped by the proposer.** The judge cannot accept a span nobody proposed. Most
  misses in every round were never proposed: names in new places (a name tag, a group chat, a
  lone given name, a table cell), lower-case dictation, OCR'd digits.
- **Precision drifts with new writing styles.** False positives come from headings, eponyms, job
  titles, demonyms, organisation names, HL7/FHIR codes and message IDs.
- **Calibration is population-bound.** The map is fitted on the proposer's known candidates. A new
  kind of candidate breaks it: the P25 token sweep scored ECE 0.29 on its own candidates.
- **Fixes trade against each other.** Each round's fixes for the last round's misses cost
  precision on the next writers' text, or the reverse.
- **Heavy setup:** a GGUF judge, a calibration file, a model server (a portable Ollama;
  antivirus has frozen a copied llama-server before) and a bundled Python for Privacy Filter.

## Gaps
- **No real-data evidence.** Every number comes from synthetic notes written by agents from
  briefs. How it does on real clinical text is unknown until a governed local validation.
- **Synthetic-set limits.** The sets are small (27 sets of about 30 notes, one writer each), and
  all of them are now dev sets. notes_v1 shares an author with the training generator.
- **SHI is under-measured.** The SHI label list is provisional, and the gate counts identifiers
  only. SHI recall is reported but not gated.
- **Some metrics are not scored:** sensitivity and category quality, beyond identifier
  overlap.
- **Documents and structured data are partial.** PDF, XML and ECG inputs depend on
  structured_deidentification's later phases. Structured-cell judging exists but has had less
  testing than free text.
- **The backend is not yet in structured_deidentification's default branch** (PR #2 is open).
- **No hardware tier above 1.7B has been tried** (a larger tier needs a decision record), and
  no GPU path.

## Learning points
1. **Select, don't generate.** Asking a small model to choose among typed options, then reading
   its logprobs, gives calibratable probabilities and auditable decisions. Generating spans or
   replacements gave neither. On realistic notes, SD's generative LLM backend fell to F1 0.09
   with 4 notes per call, before a parser fix.
2. **Recall is a proposer problem.** Once the judge was finetuned, almost every miss was a span
   that nothing proposed. What ended a run of three failed recall gates was feeding other
   engines' spans in as candidates (Privacy Filter, Presidio persons; 0009), not judge tuning.
3. **Calibrate on the population you will score.** The P5 map looked calibrated on single-span
   rows and was not on whole notes (ECE 0.143). A per-call map fitted on whole notes fixed that.
   It broke again when a new proposer added candidates the map had never seen.
4. **Fix-on-the-benchmark turns the benchmark into training data.** Every set used to find a fix
   became a dev set. Only frozen-code blind runs by new writers count, and pooling three writers
   per round gave tighter confidence intervals than single sets.
5. **Near the gate, one span is noise.** At about 800 direct gold spans, one span is 0.13 points
   of recall. Four rounds each failed by 2–9 spans, or by precision. More synthetic rounds mostly
   measured the next writer's style, so the remaining question needs real data.
6. **Fail-closed is cheap insurance.** Sending rule-found IDs to review instead of dropping them
   cost 7 false positives on the P25 blind sets. It saved no direct gold there, but the P24 blind
   run had lost three MRNs and an NRIC to judge drops.
7. **Broad sweeps need their own calibration.** The vocabulary token sweep recovered names no cue
   covered, but it cost precision and ECE until its candidates have their own calibration map.
   It is off by default.
8. **Process matters as much as the model:**
   - hash the code before a blind run;
   - log every run, failures included;
   - never let a post-hoc analysis change a blind row;
   - report INCOMPLETE rather than PASS when a check is skipped.

## Safety
- **No network at runtime:** the engine forbids non-loopback connections, and it never downloads
  models or packages.
- **No PHI in git:** never commit real patient data, even redacted. Fixtures are synthetic, with
  example.com-style contacts.
- **Internal standards stay local:** internal anonymisation standards are mapped locally and kept
  out of this repository. Committed code is policy-agnostic (`slmjev/policy.py`).
- **Responsibility:** the data controller remains responsible for confirming that
  de-identification is adequate before any release.
