"""Private Unix-socket diagnostics; recognized text stays in process memory."""
import json
import os
import socket
import threading
import time
from recordings import Recordings

SOCKET_PATH="/state/voice.sock"
class VoiceControl:
    def __init__(self):
        self.lock=threading.Lock()
        self.validate_wake=None
        self.recordings=Recordings()
        self.testing=threading.Event()
        self.cancelled=threading.Event()
        self.interrupt_callback=None
        self.lease_until=0
        self.data={"text":"","text_at":0,"recognitions":0,"rms":0,"raw_rms":0,"peak":0,"asr_ms":0}

    def metrics(self,**values):
        with self.lock:self.data.update(values)

    def transcript(self,text):
        with self.lock:
            if self.testing.is_set():
                self.data.update(text=text[:1000],text_at=time.time(),recognitions=self.data["recognitions"]+1)

    def active(self):
        with self.lock:
            if self.testing.is_set() and time.monotonic()>self.lease_until:
                self.testing.clear();self.recordings.stop();self.data.update(text="",text_at=0)
            return self.testing.is_set()

    def dispatch(self,request):
        command=request.get("command")
        if command.startswith("record_"):
            if command=="record_start":
                self.testing.set();self.lease_until=time.monotonic()+120
            result=self.recordings.dispatch(request)
            return result
        if command=="validate_wake":
            phrase=request.get("phrase","")
            if not self.validate_wake or not self.validate_wake(phrase):raise ValueError("unsupported wake phrase")
            return {"valid":True}
        if command=="interrupt":
            self.cancelled.set()
            if self.interrupt_callback:self.interrupt_callback()
            return {"interrupted":True}
            return {"interrupted":True}
        if command=="start":
            with self.lock:
                self.cancelled.clear()
                self.data.update(text="",text_at=0)
                self.testing.set();self.lease_until=time.monotonic()+120
        elif command=="stop":
            with self.lock:
                self.testing.clear();self.recordings.stop();self.data.update(text="",text_at=0)
        elif command=="status":
            if self.active():self.lease_until=time.monotonic()+120
        elif command!="status":raise ValueError("unknown voice diagnostic command")
        with self.lock:return {**self.data,"testing":self.testing.is_set()}

    def serve(self,stop):
        try:os.unlink(SOCKET_PATH)
        except FileNotFoundError:pass
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        listener.bind(SOCKET_PATH);os.chmod(SOCKET_PATH,0o600)
        listener.listen(4);listener.settimeout(.5)
        try:
            while not stop.is_set():
                try:conn,_=listener.accept()
                except socket.timeout:continue
                with conn:
                    conn.settimeout(2)
                    try:
                        data=conn.makefile("rb").readline(4097)
                        if len(data)>4096:raise ValueError("request too large")
                        reply={"ok":True,"data":self.dispatch(json.loads(data))}
                    except Exception:reply={"ok":False,"error":"invalid diagnostic request"}
                    try:conn.sendall(json.dumps(reply,ensure_ascii=False).encode()+b"\n")
                    except OSError:pass
        finally:
            listener.close()
            try:os.unlink(SOCKET_PATH)
            except FileNotFoundError:pass

def request(command,**values):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(2);conn.connect(SOCKET_PATH)
        conn.sendall(json.dumps({"command":command,**values}).encode()+b"\n")
        response=json.loads(conn.makefile("rb").readline(1500000))
    if not response.get("ok"):raise ValueError(response.get("error","diagnostics unavailable"))
    return response["data"]
