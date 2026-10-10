# Validating slm:jev on governed real data

This is the step after decision 0023. The goal is to measure slm:jev on the text it is meant for,
with the same gate and tools as the synthetic rounds:

```
direct recall ≥ 0.98, precision ≥ 0.90, ECE ≤ 0.05, F1 > 0.889
```

The rule throughout is simple: **real data stays on the approved machine, and only counts leave
it.**

> **Keep every cloud service away from the data.** A Claude Code session sends what it reads to
> Anthropic. Run these steps yourself on the approved machine. Never paste real text, file
> contents or error lines with patient data into Claude, the TypeSafe cloud Jev or any web tool.
> Only the counts from step 9 may be shared.

## The portable kit
Everything runs from one folder that you copy onto the machine: no install and no admin rights.
`eval/realval.py pack` builds it on the development laptop:

```
python eval/realval.py pack --out C:/slmjev_kit ^
    --judge models/p5/slmjev-judge-p5-qwen3-1.7b-Q4_K_M.gguf ^
    --calibration models/calibration_p22.json ^
    --ollama "%LOCALAPPDATA%\Programs\Ollama" ^
    --python <portable Python with Presidio, spaCy en_core_web_lg, onnxruntime, tokenizers> ^
    --pf-model <Privacy Filter model folder> ^
    --sd-repo <structured_deidentification checkout> --sd-commit <commit with run_engine.py>
```

What the kit holds:

| folder | what |
|---|---|
| `slm_jev\` | this repository at one commit, committed files only |
| `sd\` | structured_deidentification at one commit (its `run_engine.py` engines) |
| `python\` | a portable Python 3.12 that runs Privacy Filter, Presidio and spaCy |
| `pf\` | the Privacy Filter ONNX model |
| `ollama\` | a portable, CPU-only copy of Ollama (`ollama.exe` and `lib\ollama`, without the GPU libraries) and its own model store, `ollama\models`, with the judge already imported |
| `models\` | the judge GGUF and its calibration (`calibration.json`) |
| `smoke\` | four synthetic notes for the smoke test |

It also has numbered `.bat` launchers whose paths are all relative to the kit folder,
`README_KIT.txt`, and `MANIFEST.sha256`. The token sweep stays off.

**The judge runs on Ollama** (decision 0025):
- Each run starts the kit's own `ollama serve` on a free loopback port, with cloud models off
  and nothing pulled, and stops it at the end.
- Before judging, the engine checks that the model Ollama serves is, byte for byte, the GGUF in
  `models\`.
- No install and no Visual C++ runtime are needed: Ollama uses only the runtime built into
  Windows 10 and 11.
- An Ollama already installed on the machine is not used and not touched.
- `--llama <llama.cpp bin/Release folder>` also bundles llama.cpp in `llama\`. The kit still
  runs on Ollama; set `SLMJEV_BACKEND=llama` in `env.bat` to switch.

`pack` refuses to write into a git working tree or a synced folder. So do the kit's `hash` and
run commands, because the stray home repository (`C:\Users\<you>\.git`) picks up any folder
under it.

## Steps

### 1. Approvals and a written protocol
- Get the data controller's written approval, plus whatever your institution requires.
- Write a one-page protocol **before anyone sees results**:
  - purpose and gate (unchanged);
  - the sample (step 3) and the split;
  - who annotates, who runs the kit, and on which machine;
  - what leaves the machine (step 9);
  - retention and deletion.

### 2. Prepare the machine and copy the kit
- Use an approved, encrypted machine with no network and no OneDrive or other sync.
- Make a work folder outside any repository, for example `D:\realval\` with `sets\`,
  `reports\` and `smoke\` inside.
- Copy the kit folder, then run `1_verify_kit.bat`. It checks every file against the manifest
  and probes the judge's files.
- Run `2_smoke.bat D:\realval\smoke`, which runs four synthetic notes end to end in a few
  minutes.
- **Antivirus.** Ask IT to allow the kit folder in the endpoint protection *before* the smoke
  test. An approval for an installed Ollama may not cover a copy run from a folder.
  - Security software can quarantine an `.exe` in the kit, or freeze it silently.
  - The kit's Ollama runs the model in `ollama\lib\ollama\llama-server.exe`, signed by Ollama
    Inc.
  - On the development laptop, AVG/McAfee-class protection suspended every thread of a copied
    `llama-server.exe` (the Unsloth build) 10–30 s into judging. The original build folder was
    not affected, and neither were short test prompts. Ollama's own copy was not suspended.
  - The symptom is a run that stops with `judge backend down: 3 calls in a row failed`. The judge
    gives up after three failed calls (about 6 minutes) rather than sending every span to review
    at two minutes each.
  - In Task Manager, a frozen `llama-server.exe` (under `ollama.exe`) shows 0% CPU while the
    run waits.
- Optionally, reproduce a full synthetic set to check this CPU gives the same answers:
  ```
  5_run_set.bat <kit>\slm_jev\eval\bench\notes_v27.txt D:\realval\smoke\notes_v27.json
  ```
  It takes about 30 minutes. On the development laptop the configuration scored direct recall
  0.996, precision 0.941, ECE 0.032 and F1 0.965 on that set; this was a replay, so expect
  small differences.

### 3. Draw the sample
- **Stratify** by note type (discharge, referral, ED, letters, forms, messages), department and
  year. Include notes with no PII.
- **Size:** aim for **600–800 direct-identifier spans in part B**, the held-out part.
  - At that size one span moves recall by 0.13–0.17 points, and the 95% interval around 0.98
    is about ±1 point. Below about 400 spans a verdict turns on one or two spans; above about
    1,000, more spans add little.
  - Annotate about 10 notes first, and run `3_check_set.bat` on them to see the density.
  - At 3–5 direct spans per note, part B is about **120–270 notes**, and part A is half that
    (about 60–135). Add notes with no identifiers (10–15%) so false positives are tested.
  - Rare categories (fax, date of death, device) will have only a few spans: their recall is
    reported, but not conclusive.
- **Split by patient with a fixed seed, before any run:**
  - part A (about a third) is for diagnosis and, if needed, recalibration;
  - part B (about two thirds) is held out for the gate.
- Name notes neutrally (`rv_n001`). IDs and file names carry nothing about the patient.

### 4. Annotate, blind to the tool
- Use the format of the synthetic sets:
  - a header line per note, `=== rv_n001 | discharge ===`;
  - gold spans inline as `{{label|text}}`.
- Use the same labels and conventions. The header of `eval/bench/notes_v25.txt` is the guide:
  - honorifics stay outside name spans;
  - every mention is labelled;
  - how initials are handled;
  - `date_other` for care dates that are not a DOB or date of death.
- Annotators must not see slm:jev's output.
- Have two annotators label at least 20% of notes independently. Record their agreement, then
  settle the differences.
- Run `3_check_set.bat D:\realval\sets\part_B1.txt` on every file. It prints counts only and
  warns about:
  - duplicate note IDs;
  - lines starting with `# `, which the parser would drop as comments. Problem lists often have
    them, so indent such lines by one space.

