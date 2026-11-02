# Mahi Mini 2 voice

Local speaker controls, a LAN dashboard, Russian wake detection, Whisper.cpp recognition,
and local speech. Clone and run with Docker Compose on a Linux NVIDIA server or a
64-bit Raspberry Pi 5. The speaker must already have local-mode firmware, SSH access,
`mini2-mode`, `mini2-mic`, and the controls daemon installed. This repository does not
flash firmware or connect music streaming services.

## Server setup

Install Docker Engine and its Compose plugin using [Docker's instructions](https://docs.docker.com/engine/install/).
On the GPU server also install the [NVIDIA container toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
Use a driver supporting CUDA 12.8. The P4 is Pascal (`sm_61`):
[NVIDIA lists CUDA 12.x as its last supported toolkit generation](https://docs.nvidia.com/datacenter/tesla/drivers/cuda-toolkit-driver-and-architecture-matrix.html).
The image builds pinned Whisper.cpp source with CUDA 12.8.1 and explicit architecture 61;
flash attention is disabled. GPU allocation uses [Compose device reservations](https://docs.docker.com/compose/how-tos/gpu-support/).

```sh
git clone https://github.com/equa-tory/mahi-mini2-voice.git
cd mahi-mini2-voice
cp .env.example .env
cp templates/actions.yaml actions.yaml
cp templates/voice.yaml voice.yaml
cp templates/weather.yaml weather.yaml
mkdir -p secrets models state
chmod 700 secrets state
```

Edit `.env`: generate `AUTOMATION_TOKEN` with `openssl rand -hex 32`, set the speaker
address, and set this host's reserved LAN address as `VOICE_PUBLIC_URL` (port 8787).
Install a speaker-authorized SSH private key as `secrets/speaker_key` (mode 600) and
verify the speaker host key before adding it to `secrets/known_hosts`. SSH uses port 2222.
Runtime containers use UID 1000; make the model/state directories and config files
writable by that UID, and the key readable by it. Do not commit live configuration.

```sh
docker compose build automation voice
docker compose run --rm --no-deps voice python setup-models.py
docker compose --profile gpu up -d --build
docker compose --profile gpu run --rm --no-deps --entrypoint nvidia-smi whisper-gpu
```

Open `http://YOUR_SERVER_LAN_ADDRESS:8787` and sign in with the shared token. Keep
port 8787 accessible only to trusted LAN hosts; Whisper binds loopback port 8788.
Do not run both Whisper profiles on the same host. Models download during the setup
step; normal Whisper recognition uses the local quantized `ggml-small-q5_1.bin` file.
Use the same file on both nodes and compare `sha256sum models/whisper-cpp/*.bin`.
Vosk wake recognition and Silero speech models are also stored locally, outside Git.
The first voice start retries until Whisper is ready.

## Speaker update

```sh
cd speaker
go test -race ./...
CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build -ldflags='-s -w' -o /tmp/mini2-controls .
```

Back up `/data/opt/bin/mini2-controls` and `/data/opt/local-mode/controls.yaml` on the
speaker. Upload the binary under a temporary name, stop the old controls daemon,
replace the binary, and start it using the speaker's existing local-ring init service.
`mini2ctl` remains a symlink to this binary. Import `controls.example.yaml` only for a
new installation; existing controls and action names are retained.

The dashboard's speaker configuration interface persists ordered conditional rules.
Existing center-tap rules gain the local cancellation check once during migration.
The `action_rules_version: 2` marker preserves subsequent edits, including removed rules.
Existing rule order is respected. The normal binding runs when no active rule matches.

In `/data/opt/local-mode/server.json`, configure `url` with the primary's LAN URL and
`token` with the same `AUTOMATION_TOKEN` used on both hosts. The daemon uses the
lease owner's URL for voice status/interrupt calls, so center tap reaches the Pi after
failover. Named server actions keep using the configured primary runner URL.

## Buttons & actions

The action catalog imports old `voice.yaml` aliases, intent triggers, and responses
without changing legacy files merely by reading them. Save actions to persist the
catalog. Built-ins, named speaker actions, named server actions, and feedback responses
have stable names. Edit phrases and response variants one per line; one variant is
chosen randomly per response. Blank action response fields use the editable Default response at the top of the catalog, initially «Готово.». Placeholder validation,
spoken numbers, argument extraction, result mappings, `/run`, and `mini2ctl action run`
remain supported. Legacy fields are still accepted when no catalog override exists.

Temporary displays default to 3 seconds. An action can use that default or a custom
1–86400 second duration. This applies to clock text/faces, weather, demos and greetings;
a demo's sequence stops at its duration. Timers and Bluetooth's 180-second pairing
window keep their own durations. `action cancel [INVOCATION_ID]` cancels active local
work; stale or inactive button conditions fall through. Demo/greeting cancellation
stops remaining steps and clears its presentation; face/weather cancellation restores
the clock; pairing cancellation disables discovery. Weather refresh continues.
`server_demo`, `server_example`, and `server_hello` remain runnable without cancellation.
Microphone, recognition, wake phrase and speech voice settings remain on Voice & phrases.

## Raspberry Pi standby

Use 64-bit Raspberry Pi OS (Bookworm or later), or a compatible Debian ARM64 OS.
Confirm `uname -m` prints `aarch64`. Install Docker and Compose, then clone this
repository and follow the same config, key, directory permission and model setup steps.
If needed, add your account to the Docker group and log in again before using Docker.

Set the Pi's reserved address in `.env` and use the same automation token:

```dotenv
VOICE_NODE=pi5
VOICE_ROLE=standby
VOICE_PUBLIC_URL=http://YOUR_PI_LAN_ADDRESS:8787
VOICE_ASR_BACKEND=whisper-cpp-arm64-cpu
```

```sh
docker compose build automation voice
docker compose run --rm --no-deps voice python setup-models.py
docker compose --profile cpu up -d --build
```

For a Pi sharing resources with other services, `.env` also accepts
`WHISPER_THREADS=2`, `WHISPER_CPU_LIMIT=2`, `VOICE_CPU_LIMIT=2`, and
`VOICE_MEMORY_LIMIT=2g`. These limit inference/voice load; they do not resolve
an unstable power supply.

The CPU image builds the same pinned source natively on ARM64 with CUDA and native
CPU tuning disabled. It uses four threads and the same quantized model. The ARM64
voice image uses Whisper.cpp; faster-whisper is omitted there. Do not select the
`base`/`small` faster-whisper profiles on ARM64.

The speaker grants a single 8-second voice lease. Nodes renew while their local runner
and Whisper health endpoints respond. A failed renewal stops capture and interrupts
local speech. The speaker-side capture loop checks the lease every 100 ms; a speaker-local file lock also prevents overlapping capture processes. The Pi
acquires after missed renewals. A recovered primary must remain healthy for 60 seconds;
the Pi then stops renewing and the primary waits for the old lease to expire before
acquiring. A health gap over three seconds resets that recovery interval. Process
restarts use fresh session IDs. Speaker and `/voice/status` report owner and ASR backend.
Hardware/software microphone mute checks remain in effect on either node.

## Manual configuration transfer

No automatic sync is required. The Pi retains its last imported config when the
primary is unavailable. Export only when desired, copy privately, and import on the Pi:

```sh
# On the primary; --speaker is optional.
docker compose exec automation python config_transfer.py export /state/mahi.snapshot.json --speaker
scp state/mahi.snapshot.json YOUR_PI:~/mahi-mini2-voice/state/mahi.snapshot.json
# On the Pi:
docker compose exec automation python config_transfer.py import /state/mahi.snapshot.json
```

Snapshots include action, voice, weather, and optionally speaker YAML. They exclude
`.env`, keys, model files, recordings and runtime state. Snapshots may contain private
action URLs or inline configuration values: keep them private and outside Git. Import
validates configurations before writing, retains bind-mounted file inodes, and applies
speaker YAML if supplied. It never contacts the primary. Voice changes apply when idle.

## Verification

```sh
python3 -m unittest discover -q
(cd speaker && go test -race ./... && go vet ./...)
node --check dist/app.js
docker compose --profile gpu config -q
docker compose --profile cpu config -q
```

To exercise failover, inspect `mini2ctl voice lease status`, stop primary `voice`, and
verify that the Pi becomes the sole owner after expiry. Repeat with the primary Whisper
service stopped and with the host offline. Restore primary services, verify the 60-second
recovery interval and non-overlapping ownership, and interrupt an active Pi request with
center tap. Test rear mute and software mute while each host owns the lease. Compare
recognition latency using the same WAV input and model on both nodes with
`python3 benchmark.py /path/to/sample.wav --language ru`.
