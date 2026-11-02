FROM python:3.12-alpine
RUN apk add --no-cache openssh-client && pip install --no-cache-dir PyYAML==6.0.2
RUN addgroup -g 1000 automation && adduser -D -u 1000 -G automation automation
WORKDIR /app
COPY config_transfer.py action_catalog.py recordings.py action_args.py runner.py weather.py dashboard.py voice_config.py voice_intents.py voice_ipc.py ./
COPY dist/ ./dist/
USER 1000:1000
CMD ["python", "-u", "runner.py"]
