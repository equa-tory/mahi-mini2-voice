"""Resident local recognition backends; no downloads or action execution."""
import io
import json
import os
import platform
from pathlib import Path
import re
import urllib.request
import uuid
import wave
from types import SimpleNamespace


class WhisperCpp:
    def __init__(self):
        self.model_file = None
        self.model_ready = False
        self.url = os.environ.get("WHISPER_CPP_URL", "http://127.0.0.1:8788/inference")

    def transcribe(self, samples, language=None, beam_size=1, initial_prompt=None, **_):
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
        if self.model_file and self.model_ready:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.url.rsplit("/",1)[0]+"/model", timeout=15) as response:
                self.model_ready = response.read(256).decode().strip() == "/models/"+self.model_file
        if self.model_file and not self.model_ready:
            server_operation("load", self.model_file)
            self.model_ready = True
        fields = {"language": language or "auto", "response_format": "json",
                  "suppress_non_speech": "true",
                  "temperature": "0", "temperature_inc": "0", "best_of": "1",
                  "beam_size": str(max(1, min(5, int(beam_size))))}
        if initial_prompt:
            fields["prompt"] = initial_prompt[:400]
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
    def __init__(self, model_file=None):
        self.gpu = WhisperCpp()
        self.gpu.model_file = model_file
        self.cpu = load_asr("small")
        self.backend = model_backend(model_file)
        if model_file:
            try:
                server_operation("load", model_file)
                self.gpu.model_ready = True
            except OSError:pass  # Keep the CPU fallback ready during GPU recovery.

    def transcribe(self, samples, **kwargs):
        try:
            result = self.gpu.transcribe(samples, **kwargs)
            self.backend = model_backend(self.gpu.model_file)
            return result
        except (OSError, ValueError, TimeoutError):
            self.gpu.model_ready = False
            # Materialize segments here: faster-whisper inference is lazy.
            segments, info = self.cpu.transcribe(samples, **kwargs)
            result = list(segments), info
            self.backend = "faster-whisper-small-cpu-fallback"
            return result


GPU_MODELS = {"small-cpp": "ggml-small-q5_1.bin", "turbo-cpp": "ggml-large-v3-turbo-q5_0.bin"}

def model_backend(model_file):
    return "whisper-cpp-" + (model_file or "configured").removeprefix("ggml-").removesuffix(".bin")

def server_operation(operation, model_file=None):
    url = os.environ.get("WHISPER_CPP_URL", "http://127.0.0.1:8788/inference")
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("whisper.cpp must use a local HTTP endpoint")
    boundary = uuid.uuid4().hex
    data = b""
    if model_file:
        if model_file not in GPU_MODELS.values():raise ValueError("unsupported GPU model")
        data = f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n/models/{model_file}\r\n'.encode()
    if model_file:data += f"--{boundary}--\r\n".encode()
    request = urllib.request.Request(url.rsplit("/",1)[0]+"/"+operation, data=data,
        headers={"Content-Type":f"multipart/form-data; boundary={boundary}" if model_file else "application/octet-stream"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=30) as response:
        return response.read(4096)

def unload_asr():
    try:server_operation("unload")
    except OSError:pass  # An offline GPU service owns no usable recognition model.

def hallucinated_transcript(text):
    normalized = re.sub(r"[^а-яёa-z ]", " ", text.lower())
    normalized = " ".join(normalized.split())
    return bool(normalized in ("продолжение следует", "blank audio", "music", "тишина") or
                re.match(r"^(?:субтитры(?: сделал| подготовил)?|редактор субтитров)\b", normalized))

def load_asr(name):
    if name == "off":return None
    if name in GPU_MODELS:
        backend = WhisperCpp()
        from urllib.parse import urlsplit
        url = urlsplit(backend.url)
        if url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("whisper.cpp must use a local HTTP endpoint")
        model_file=GPU_MODELS[name]
        if not (Path("/models/whisper-cpp")/model_file).is_file():raise ValueError("GPU model is not installed: "+model_file)
        if cpu_fallback_enabled():return ResilientWhisperCpp(model_file)
        backend.model_file=model_file
        server_operation("load", model_file)
        backend.model_ready=True
        return backend
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4,
                        download_root="/models", local_files_only=True)
