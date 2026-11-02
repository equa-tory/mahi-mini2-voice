"""Local-only speech synthesis, with swappable voices."""
import io
import pathlib
import wave
from voice_models import download

class Speech:
    def __init__(self,config,piper_path):
        self.engine=config.get("tts_engine","piper")
        self.volume=float(config.get("tts_volume",.65))
        if not 0<=self.volume<=1:raise ValueError("tts_volume must be 0..1")
        if self.engine=="piper":
            from piper import PiperVoice,SynthesisConfig
            self.voice=PiperVoice.load(piper_path)
            self.synthesis=SynthesisConfig(volume=self.volume)
        elif self.engine=="silero":
            import torch
            torch.set_num_threads(4)
            path=pathlib.Path("/models/silero-v5_6_ru.pt")
            download("https://models.silero.ai/models/tts/ru/v5_6_ru.pt",path)
            self.voice=torch.package.PackageImporter(str(path)).load_pickle("tts_models","model")
            self.voice.to(torch.device("cpu"))
            self.speaker=config.get("tts_speaker","xenia")
            if self.speaker not in self.voice.speakers:raise ValueError("unknown Silero speaker")
        else:raise ValueError("unknown TTS engine")

    def wav(self,text,speaker=None):
        buffer=io.BytesIO()
        with wave.open(buffer,"wb") as output:
            if self.engine=="piper":
                self.voice.synthesize_wav(text,output,syn_config=self.synthesis)
            else:
                import torch
                import numpy as np
                with torch.inference_mode():
                    audio=self.voice.apply_tts(text=text,speaker=speaker or self.speaker,
                        sample_rate=24000,put_accent=True,put_yo=True)
                pcm=np.clip(audio.cpu().numpy()*self.volume*32767,-32768,32767).astype(np.int16)
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
                output.writeframes(pcm.tobytes())
        return buffer.getvalue()
