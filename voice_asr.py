"""Resident local recognition backends; no downloads or action execution."""
import io
import json
import os
import platform
import urllib.request
import uuid
import wave
from types import SimpleNamespace


class WhisperCpp:
    def __init__(self):
        self.url = os.environ.get("WHISPER_CPP_URL", "http://127.0.0.1:8788/inference")

    def transcribe(self, samples, language=None, **_):
        import numpy as np
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        audio = io.BytesIO()
        with wave.open(audio, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(pcm)
        boundary = uuid.uuid4().hex
        parts = []
        fields = {"language": language or "auto", "response_format": "json",
                  "temperature": "0", "temperature_inc": "0", "best_of": "1",
                  "beam_size": "1"}
        context = int(os.environ.get("WHISPER_CPP_AUDIO_CONTEXT", "0"))
        if context:
            if not 256 <= context <= 1500:
                raise ValueError("audio context must be 0 or 256..1500")
            # Never shorten context below the submitted audio plus one second.
            context = min(1500, max(context, (len(samples) + 15999) // 16000 * 50 + 50))
            fields["audio_ctx"] = str(context)
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="command.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode() + audio.getvalue() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(self.url, data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        # Never send microphone audio through an environment-configured proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=15) as response:
            result = json.load(response)
        if not isinstance(result.get("text"), str):
            raise ValueError("invalid whisper.cpp response")
        return [SimpleNamespace(text=result["text"])], None


def cpu_fallback_enabled():
    return os.environ.get("VOICE_CPU_FALLBACK", "1" if platform.machine() in ("x86_64", "AMD64") else "0") == "1"


class ResilientWhisperCpp:
    """Keep both models resident; retry the GPU on every new request."""
    def __init__(self):
        self.gpu = WhisperCpp()
        self.cpu = load_asr("small")
        self.backend = os.environ.get("VOICE_ASR_BACKEND", "whisper-cpp")

    def transcribe(self, samples, **kwargs):
        try:
            result = self.gpu.transcribe(samples, **kwargs)
            self.backend = os.environ.get("VOICE_ASR_BACKEND", "whisper-cpp")
            return result
        except (OSError, ValueError, TimeoutError):
            # Materialize segments here: faster-whisper inference is lazy.
            segments, info = self.cpu.transcribe(samples, **kwargs)
            result = list(segments), info
            self.backend = "faster-whisper-small-cpu-fallback"
            return result


def load_asr(name):
    if name == "small-cpp":
        backend = WhisperCpp()
        from urllib.parse import urlsplit
        url = urlsplit(backend.url)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("whisper.cpp must use a local HTTP endpoint")
        if cpu_fallback_enabled():return ResilientWhisperCpp()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{url.netloc}/health", timeout=3) as response:
            if json.load(response).get("status") != "ok":raise ValueError("whisper.cpp is not ready")
        return backend
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4,
                        download_root="/models", local_files_only=True)
