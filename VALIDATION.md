# Validation — 2026-11-03

- Python runtime-image suite: 52 tests passed, including catalog migration, editable
  default responses, templates, argument extraction, spoken numbers, configuration
  transfer, backend health and lease fencing.
- Speaker: Go race tests and `go vet` passed; Linux ARMv7 binary built and installed
  after backing up the previous binary and controls configuration.
- Browser: 46 catalog entries loaded; legacy trigger/response editors absent; action
  save succeeded; global default response survived a save/reload and was restored
  to «Готово.»; no JavaScript errors.
- Live speaker: demo, greeting, face and weather cancellation cleared their displays;
  a canceled demo did not resume; custom seven-second face duration verified;
  inactive cancellation fell through; pairing discovery ended; picker and timer
  cancellation succeeded. Ordered rules retained assistant → timer → picker → local.
- Main server: pinned CUDA 12.8.1 image built with `sm_61`, sees Tesla P4 with driver
  580.178.04, and uses its CUDA backend. Disable flash attention at build and runtime
  together to avoid falling back to CPU attention on this configuration.
- A public upstream 11-second JFK sample, using the same small Q5_1 model and request
  settings, produced matching English transcripts: server CPU warm request 4.315 s;
  packaged P4 GPU warm request approximately 0.3 s. These are one-sample checks,
  not a broad accuracy/latency benchmark.
- Pi 5: fresh public clone, ARM64 CPU and voice images built and started on 64-bit
  Debian 13. Fixed Vosk's ARM64 dependency on `libatomic.so.1`. Whisper model SHA-256
  matched the primary. Voice and CPU services were constrained to two CPU cores.
- Live Pi ownership, interruption through the speaker's active-owner URL, and automatic
  recovery to the primary after the stability interval passed. Software microphone
  mute disabled listening on both nodes and the original setting was restored.
- Runner unavailable, Whisper unavailable, and primary-worker freeze tests all passed:
  Pi takeover took 8.2, 9.2 and 9.1 seconds respectively; primary recovery took about
  71 seconds after health was restored. Speaker capture count stayed at most one.
- Manual snapshot export, private copy and import on the Pi succeeded; it keeps its
  local copy rather than depending on the primary for configuration reads.
- Tracked source and the complete public history were audited for private keys,
  tokens, live config/state, audio, models and private host paths.

The Pi lost power/network connectivity during the first recognition benchmark.
After reboot its kernel repeatedly reported `Undervoltage detected!`. Pi recognition
latency remains unmeasured; further load testing was deferred. Software ownership
checks do not establish hardware reliability under these power conditions.

Recent voice diagnostics and cancellation update: 58 Python tests pass (two optional dependency tests skipped on the host), Go race tests and vet pass, and JavaScript syntax checks pass. Browser verification saved and reloaded a custom history length, retained existing history across worker restart, and loaded the global cancel catalog entry without page errors. Live speaker checks confirmed the configured timer → picker → assistant → local cancellation order and timer, picker, and local cancellation operations. Standby ownership remains disabled on the deployed speaker.

## Russian-only mode, resident GPU model and CPU fallback

- 60 Python tests passed (two optional host NumPy skips). Russian-only routing,
  legacy bilingual routing, service failure and recovery have regression coverage.
- Compared eight saved clips on P4 Small Q5 and Large-v3 Turbo Q5: Small took
  0.16–0.23 s; Turbo 0.25–0.56 s. Turbo recovered one fast time query missed
  by Small; another similar query still failed. This is a spot check, not WER.
- Turbo used 1094 MiB VRAM with the tested 512-frame audio context.
- Live backend test with an unreachable GPU endpoint used the resident Small CPU
  fallback (8.82 s); restoring the endpoint used Turbo GPU again (0.50 s).
- CPU-only profiles remain available. GPU/CPU backend is recorded in request
  history and displayed in voice status. No model is reloaded per request.

## Selectable GPU models, unload and hallucination diagnosis

- 64 Python tests pass (two optional host NumPy skips). Covers Small/Turbo model
  mapping, missing model rejection, paused lease ownership, valid unload request
  encoding, and known subtitle hallucinations versus normal Russian commands.
- Live dashboard config switches successfully loaded GPU Small and Turbo.
- Unload freed model weights: GPU process memory fell to 118 MiB (CUDA context),
  listening became false, phase became models_unloaded, and voice lease released.
- Replaying the same recent timer clip with full context recovered its timer
  words; reduced context had produced unrelated words. Full context measured
  about 0.86–0.96 s per short clip. Beam width 5 and higher normalization gain
  offered no consistent improvement, so neither was adopted.
- Pre-wake buffer extended from one to three seconds to preserve fast commands.
  Quiet subtitle hallucination recordings still cannot be recovered reliably;
  raw text/audio remain visible and the result is explicitly ignored.
