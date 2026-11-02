FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client libgomp1 libespeak-ng1 && rm -rf /var/lib/apt/lists/*
COPY voice-requirements.txt /tmp/requirements.txt
ARG TARGETARCH
RUN if [ "$TARGETARCH" = arm64 ]; then sed '/^faster-whisper==/d' /tmp/requirements.txt > /tmp/arm-requirements.txt && pip install --no-cache-dir -r /tmp/arm-requirements.txt; else pip install --no-cache-dir -r /tmp/requirements.txt; fi
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.9.1
RUN groupadd -g 1000 automation && useradd -m -u 1000 -g 1000 automation
WORKDIR /app
COPY setup-models.py action_catalog.py voice_lease.py voice_asr.py recordings.py action_args.py voice.py voice_intents.py voice_models.py voice_tts.py voice_config.py voice_ipc.py voice_numbers.py ./
USER 1000:1000
CMD ["python", "-u", "voice.py"]
