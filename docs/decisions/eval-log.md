# Eval log

One row per eval run, appended and never edited. Synthetic data only. Gate thresholds are in
CLAUDE.md, *Release gate*. A check that was not run is INCOMPLETE, never PASS.

| Date | Run | Model / config | Data | Direct recall (flagged) | Precision | ECE | F1 vs 0.889 | Latency | Gate | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | P3 zero-shot judge (`eval/judge_eval.py`) | Qwen3-1.7B Q4_K_M, llama-server CPU; Choice v2b, all rotations, Noul+Score measured | dev seed 11, 20 notes, 180 oracle candidates | 0.983 | auto 0.980; flagged 0.895 (oracle proposer) | 0.042 (uncalibrated) | not run | 2.5 s/candidate at 4 rotations; ≈93 s per 1k chars | **INCOMPLETE** | Oracle proposer; no calibration fit; no baseline set. See 0003. |
| 2026-09-25 | P4 calibrated judge, end to end (`eval/judge_eval.py --prod --calibration`) | Qwen3-1.7B Q4_K_M, llama-server CPU; Choice only, 4 rotations, rule-certain fast path, column context; temperature T=3.70 fitted on train seed 21 (drop < 0.0246, accept ≥ 0.572) | test seed 31, 40 notes + 60 cells, 382 oracle candidates (252 gold) | 0.992 (all 0.992) | auto 0.978; flagged 0.877 (oracle proposer) | 0.025 (calibrated; raw 0.035) | not run | 2.2 s/model-judged candidate (p95 3.4); ≈82 s per 1k chars p50 | **INCOMPLETE** | Oracle proposer; no baseline set; review 0.147; 2 misses (DOB in `procedure_date`), 5 false accepts. See 0004. |
