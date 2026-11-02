"""LAN-only Russian wake phrase + multilingual voice commands. No audio files."""
import array
import collections
import io
import json
import math
import os
import pathlib
import queue
import shlex
import signal
import subprocess
import threading
import time
import urllib.request
import wave
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from voice_intents import classify, strip_wake
from action_catalog import voice_routes
from voice_models import ensure_models
from voice_ipc import VoiceControl
from voice_lease import VoiceLease
from voice_numbers import number, duration, clock

STOP = threading.Event()
STATE_LOCK = threading.Lock()
STATE = {"phase":"starting","wake_count":0,"command_count":0,"error":""}
# Preserve the bounded diagnostic history across worker restarts.
try:
    previous=json.loads(pathlib.Path("/state/status.json").read_text())
    limit=previous.get("history_limit",10)
    if isinstance(limit,int) and not isinstance(limit,bool) and 1<=limit<=200:
        STATE.update(history_limit=limit,history=previous.get("history",[])[:limit])
except (OSError,ValueError,TypeError):pass
ALLOWED = threading.Event()
AUDIO = queue.Queue(maxsize=20)
STREAM_LOCK = threading.Lock()
STREAM = None
SPEAKING = threading.Event()
LEASE = None

def status(**values):
    with STATE_LOCK:
        STATE.update(values)
        STATE["updated_at"]=int(time.time())
        p=pathlib.Path("/state/status.json")
        p.with_suffix(".tmp").write_text(json.dumps(STATE))
        p.with_suffix(".tmp").replace(p)

def voice_event(new=False, **values):
    """Keep the configured number of recent wake/request interactions in local status."""
    history=[dict(entry) for entry in STATE.get("history", [])]
    if new or not history:
        history.insert(0, {"at":int(time.time()), "outcome":"listening", "attempts":[]})
    history[0].update(values)
    status(history=history[:STATE.get("history_limit",10)])

HISTORY_AUDIO = pathlib.Path("/state/voice-history")

def record_request_audio(audio):
    HISTORY_AUDIO.mkdir(parents=True, exist_ok=True)
    current=STATE.get("history", [{}])[0] if STATE.get("history") else {}
    name=current.get("audio_id") or uuid.uuid4().hex
    with wave.open(str(HISTORY_AUDIO / (name+".wav")), "wb") as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(audio)
    for old in sorted(HISTORY_AUDIO.glob("*.wav"), key=lambda p:p.stat().st_mtime, reverse=True)[STATE.get("history_limit",10):]:
        old.unlink(missing_ok=True)
    voice_event(audio_id=name, audio_seconds=round(len(audio)/32000,2))

def ssh(command):
    return ["ssh","-T","-i","/secrets/speaker_key","-p","2222",
            "-o","BatchMode=yes","-o","ConnectTimeout=3",
            "-o","ServerAliveInterval=3","-o","ServerAliveCountMax=2",
            "-o","StrictHostKeyChecking=yes","-o","UserKnownHostsFile=/secrets/known_hosts",
            "root@"+os.environ["SPEAKER_HOST"],command]

