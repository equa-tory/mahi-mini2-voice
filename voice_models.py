import json
import os
import pathlib
import urllib.request
import zipfile

ROOT = pathlib.Path("/models")
def download(url,destination):
    if destination.is_file():return
    temporary=destination.with_suffix(destination.suffix+".download")
    try:
        with urllib.request.urlopen(url,timeout=60) as source, temporary.open("wb") as target:
            while data:=source.read(1024*1024):target.write(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

def ensure_models(tts_model):
    ROOT.mkdir(exist_ok=True)
    name="vosk-model-small-ru-0.22"
    model=ROOT/name
    if not (model/"am/final.mdl").is_file():
        archive=ROOT/(name+".zip")
        download("https://alphacephei.com/vosk/models/"+name+".zip",archive)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not (ROOT/entry.filename).resolve().is_relative_to(ROOT.resolve()):
                    raise ValueError("invalid model archive path")
            if z.testzip() is not None:raise ValueError("corrupt model archive")
            z.extractall(ROOT)
        archive.unlink()
    # Start with a known Russian voice; do not build arbitrary URLs from config.
    if tts_model!="ru_RU-irina-medium":raise ValueError("unsupported TTS model")
    prefix="https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/"
    for suffix in (".onnx",".onnx.json"):
        download(prefix+tts_model+suffix,ROOT/(tts_model+suffix))
    return str(model),str(ROOT/(tts_model+".onnx"))
