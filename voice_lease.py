"""Speaker is the authority. Any failed health/renewal stops local capture."""
import os
import time
import urllib.request
import uuid

class VoiceLease:
    def __init__(self, ctl, health=None, model="small-cpp"):
        self.ctl=ctl
        self.node=os.environ.get('VOICE_NODE','primary')+'-'+uuid.uuid4().hex
        self.role=os.environ.get('VOICE_ROLE','primary')
        self.url=os.environ.get('VOICE_PUBLIC_URL','')
        self.configure_model(model)
        self.health=health or self.check_health
        self.owned=False
        self.healthy=False

    def configure_model(self, model):
        self.asr_model=model
        self.backend=os.environ.get('VOICE_ASR_BACKEND','whisper-cpp') if model=='small-cpp' else 'faster-whisper-'+model+'-cpu'

    def check_health(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        urls=[os.environ.get('AUTOMATION_URL','http://127.0.0.1:8787')+'/health']
        # A resident CPU fallback keeps recognition available if the GPU fails.
        from voice_asr import cpu_fallback_enabled
        if self.asr_model=="small-cpp" and not cpu_fallback_enabled():
            urls.append(os.environ.get("WHISPER_CPP_URL","http://127.0.0.1:8788/inference").rsplit("/",1)[0]+"/health")
        for url in urls:
            with opener.open(url,timeout=1) as response:
                if response.status!=200: raise RuntimeError('voice backend unavailable')

    def renew(self):
        if not self.url: raise RuntimeError('VOICE_PUBLIC_URL must identify this node')
        try:
            self.health()
            self.healthy=True
            reply=self.ctl('voice','lease','renew' if self.owned else 'acquire',self.node,self.role,self.url,self.backend)
            self.owned=reply['granted']
            return self.owned
        except Exception:
            self.healthy=False
            self.owned=False
            return False

    def release(self):
        self.owned=False
        try:self.ctl('voice','lease','release',self.node)
        except Exception:pass
