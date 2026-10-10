# 0024: Ollama as a second backend for the judge

Date: 2026-10-10. Status: **proposed**. The backend is in the code; making it the kit's default
waits on the user. This is not a release, and the gate is unchanged.

## Context
On the dev laptop, antivirus suspends any copied `llama-server.exe` partway through judging, even
though the build is Authenticode-signed (Unsloth AI Inc.). Only the original build folder runs, or
a copy that has an AV exception. Hospital IT already allows Ollama. Ollama 0.35 runs its own
signed `llama-server.exe` child (Ollama Inc.), and its native `/api/generate` endpoint returns
first-token logprobs in raw mode. Its `/v1` endpoint ignores logprobs.

## Decision
- **New backend.** `judge.Ollama` sends the same rendered Qwen3 prompt that llama-server builds,
  with thinking off and `raw: true`. It sets `num_predict: 1`, `temperature: 0`, `num_ctx: 4096`
  and `num_gpu: 0` (CPU only, as `-ngl 0`). It reads `logprobs[0].top_logprobs`.
- **Shared failure handling.** It uses the same loopback-only transport and `BackendDown` rule as
  `LlamaServer`.
- **Selection.** `SLMJEV_BACKEND=ollama` selects it. `SLMJEV_OLLAMA_URL` and
  `SLMJEV_OLLAMA_MODEL` default to `http://127.0.0.1:11434` and `slmjev-judge-p5`.
- **Same model or nothing.** The engine refuses to run unless Ollama serves the same file as
  `SLMJEV_JUDGE_MODEL`. It compares the blob digest from `/api/show` with the GGUF's SHA-256, so
  the calibration map stays bound to the model it was fitted on.
- **Default unchanged.** The default backend stays `llama`.
- **Import.** The model goes in with a two-line Modelfile: `FROM <gguf>` and
  `TEMPLATE {{ .Prompt }}`.
- **Air gap.** On a data machine, Ollama must run with `OLLAMA_NO_CLOUD=1`. The engine already
  refuses non-loopback URLs.

## Evidence (synthetic, dev set)
Both backends ran the same code on notes_v27 (32 notes, 26,803 chars), with
`jev+pf+ner.person`, `calibration_p22.json` and the P5 GGUF on CPU:

| backend | direct recall | precision | ECE | F1 | s per 1k chars | per-note p95 |
|---|---|---|---|---|---|---|
| llama-server (unsloth b11160) | 0.996 (261/262) | 0.941 | 0.032 | 0.965 | 57.3 | 117.2 |
| Ollama 0.35 (its llama.cpp fork) | 0.996 (261/262) | 0.932 | 0.034 | 0.960 | 52.4 | 109.8 |

- **Agreement.** 436 candidates matched. Calibrated confidence differs by a median of 0.000 and a
  p95 of 0.025.
- **Changed decisions.** 13 candidates changed decision or category:
  - 6 changed category inside review. Three headings moved from an SHI category to `name` or
    `none`, which puts them in the identifier view. These are Ollama's 3 extra false positives.
  - 5 moved into review under Ollama:
    - two stayed within a span that a rule kept anyway;
    - two had been drops under llama-server, and one of them is the `other_id` it recovered;
    - one is a job title.
  - 1 moved from review to kept: "Public Prosecutor", a false positive under both backends.
  - 1 was the failed judgment below.
  - The net effect was 3 more reviews, 2 fewer drops, 1 more true positive and 3 more false
    positives.
  - Ollama caught one `other_id` that llama-server dropped.
- **One failed judgment.** Ollama put 0.65–0.75 on a letter outside the option list (`M` for A–J)
  in one rotation for one candidate. The judge rejected that rotation as off-format and sent the
  span to review, as fail-closed requires.
- **Cause.** Both servers tokenised that prompt to the same 288 tokens. llama-server itself puts
  0.12 on the same invalid letter. So the cause is a fragile prompt plus different CPU kernels,
  not a prompt or cache bug.
- **Antivirus.** Over 23.5 min of judging, the Ollama child had 0 suspended threads.

## Consequences
- **Speed and gate.** Ollama is about as fast as llama-server on this CPU and passes every gate
  check on this dev set. It is not bit-identical, so a few near-tie decisions move.
- **Before release.** A release evaluation must name the backend it ran on.
- **Calibration.** The calibration map was fitted on llama-server output. It holds within 0.002 ECE
  here, but it should be refitted if Ollama becomes the shipped default.
- **Not done yet.** Kit integration waits on the user: env settings, Modelfile import step,
  `OLLAMA_NO_CLOUD=1` and the guide.
