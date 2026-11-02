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
