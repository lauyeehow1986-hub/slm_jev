# Eval log

One row per eval run, appended and never edited. Synthetic data only. Gate thresholds are in
CLAUDE.md, *Release gate*. A check that was not run is INCOMPLETE, never PASS.

| Date | Run | Model / config | Data | Direct recall (flagged) | Precision | ECE | F1 vs 0.889 | Latency | Gate | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-25 | P3 zero-shot judge (`eval/judge_eval.py`) | Qwen3-1.7B Q4_K_M, llama-server CPU; Choice v2b, all rotations, Noul+Score measured | dev seed 11, 20 notes, 180 oracle candidates | 0.983 | auto 0.980; flagged 0.895 (oracle proposer) | 0.042 (uncalibrated) | not run | 2.5 s/candidate at 4 rotations; ≈93 s per 1k chars | **INCOMPLETE** | Oracle proposer; no calibration fit; no baseline set. See 0003. |