def remote(argv, data=None, timeout=5):
    result=subprocess.run(ssh(shlex.join(argv)),input=data,capture_output=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError("speaker command failed")
    return result.stdout

def ctl(*args):
    readonly=args==("status",) or len(args)>1 and args[1] in ("status","get","selection") or args[:2]==("voice","lease")
    if LEASE is not None and not readonly:
        fence=json.loads(remote(["/data/opt/bin/mini2ctl","voice","lease","check",LEASE.node]))
        if fence.get("data") is not True:raise RuntimeError("voice ownership expired")
    response=json.loads(remote(["/data/opt/bin/mini2ctl",*args]))
    if not response.get("ok"):raise RuntimeError(response.get("error","speaker command failed"))
    return response.get("data")

def privacy_allowed(data):
    privacy=data.get("privacy",{})
    return (data.get("mode")=="local" and data.get("hardware_mic_muted",True) is False
            and data.get("mic_muted",True) is False
            and privacy.get("desired_muted",True) is False
            and privacy.get("applied_muted",True) is False
            and not privacy.get("error"))

def clear_audio():
    while True:
        try:AUDIO.get_nowait()
        except queue.Empty:return

def stop_stream():
    global STREAM
    with STREAM_LOCK:
        if STREAM and STREAM.poll() is None:
            STREAM.terminate()

def guard(control=None):
    while not STOP.is_set():
        try:
            allowed=LEASE is not None and LEASE.renew() and privacy_allowed(ctl("status"))
            status(voice_owner=ctl("voice","lease","status"),asr_backend=LEASE.backend)
        except Exception:
            allowed=False
        if allowed:
            changed=not ALLOWED.is_set()
            ALLOWED.set()
            if changed and control:control.cancelled.clear()
            status(listening_enabled=True,**({"phase":"idle"} if changed else {}))
        else:
            ALLOWED.clear()
            if control:
                control.cancelled.set()
                if control.interrupt_callback:control.interrupt_callback()
            stop_stream()
            clear_audio()
            status(phase="models_unloaded" if LEASE and LEASE.asr_model=="off" else "standby" if LEASE and LEASE.healthy and not LEASE.owned else "muted_or_disconnected",listening_enabled=False)
        STOP.wait(.5)

CAPTURE_COMMAND = """set -eu
exec 9>/tmp/mahi-voice-capture.lock
/usr/bin/flock -n 9 || exit 1
[ "$(cat /sys/class/gpio/gpio414/value)" = 1 ]
[ "$(cat /data/opt/local-mode/mic-muted)" = 0 ]
/data/opt/bin/mini2ctl voice lease check "$1" | /bin/grep -Eq '"data"[[:space:]]*:[[:space:]]*true'
# Only the lease-owning lock holder may clean up orphaned capture children.
for pid in $(pidof arecord 2>/dev/null || true); do
 parent=$(sed -n 's/^PPid:[[:space:]]*//p' /proc/$pid/status 2>/dev/null || true)
 [ "$parent" != 1 ] || kill "$pid" 2>/dev/null || true
done
/usr/bin/arecord -q -D hw:0,1 -t raw -f S16_LE -r 16000 -c 8 9>&- &
rec=$!
trap 'kill "$rec" 2>/dev/null || true' EXIT
trap 'exit 0' HUP INT TERM
while kill -0 "$rec" 2>/dev/null; do
 if ! /data/opt/bin/mini2ctl voice lease check "$1" | /bin/grep -Eq '"data"[[:space:]]*:[[:space:]]*true' || [ "$(cat /sys/class/gpio/gpio414/value)" != 1 ] || [ "$(cat /data/opt/local-mode/mic-muted)" != 0 ]; then
  kill "$rec" 2>/dev/null || true
  break
 fi
 sleep 0.1
done
wait "$rec"
"""

def read_microphone(settings,control):
    global STREAM
    while not STOP.is_set():
        if not ALLOWED.wait(.5):continue
        process=subprocess.Popen(ssh("sh -c "+shlex.quote(CAPTURE_COMMAND)+" voice-capture "+shlex.quote(LEASE.node)),
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        with STREAM_LOCK:STREAM=process
        warmup=20  # PDM capture startup has a large transient; discard two seconds.
        try:
            while ALLOWED.is_set() and not STOP.is_set():
                data=process.stdout.read(25600)  # 100 ms, 8 channels
                if len(data)!=25600:break
                if not ALLOWED.is_set():break
                if SPEAKING.is_set():
                    clear_audio()
                    continue
                if warmup:
                    warmup-=1
                    continue
                samples=array.array("h",data)
                config=settings();channel=int(config.get("microphone_channel",0))
                raw=samples[channel::8];gain=float(config.get("microphone_gain",4))
                amplified=array.array("h",(max(-32768,min(32767,int(value*gain))) for value in raw))
                mono=amplified.tobytes()
                control.recordings.feed(mono,ALLOWED.is_set())
                control.metrics(raw_rms=round(rms(raw.tobytes()),1),rms=round(rms(mono),1),
                    peak=max(abs(v) for v in amplified),gain=gain,clipped_samples=sum(abs(v)>=32767 for v in amplified),clipping_fraction=round(sum(abs(v)>=32767 for v in amplified)/len(amplified),4))
                try:AUDIO.put_nowait(mono)
                except queue.Full:
                    try:AUDIO.get_nowait()
                    except queue.Empty:pass
                    AUDIO.put_nowait(mono)
        finally:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            process.stdout.close()
            control.recordings.feed(b"",False)
            control.metrics(rms=0,raw_rms=0,peak=0)
            with STREAM_LOCK:
                if STREAM is process:STREAM=None
        STOP.wait(.5)

def rms(audio):
    values=array.array("h",audio)
    return math.sqrt(sum(x*x for x in values)/max(1,len(values)))

def indicate(phase):
    if LEASE is not None:
        try:
            if ctl("voice","lease","check",LEASE.node) is not True:return
        except Exception:return
    try:
        if phase in ("listening","thinking","speaking"):
            from voice_config import read_voice
            face=read_voice("/config/voice.yaml")[1].get("assistant_face","eyes")
            if face:remote(["/data/opt/bin/mini2ctl","clock","face",face,"30"])
        remote(["/data/opt/bin/mini2-indicate",phase,"20"] if phase!="idle"
               else ["/data/opt/bin/mini2-indicate","idle"])
    except Exception:pass

def degrees(n):
    value=abs(n)%100
    if 11<=value<=14:return "градусов"
    return "градус" if value%10==1 else "градуса" if value%10 in (2,3,4) else "градусов"

def recognition_languages(language, testing=False):
    if language == "auto":return [None]
    if language == "ru-only":return ["ru"]
    return [language] + (["en"] if language == "ru" and not testing else [])

class Engine:
    def __init__(self,config):
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        from vosk import Model, KaldiRecognizer, SetLogLevel
        from voice_asr import load_asr
        from voice_tts import Speech
        global LEASE
        LEASE=VoiceLease(ctl,model=config.get("whisper_model","base"))
        self.config=config
        status(history_limit=config.get("history_limit",10))
        self.control=VoiceControl()
        self.play_lock=threading.Lock()
        self.playback=None
        self.control.interrupt_callback=self.interrupt
        self.wake=config["wake_word"].lower()
        status(phase="downloading_models")
        model_path,tts_path=ensure_models(config.get("tts_model","ru_RU-irina-medium"))
        SetLogLevel(-1)
        self.model=Model(model_path)
        self.control.validate_wake=lambda phrase: isinstance(phrase,str) and 1<=len(phrase)<=80 and bool(phrase.split()) and all(self.model.vosk_model_find_word(word)>=0 for word in phrase.split())
        for word in self.wake.split():
            if self.model.vosk_model_find_word(word)<0:
                raise RuntimeError("wake word is absent from recognizer vocabulary")
        self.rec=KaldiRecognizer(self.model,16000,json.dumps([self.wake,"[unk]"],ensure_ascii=False))
        status(phase="loading_transcription")
        if config.get("whisper_model")=="off":
            from voice_asr import unload_asr
            unload_asr()
        self.asr=load_asr(config.get("whisper_model","base"))
        self.speech=Speech(config,tts_path)
        self.matches=0
        self.preroll=collections.deque(maxlen=30)
        self.noise=20.0
        self.levels=collections.deque(maxlen=50)

    def speech_threshold(self):
        base=int(self.config.get("speech_threshold",80))
        return max(base,self.noise*2) if self.config.get("auto_threshold",True) else base

    def capture_request(self,initial):
        result=bytearray(initial)
        started=time.monotonic()
        self.control.metrics(speech_start_seconds=0,speech_end_seconds=0)
        last_speech=started
        threshold=self.speech_threshold()
        while ALLOWED.is_set() and not STOP.is_set() and not self.control.cancelled.is_set():
            try:audio=AUDIO.get(timeout=.3)
            except queue.Empty:continue
            result.extend(audio)
            now=time.monotonic()
            if rms(audio)>threshold:
                last_speech=now
                self.control.metrics(speech_end_seconds=round(now-started,2))
            if now-started>=1.2 and now-last_speech>=float(self.config.get("silence_seconds",.65)):break
            if now-started>=float(self.config.get("max_listen_seconds",8)):break
        return bytes(result) if ALLOWED.is_set() and not STOP.is_set() and not self.control.cancelled.is_set() else b""

    def interrupt(self):
        clear_audio()
        stop_stream()
        with self.play_lock:
            process=self.playback
            if process and process.poll() is None:
                process.terminate()

    def say(self,text,speaker=None):
        if not text.strip() or STOP.is_set() or not ALLOWED.is_set() or self.control.cancelled.is_set():return
        SPEAKING.set()
        clear_audio()
        try:
            status(phase="speaking")
            indicate("speaking")
            from voice_numbers import pronounce_numbers
            audio=self.speech.wav(pronounce_numbers(text),speaker=speaker)
            if not ALLOWED.is_set() or self.control.cancelled.is_set():return
            if LEASE is not None and ctl("voice","lease","check",LEASE.node) is not True:return
            process=subprocess.Popen(ssh(shlex.join(["/usr/bin/aplay","-q","-D","default","-t","wav"])),stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            with self.play_lock:self.playback=process
            try:process.communicate(input=audio,timeout=25)
            finally:
                with self.play_lock:
                    if self.playback is process:self.playback=None
        finally:
            clear_audio()
            SPEAKING.clear()

    def response(self,name,**values):
        from voice_config import render_response
        return render_response(getattr(self,"config",{}),name,**values)

    def perform(self,intent,value):
        if self.control.active():return ""
        if not ALLOWED.is_set() or not privacy_allowed(ctl("status")):
            return ""
        if intent=="cancel":
            # This request is the cancellation command, not an assistant job to stop.
            status(phase="idle")
            cancelled=ctl("action","stop")
            voice_event(cancelled_action=cancelled)
            return self.response("cancel")
        if intent=="timer_start":
            ctl("timer","start",str(value))
            return self.response("timer_start",duration=duration(value))
        if intent=="timer_cancel":
            if not ctl("timer","status")["active"]:return self.response("no_timer")
            ctl("timer","cancel");return self.response("timer_cancel")
        if intent=="timer_status":
            state=ctl("timer","status")
            return self.response("timer_status",duration=duration(state["remaining_seconds"])) if state["active"] else self.response("no_timer")
        if intent=="clarify_timer":return self.response("clarify_timer")
        if intent=="weather":
            data=ctl("weather","status")
            seconds=self.config.get("action_catalog",{}).get("builtin:weather",{}).get("display_seconds")
            self.presentation_after_reply=("weather","show",*([str(seconds)] if seconds else []))
            temperature=round(data["temperature_c"])
            return self.response("weather",temperature=number(temperature),degrees=degrees(temperature),city=data.get("city",data.get("location","вашем городе")))
        if intent=="time":
            now=datetime.now(ZoneInfo("Europe/Moscow"))
            seconds=self.config.get("action_catalog",{}).get("builtin:time",{}).get("display_seconds")
            self.presentation_after_reply=("clock","text",now.strftime("%H%M"),*([str(seconds)] if seconds else []))
            return self.response("time",time=clock(now.hour,now.minute))
        if intent in ("volume_down","volume_up"):
            ctl("volume","down" if intent=="volume_down" else "up");return self.response(intent)
        if intent=="ambiguous":return self.response("ambiguous")
        if intent=="action":
            target,sep,name=value.partition(":")
            if sep and target=="speaker":
                self.presentation_after_reply=("action","run",name)
                return self.response(value,action=name)
            response_key=value if sep else "server:"+value
            if sep:value=name
            body=json.dumps({"action":value,"voice_text":getattr(self,"command_text","") ,**({"voice_owner":LEASE.node} if LEASE else {})}).encode()
            req=urllib.request.Request(os.environ.get("AUTOMATION_URL","http://127.0.0.1:8787")+"/run",body,
                 {"Authorization":"Bearer "+os.environ["AUTOMATION_TOKEN"],"Content-Type":"application/json"})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(req,timeout=10) as response:
                    reply=json.load(response)
                    if not reply.get("ok"):raise RuntimeError("action failed")
            except urllib.error.HTTPError as error:
                if error.code==400:
                    reply=json.load(error)
                    if reply.get("clarification"):return self.response("ambiguous")+" "+reply.get("error","")
                raise
            result=reply.get("result")
            mapped=result.get("results",{}) if isinstance(result,dict) else {}
            return self.response(response_key,action=value,**{"result_"+k:str(v) for k,v in mapped.items()})
        return self.response("unknown")

    def reload_config(self):
        now=time.monotonic()
        if now<getattr(self,"next_config_check",0):return
        self.next_config_check=now+1
        try:
            from voice_config import read_voice
            raw,fresh=read_voice("/config/voice.yaml")
            if raw==getattr(self,"config_source",None):return
            if fresh.get("tts_engine","piper")!=self.config.get("tts_engine","piper"):
                raise ValueError("speech engine changes need voice restart")
            if fresh.get("whisper_model","base")!=self.config.get("whisper_model","base"):
                from voice_asr import load_asr
                status(phase="loading_transcription")
                from voice_asr import unload_asr
                import gc
                if fresh.get("whisper_model") in ("off","base","small"):unload_asr()
                self.asr=None
                gc.collect()
                self.asr=load_asr(fresh.get("whisper_model","base"))
                clear_audio();self.rec.Reset();status(phase="idle")
            word=fresh["wake_word"].lower()
            if any(self.model.vosk_model_find_word(part)<0 for part in word.split()):
                raise ValueError("wake phrase contains an unknown Russian word")
            if self.speech.engine=="silero":
                speaker=fresh.get("tts_speaker","baya")
                if speaker not in self.speech.voice.speakers:raise ValueError("unknown voice")
                self.speech.speaker=speaker
            self.speech.volume=float(fresh.get("tts_volume",.65))
            if hasattr(self.speech,"synthesis"):self.speech.synthesis.volume=self.speech.volume
            if word!=self.wake:
                self.rec.SetGrammar(json.dumps([word,"[unk]"],ensure_ascii=False));self.rec.Reset()
                self.wake=word
            if fresh.get("microphone_channel",0)!=self.config.get("microphone_channel",0):
                stop_stream();clear_audio();self.preroll.clear();self.rec.Reset()
            self.config=fresh
            status(history_limit=fresh.get("history_limit",10),history=STATE.get("history",[])[:fresh.get("history_limit",10)])
            for old in sorted(HISTORY_AUDIO.glob("*.wav"),key=lambda p:p.stat().st_mtime,reverse=True)[fresh.get("history_limit",10):]:old.unlink(missing_ok=True)
            if LEASE:LEASE.configure_model(fresh.get("whisper_model","base"))
            self.config_source=raw
            status(config_error="",tts_speaker=fresh.get("tts_speaker","irina"),wake_word=self.wake)
        except Exception as error:
            status(config_error=str(error)[:160])

    def decode(self,audio,testing=False):
        import numpy as np
        samples=np.frombuffer(audio,dtype=np.int16).astype(np.float32)/32768
        samples-=samples.mean() if len(samples) else 0
        peak=float(np.max(np.abs(samples))) if len(samples) else 0
        if peak>0:samples*=min(16,.8/peak)
        language=self.config.get("speech_language","ru")
        candidates=recognition_languages(language, testing)
        selected=("empty",None)
        started=time.monotonic()
        for candidate in candidates:
            segments,_=self.asr.transcribe(samples,language=candidate,beam_size=1,
                condition_on_previous_text=False,vad_filter=False)
            raw_text=" ".join(segment.text for segment in segments)
            text=strip_wake(raw_text,self.wake)
            result=classify(text,*voice_routes(self.config))
            from voice_asr import hallucinated_transcript
            if hallucinated_transcript(raw_text):result=("ignored",None)
            attempts=[]
            attempts.append({"text":raw_text[:2000],"language":candidate or "auto","intent":result[0],"pass":1})
            # Default decoding is fast. Retry only a failed or incomplete command with
            # a constrained Russian command vocabulary; never retry known hallucinations.
            if result[0] in ("unknown","clarify_timer"):
                try:
                    retry_segments,_=self.asr.transcribe(samples,language=candidate,beam_size=5,
                        initial_prompt="Махи. Таймер на одну минуту. Таймер на полторы минуты. Погода. Который час. Стоп.",
                        condition_on_previous_text=False,vad_filter=False)
                    retry_raw=" ".join(segment.text for segment in retry_segments)
                    retry_text=strip_wake(retry_raw,self.wake)
                    retry_result=classify(retry_text,*voice_routes(self.config))
                    if hallucinated_transcript(retry_raw):retry_result=("ignored",None)
                    attempts.append({"text":retry_raw[:2000],"language":candidate or "auto","intent":retry_result[0],"pass":2,"beam_size":5})
                    if retry_result[0] not in ("unknown","empty","clarify_timer","ignored"):
                        raw_text,text,result=retry_raw,retry_text,retry_result
                except Exception as error:
                    attempts.append({"pass":2,"error":str(error)[:200]})
            if not testing:
                self.command_text=text
                previous=list(STATE.get("history", [{}])[0].get("attempts", []))
                voice_event(text=raw_text[:2000], command=text[:2000], intent=result[0], arguments=result[1], attempts=(previous+attempts)[-4:], outcome="recognized")
            elapsed=round((time.monotonic()-started)*1000)
            backend=getattr(self.asr,"backend",LEASE.backend if LEASE else self.config.get("whisper_model","base"))
            if LEASE:LEASE.backend=backend
            if not testing:voice_event(asr_backend=backend,asr_ms=elapsed)
            status(asr_ms=elapsed,asr_backend=backend)
            self.control.metrics(asr_ms=elapsed,asr_backend=backend,capture_seconds=round(len(samples)/16000,1))
            if testing:
                self.control.transcript(raw_text)
                self.control.metrics(intent=result[0],arguments=result[1])
            if result[0] not in ("unknown","empty"):
                return result
            if result[0]=="empty":return result
            selected=result
        return selected

    def handle(self,audio):
        import numpy as np
        if not audio or self.control.cancelled.is_set():return
        if not ALLOWED.is_set() or not privacy_allowed(ctl("status")):return
        record_request_audio(audio)
        status(phase="thinking");indicate("thinking")
        language=self.config.get("speech_language","auto")
        if language=="ru-only":language="ru"
        intent,value=self.decode(audio)
        if self.control.cancelled.is_set():return
        # The bounded dashboard history is explicitly enabled for recognition diagnosis.
        status(last_intent=intent,command_count=STATE["command_count"]+1)
        if self.control.active() or self.control.cancelled.is_set():return
        if intent=="ignored":
            voice_event(outcome="ignored_hallucination",response="")
            return
        if intent=="empty":
            self.say(self.response("empty"))
            clear_audio();status(phase="listening");indicate("listening")
            followup=self.capture_request(b"")
            if followup:
                record_request_audio(followup)
                intent,value=self.decode(followup)
                if intent=="empty":
                    voice_event(outcome="no_speech")
                    return
            else:
                voice_event(outcome="no_speech")
                return
        if self.control.active() or self.control.cancelled.is_set():return
        if not ALLOWED.is_set() or not privacy_allowed(ctl("status")):return
        try:
            reply=self.perform(intent,value)
            self.say(reply)
            voice_event(outcome="cancelled" if self.control.cancelled.is_set() else "unrecognized" if intent=="unknown" else "ambiguous" if intent=="ambiguous" else "completed", response=reply)
        except Exception as error:
            voice_event(outcome="error", error=str(error)[:300])
            self.say(self.response("error"))

    def preview_voice(self):
        path=pathlib.Path("/state/preview.json")
        if not path.exists():return
        try:
            data=json.loads(path.read_text());path.unlink()
            if time.time()-data.get("at",0)>30:return
            speaker=data.get("speaker")
            if speaker not in ("aidar","baya","kseniya","xenia","eugene"):return
            self.say("Привет! Я слушаю. Чем помочь?",speaker=speaker)
        except Exception:status(error="voice preview failed")
        finally:
            clear_audio();self.rec.Reset();self.preroll.clear();self.matches=0
            indicate("idle")
            status(phase="idle" if ALLOWED.is_set() else "muted_or_disconnected")

    def compare_recording(self):
        tests=self.control.recordings
        with tests.lock:
            path=tests.pending
            tests.pending=None
        if not path:return
        try:
            import wave
            import numpy as np
            from vosk import KaldiRecognizer
            with wave.open(path,'rb') as f:audio=f.readframes(f.getnframes())
            samples=np.frombuffer(audio,dtype=np.int16).astype(np.float32)/32768
            rec=KaldiRecognizer(self.model,16000,json.dumps([self.wake,"[unk]"],ensure_ascii=False))
            rec.AcceptWaveform(audio)
            wake_text=json.loads(rec.FinalResult()).get("text","")
            results=[]
            from voice_asr import load_asr
            models=["base","small"]
            if pathlib.Path("/models/whisper-cpp/ggml-small-q5_1.bin").is_file():models.append("small-cpp")
            for name in models:
                started=time.monotonic()
                model=load_asr(name)
                loaded=time.monotonic()
                segments,_=model.transcribe(samples,language=recognition_languages(self.config.get("speech_language","ru"), True)[0],beam_size=1,condition_on_previous_text=False)
                text=" ".join(segment.text for segment in segments)
                intent,args=classify(strip_wake(text,self.wake),*voice_routes(self.config))
                results.append({"model":name,"text":text,"intent":intent,"arguments":args,"model_load_ms":round((loaded-started)*1000),"asr_ms":round((time.monotonic()-loaded)*1000),"capture_seconds":len(audio)/32000})
                del model
                with tests.lock:tests.comparison.update(results=list(results),wake_detected=self.wake in wake_text,wake_text=wake_text)
        except Exception as error:
            with tests.lock:tests.comparison['error']=str(error)[:160]
        finally:
            with tests.lock:tests.comparison['running']=False
            clear_audio();self.rec.Reset();self.preroll.clear()

    def run(self):
        status(phase="idle",error="")
        threading.Thread(target=self.control.serve,args=(STOP,),daemon=True).start()
        threading.Thread(target=guard,args=(self.control,),daemon=True).start()
        channel=int(self.config.get("microphone_channel",0))
        if channel not in (0,2,4,6):raise ValueError("microphone channel must be 0,2,4,6")
        threading.Thread(target=read_microphone,args=(lambda:self.config,self.control),daemon=True).start()
        while not STOP.is_set():
            self.reload_config()
            self.preview_voice()
            if self.asr is None:
                STOP.wait(.3)
                continue
            self.compare_recording()
            testing=self.control.active()
            if testing!=getattr(self,"was_testing",False):
                self.was_testing=testing;self.rec.Reset();self.preroll.clear();self.matches=0
                if not testing:clear_audio()
                status(phase="listen_test" if testing else "idle")
            if not ALLOWED.is_set():
                self.control.recordings.feed(b"",False)
                self.rec.Reset();self.preroll.clear();self.matches=0
                STOP.wait(.3);continue
            try:audio=AUDIO.get(timeout=.5)
            except queue.Empty:continue
            self.preroll.append(audio)
            level=rms(audio)
            # Estimate quiet floor only from low-energy frames.
            self.levels.append(level)
            if len(self.levels)>=10:self.noise=sorted(self.levels)[len(self.levels)//5]
            self.control.metrics(effective_threshold=round(self.speech_threshold()))
            if self.control.active():
                if STATE.get("phase")!="listen_test":status(phase="listen_test")
                if level>self.speech_threshold():
                    captured=self.capture_request(b"".join(self.preroll))
                    if self.control.active() and captured and privacy_allowed(ctl("status")):
                        self.decode(captured,testing=True)
                    clear_audio();self.preroll.clear();self.rec.Reset()
                continue
            if STATE.get("phase")=="listen_test":status(phase="idle")
            final=self.rec.AcceptWaveform(audio)
            result=json.loads(self.rec.Result() if final else self.rec.PartialResult())
            words=result.get("text",result.get("partial","")).split()
            matched=self.wake in " ".join(words)
            self.matches=self.matches+1 if matched else 0
            if matched and (final or self.matches>=int(self.config.get("wake_confirmations",2))):
                if not ALLOWED.is_set() or not privacy_allowed(ctl("status")):continue
                self.control.cancelled.clear()
                self.presentation_after_reply=None
                voice_event(new=True, wake=" ".join(words)[:2000])
                status(phase="listening",wake_count=STATE["wake_count"]+1,error="")
                threading.Thread(target=indicate,args=("listening",),daemon=True).start()
                try:self.handle(self.capture_request(b"".join(self.preroll)))
                except Exception as error:
                    voice_event(outcome="error", error=str(error)[:300])
                    status(error="voice processing failed")
                finally:
                    indicate("idle")
                    try:
                        ctl("clock","clear")
                        presentation=getattr(self,"presentation_after_reply",None)
                        if presentation and ALLOWED.is_set() and not self.control.cancelled.is_set():ctl(*presentation)
                    except Exception:pass
                    clear_audio();self.rec.Reset();self.preroll.clear();self.matches=0
                    status(phase="idle" if ALLOWED.is_set() else "muted_or_disconnected")
                    STOP.wait(.8);clear_audio()

def shutdown(*_):
    STOP.set();ALLOWED.clear();stop_stream()
    if LEASE:LEASE.release()

if __name__=="__main__":
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    try:
        from voice_config import read_voice
        _,config=read_voice("/config/voice.yaml")
        Engine(config).run()
    except Exception as error:
        status(phase="error",error=str(error)[:200])
        raise
    finally:
        shutdown()
        status(phase="error" if STATE.get("error") else "stopped",listening_enabled=False)
