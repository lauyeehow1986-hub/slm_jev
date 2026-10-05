# 0023: Stop the blind rounds, fix the configuration, and hand it to the SD backend

Date: 2026-10-06. Status: **accepted**. This is **not** a release: no configuration has passed the
pooled blind gate, and the gate is unchanged.

## Context
Four pooled blind rounds (P22–P25, decisions 0019–0022) each failed by a small margin:

| round | direct recall | precision | ECE | F1 | failed on |
|---|---|---|---|---|---|
| P22, notes_v16–v18 | 0.970 | 0.945 | 0.014 | 0.956 | direct recall (9 spans) |
| P23, notes_v19–v21 | 0.979 | 0.900 | 0.031 | 0.933 | direct recall (2 spans) |
| P24, notes_v22–v24 | 0.977 | 0.951 | 0.021 | 0.963 | direct recall (3 spans) |
| P25, notes_v25–v27 | 0.984 | 0.879 | 0.064 | 0.929 | precision, ECE |

Each round's fixes for the last round's misses cost something on the next writers' text. More
rounds on synthetic sets mostly measure the next writer's style. The open question is how the
system does on the text it is meant for, and only governed real data can answer that.

## Decision

### 1. Stop the synthetic blind rounds
No fifth round. notes_v1–v27 and sd20 stay dev sets. The gate in `CLAUDE.md` is not lowered.

### 2. One configuration, used by default
- **Judge:** the P5 LoRA Qwen3-1.7B GGUF (`models/p5/…-Q4_K_M.gguf`, gitignored).
- **Calibration:** `models/calibration_p22.json` (gitignored), with its thresholds.
- **Proposers:** `jev+pf+ner.person`, as since 0019.
- **Fail closed (P25):** kept. A span found by an ID rule or an ID/person-slot shape that the judge
  would drop goes to review instead (`fail_closed:<source>`). On the P25 blind sets it cost 7 false
  positives and kept no direct gold, but it is the project's rule: a missed identifier costs more
  than an extra flag.
- **Token sweep (P25):** **off by default.** It is opt-in: `"token_sweep": true` in the request,
  `SLMJEV_TOKEN_SWEEP=1` in the environment, or `--token-sweep` on `eval/bench.py`. A request's
  `false` beats the environment. On the blind run the calibration map, fitted before the sweep
  existed, was badly wrong on swept words (171 candidates, 8 gold, ECE 0.29), and they caused the
  precision and ECE failures.

Re-scoring the P25 blind run with the sweep off (post hoc, so not a result) gives direct recall
0.979 (755 of 771, one span short of 756), precision 0.913, ECE 0.034. That is about where P24
stood. This configuration is the best-measured one, not a passing one.

### 3. P6: the structured_deidentification backend, refreshed
The `slm:jev` backend in structured_deidentification (branch `claude/slm-jev-backend`, P6 since
2026-09-26) still works with this code:
- the base branch (`claude/shiny-deidentification-app-6a7932`, now with PR #1's free-text overlap
  fix) merged cleanly into the backend branch;
- the smoke suite passes: `run_all.R` 5 of 5 suites, `smoke5_jev.R` live 22 of 22 (one live scan,
  5.1 s);
- `docs/ner_packaging.md` there now has a status note: slm_jev has not passed its gate; use the P5
  judge with `calibration_p22.json`; leave the sweep off; keep the reviewer step; validate on a
  governed local sample first.

The backend is not yet in SD's default branch. That needs a pull request.

## What comes next
1. **Merge the backend into SD** through a pull request, with slm:jev still opt-in there.
2. **Validate on governed real data, locally.** The data controller approves a small sample. A
   reviewer annotates it on the target machine, and nothing leaves that machine. The same
   `eval/pool.py` gate is applied, and the result is recorded as counts only, never as text. Not
   one character of that sample is committed.
3. **Recalibrate only on real data,** if step 2 shows the map is off. If the sweep comes back, it
   needs its own calibration first.
4. **Latency** (about 64 s per 1k characters on this CPU) still limits slm:jev to small files and
   the review queue, not bulk runs.

## Code
- `slmjev/engine.py`: `ENV_TOKEN_SWEEP`, `sweep_vocab(cfg)`; `settings()` reads `token_sweep`;
  `scan()` passes the vocabulary only when the sweep is on.
- `eval/bench.py`: `--token-sweep`.
- `tests/test_engine.py`: the sweep is off unless asked for; a request's setting beats the
  environment.
