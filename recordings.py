"""Explicit server-only test recordings. Normal microphone input is never retained."""
import base64
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import wave

class Recordings:
    def __init__(self,path='/state/recordings'):
        self.path=Path(path)
        self.lock=threading.RLock()
        self.buffer=None
        self.expected=''
        self.pending=None
        self.comparison=None

    def root(self):
        self.path.mkdir(mode=0o700,parents=True,exist_ok=True)
        return self.path

    def file(self,identifier,suffix):
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('invalid recording id')
        return self.root()/(identifier+suffix)

    def start(self,expected):
        with self.lock:
            if self.buffer is not None:raise ValueError('recording already active')
            if len(list(self.root().glob('*.wav')))>=50:raise ValueError('maximum 50 recordings; delete a test first')
            if not isinstance(expected,str) or len(expected)>2000:raise ValueError('expected text maximum 2000 characters')
            self.buffer=bytearray();self.expected=expected

    def feed(self,audio,allowed=True):
        with self.lock:
            if self.buffer is None:return
            if not allowed:self.stop();return
            self.buffer.extend(audio[:960000-len(self.buffer)])
            if len(self.buffer)>=960000:self.stop()

    def stop(self):
        with self.lock:
            if self.buffer is None:return None
            data=bytes(self.buffer);self.buffer=None
            identifier=uuid.uuid4().hex
            with wave.open(str(self.file(identifier,'.wav')),'wb') as f:
                f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(data)
            os.chmod(self.file(identifier,'.wav'),0o600)
            metadata={'id':identifier,'expected':self.expected,'seconds':len(data)/32000,'created':time.time()}
            path=self.file(identifier,'.json');path.write_text(json.dumps(metadata,ensure_ascii=False));path.chmod(0o600)
            return metadata

    def dispatch(self,request):
        with self.lock:
            op=request['command']
            if op=='record_start':self.start(request.get('expected',''))
            elif op=='record_stop':return self.stop()
            elif op=='record_list':return {'recordings':[json.loads(p.read_text()) for p in sorted(self.root().glob('*.json'))], 'recording':self.buffer is not None,'comparison':self.comparison}
            elif op=='record_delete':
                for suffix in ('.wav','.json'):self.file(request.get('id'),suffix).unlink(missing_ok=True)
            elif op=='record_replay':return {'audio':base64.b64encode(self.file(request.get('id'),'.wav').read_bytes()).decode()}
            elif op=='record_compare':
                if self.pending or self.comparison and self.comparison.get('running'):raise ValueError('comparison already running')
                path=self.file(request.get('id'),'.wav')
                if not path.exists():raise ValueError('recording missing')
                self.pending=str(path);self.comparison={'running':True,'id':request['id'],'results':[]}
            else:raise ValueError('unknown recording command')
            return {'recording':self.buffer is not None}
