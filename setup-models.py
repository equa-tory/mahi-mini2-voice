"""Run in the voice image, once per host. Model files never enter Git."""
from pathlib import Path
from voice_models import ensure_models, download
root=Path('/models/whisper-cpp');root.mkdir(parents=True,exist_ok=True)
download('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin',root/'ggml-small-q5_1.bin')
ensure_models('ru_RU-irina-medium')
download('https://models.silero.ai/models/tts/ru/v5_6_ru.pt',Path('/models/silero-v5_6_ru.pt'))
print('Models ready. Use sha256sum on both hosts to confirm identical Whisper models.')
