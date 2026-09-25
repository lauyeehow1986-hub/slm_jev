# 0002: Judge runtime is llama-server, called directly

Date: 2026-09-25. Status: accepted for development; P6 packaging still open.

## Context
The judge reads option-token logprobs from a local GGUF model, so the runtime must return the raw
first-token distribution. The candidates were:
- llama-cpp-python;
- the llama.cpp CLI or server, whose `llama-server.exe` Defender quarantined before (finetune_slm
  decision 0002);
- Unsloth Desktop / Studio, which bundles llama.cpp and runs it behind its own OpenAI-compatible
  API.

We installed Unsloth Studio on the dev laptop, with its installer read before it ran. We then
probed the runtimes with `eval/probe_runtime.py`, on synthetic text only, with Qwen3-1.7B Q4_K_M,
CPU only (`-ngl 0`), and a 13th-gen i7 with 14 threads.

## Findings
| Question | Unsloth API (`:8888`) | llama-server direct |
|---|---|---|
| Chat `logprobs` / `top_logprobs` | **rejected**: HTTP 400 "logprobs is not supported for chat completions" | yes |
| `/v1/completions` logprobs | hangs | yes |
| native `/completion` `n_probs` | 404 | yes |
| Probabilities independent of temperature and top-k (pre-sampling) | n/a | yes: identical at t=0, t=1/k=40 and t=1/k=1 |
| Shared-prefix KV reuse (`timings.cache_n`) | n/a | yes: 75–80% of prompt tokens reused per question |
| Latency per question (p50 / p95) | n/a | 0.18 s / 0.22 s |

- **Unsloth exposes its internal llama-server insecurely.** The internal llama-server it launches
  has no API key and allows CORS from every origin: its own log says "no API key is set and CORS
  allows all origins". Any web page open on the machine could therefore reach it, which is not
  acceptable once real data is involved.
- **The Unsloth-installed server hung.** After the proxied `/v1/completions` call, that server
  stopped answering even `/v1/models`.
- **The llama.cpp binary survived Defender.** The prebuilt that Unsloth installed (tag b11160,
  CUDA build) passed validation and was not quarantined.

## Decision
- **Runtime:** `llama-server`, launched by us and called directly over loopback. Launch it with:
  - `--host 127.0.0.1` and a per-session `--api-key`;
  - `--no-webui` and `--reasoning off`;
  - `-np 1` and `-ngl 0` (CPU, matching the target).

  The client uses only the standard library (`urllib`) and refuses any non-loopback URL.
- **Unsloth Studio is not in the judge path.** It stays on the dev laptop for two jobs:
  1. as the source of a Defender-tolerated llama.cpp build;
  2. for P5 QLoRA on the RTX 4060.

  Its tools and Cloudflare tunnel stay off.
- **Model:** Qwen3-1.7B Q4_K_M stays the judge.
  - Qwen3.8 exists officially only as 27B and larger, so it can't meet the ≤ 4 GB target.
  - Third-party "Qwen3.8-2B/4B-Distill" repos have unknown provenance and are not used.
  - Qwen3.8-27B is a candidate **teacher** for P5 on the dev laptop.

## Consequences for P3/P4
- **Order bias is severe.**
  - On a 4-way Choice asked in all four rotations, the per-label probability moved by 0.40 on
    average, and the top answer changed in 5 of 12 candidates.
  - Permutation averaging is mandatory: every Choice question is asked in all rotations.
- **Zero-shot answers are overconfident and too inclusive.**
  - The yes/no question "does this help identify a person" answered yes at p ≈ 1.0 for
    admission dates, ward/bed and `1/7` durations.
  - It got 7/12 right at a 0.5 threshold, and all 6 gold spans were accepted.
  - So question wording must separate *direct identifiers* from *context that the policy
    retains*, the Choice question must carry the decision, and calibration (P4) is needed.
- **Cost model:** ~0.2 s per question on this CPU. A note with 15 candidates × (1 Noul + 4 Choice
  rotations) costs ~15 s. Batching several candidates into one prompt is worth measuring in P3.
- **P6 packaging** must ship a CPU llama-server build next to the R app, and must re-check
  Defender on the target machine.

## Reproduce
```
llama-server -m <Qwen3-1.7B-Q4_K_M.gguf> --host 127.0.0.1 --port 8089 -c 4096 -np 1 -ngl 0 ^
  --no-webui --reasoning off --api-key <random>
set SLMJEV_LLM_KEY=<random>
uv run --system-certs python eval/probe_runtime.py --url http://127.0.0.1:8089
```