### 5. Freeze
```
4_freeze.bat D:\realval\freeze.sha256 <kit folder> D:\realval\sets
```
Change nothing between the freeze and the score.

### 6. Run
Split part B into files of 30–50 notes and run each:
```
5_run_set.bat D:\realval\sets\part_B1.txt D:\realval\reports\B1.json
```
- Budget about 64 seconds per 1,000 characters on a CPU like the development laptop's.
  `3_check_set.bat` estimates the hours per file. Run overnight.
- Each run also records the rules, Presidio and Privacy Filter baselines on the same text.
- **Reports contain note text** (misses, false positives, judged candidates). They stay on this
  machine and are treated as PHI.

### 7. Score against the gate
```
6_pool.bat D:\realval\reports\pooled_B.json D:\realval\reports\B1.json D:\realval\reports\B2.json ...
7_verify_freeze.bat D:\realval\freeze.sha256
```
The verdict (PASS, FAIL or INCOMPLETE) is the result. Nothing afterwards changes it.

### 8. Analyse the errors, on the machine
Sort each miss and false positive into a cause, as the decision records do:
- never proposed;
- proposed but judged not an identifier;
- sent to review;
- by layout: table, header, chat, dictation, OCR.

Write the categories as **words and counts, never quoted strings**.

### 9. Take out only counts
- `pooled_B.json` holds rates, counts, intervals and report file names, but no text. Check it
  anyway, and have the data controller clear it.
- Bring out that file, the cause counts, the annotator agreement and the latency. From those, the
  eval-log row and decision record can be written without seeing any data.

### 10. Decide, then clean up
- **PASS:** slm:jev can be offered for this kind of text in structured_deidentification. Keep the
  reviewer step.
- **FAIL:** diagnose on part A. If calibration is the problem, refit it on part A
  (`eval/fit_bench_calibration.py`). Part B is now a dev set, so **confirming any fix needs a
  fresh held-out sample**.
- Delete the reports and annotated sets as the protocol says. Keep only the hashes and the
  counts.
