# 0025: Ollama is the default backend, bundled portable in the kit

Date: 2026-10-10. Status: **accepted** (the user's decision). This is not a release, and the gate
is unchanged.

## Context
Decision 0024 added Ollama as a second backend. On notes_v27 (dev) it matched llama-server
within the gate: direct recall 0.996 on both, precision 0.932 vs 0.941, ECE 0.034 vs 0.032.
Antivirus did not freeze it. llama-server is flagged on the machines that matter, and hospital IT
allows Ollama. The user asked for Ollama as the default, in a kit that runs on a hospital laptop
by copying it over.

## Decision
- **Default.** `SLMJEV_BACKEND` now defaults to `ollama`; `llama` remains available.
- **Portable start.** When `SLMJEV_OLLAMA` names an `ollama.exe`, the engine starts it as
  `ollama serve`:
  - loopback only, on a free port;
  - with its own model store (`SLMJEV_OLLAMA_MODELS`, default `models` beside the exe);
  - `OLLAMA_NO_CLOUD=1`, `OLLAMA_NOPRUNE=1`, one model and one request at a time.
  - Otherwise the engine uses a running Ollama at `SLMJEV_OLLAMA_URL`.
- **Shutdown.** The started server sits in a kill-on-close job object. Stopping it ends its model
  runner too, so a crashed engine leaves nothing behind.
- **Same model or nothing.** Ollama has no API key. So, as in 0024, the engine judges only after
  `/api/show` reports the blob digest of `SLMJEV_JUDGE_MODEL`.
- **Kit contents.** `realval.py pack --ollama <Ollama install dir>` bundles `ollama.exe` and
  `lib\ollama`:
  - Left out: the GPU libraries (2.7 GB), the tray app, the uninstaller and the quantizer.
  - The CPU-only copy is about 60 MB.
  - The kit's own Ollama imports the judge with `ollama create` from the local GGUF, and the
    blob digest is checked.
  - `env.bat` selects Ollama whenever it is bundled. `--llama` is now optional.
- **Runtime DLLs.** Ollama's CPU files import only Windows system DLLs and the Universal CRT,
  which ship with Windows 10 and 11. No Visual C++ runtime is bundled for it.

## Consequences
- **Copies on disk.** The kit carries the judge twice: `models\` holds the GGUF the calibration
  and digest check refer to, and `ollama\models\blobs` holds Ollama's copy. That is about 1.1 GB
  more.
- **Calibration.** `calibration_p22.json` was fitted on llama-server output and is kept. On
  notes_v27 under Ollama it gives ECE 0.034, within the gate. Refitting on Ollama output is
  open; a release evaluation must say which backend and calibration it ran on.
- **Footprint.** Ollama may write a key pair to `%USERPROFILE%\.ollama` on first start. It does
  not use the network for local models. The engine's socket guard covers only the Python
  process, so the "no network" claim for `ollama serve` rests on loopback binding, cloud models
  being off and nothing ever being pulled.
- **Other callers.** structured_deidentification's `run_engine.py` now gets Ollama unless it
  sets `SLMJEV_BACKEND=llama`.
- **Antivirus.** IT's approval of an installed Ollama may not cover a copy run from a folder.
  The kit guide still asks IT to allow the kit folder first.
