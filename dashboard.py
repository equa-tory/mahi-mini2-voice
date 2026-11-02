"""Authenticated LAN dashboard bridge; all hardware validation stays in mini2ctl."""
import fcntl
import json
import os
import pathlib
import threading
import time
import yaml
from voice_config import validate_voice, DEFAULT_RESPONSES
from voice_ipc import request as voice_request

LOCK=threading.Lock()
VOICE_PATH="/config/voice.yaml"
CONTROL={
 "clock":{"text","clear","segments","face","draw"},
 "ring":{"set","zone","clear","animate"},
 "volume":{"get","set","up","down"},
 "mute":{"status","mute","unmute","toggle"},
 "brightness":{"clock","ring"},
 "light":{"get","enable","gain","integration"},
 "timer":{"pick","increase","decrease","close","selection","start","status","cancel","dismiss","sound-test","sound-level"},
 "weather":{"show","status","refresh"},
 "action":{"run","cancel"},
}

def control(args,send):
    if not isinstance(args,list) or not 2<=len(args)<=10 or any(not isinstance(x,str) or len(x)>4096 for x in args):
        raise ValueError("invalid control arguments")
    if args[0] not in CONTROL or args[1] not in CONTROL[args[0]]:
        raise ValueError("unsupported dashboard control")
    return rpc(args,send)

def rpc(args,send):
    result=json.loads(send(args))
    if not result.get("ok"):raise ValueError(result.get("error","speaker command failed"))
    return result.get("data")

def get(path,send,load_actions):
    if path=="/ui/catalog":
        from action_catalog import catalog
        with open(VOICE_PATH) as f: config=yaml.safe_load(f)
        speaker=yaml.safe_load(rpc(["config","effective"],send))
        return {"entries":catalog(config,speaker.get("actions",{}),load_actions()),"display_seconds":speaker.get("display_seconds",3),"default_response":config.get("default_response","Готово.")}
    if path=="/ui/recordings":return voice_request("record_list")
    if path=="/ui/listen-test":return voice_request("status")
    if path=="/ui/weather":
        return rpc(["weather","status"],send)
    if path=="/ui/state":
        return rpc(["status"],send)
    if path=="/ui/speaker-config-yaml":
        return {"yaml":rpc(["config","get"],send)}
    if path=="/ui/speaker-config":
        return yaml.safe_load(rpc(["config","effective"],send))
    if path=="/ui/voice-config":
        with open(VOICE_PATH) as f:
            fcntl.flock(f,fcntl.LOCK_SH)
            return {**yaml.safe_load(f),"response_defaults":DEFAULT_RESPONSES}
    if path=="/ui/actions":
        return {"actions":load_actions()}
    if path=="/ui/actions-yaml":
        with open(os.environ.get("ACTIONS_CONFIG","/config/actions.yaml")) as f:return {"yaml":f.read()}
    raise ValueError("unknown dashboard endpoint")

def write_in_place(path,text):
    # Existing file bind mounts must retain their inode.
    with open(path,"r+") as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        f.seek(0);f.write(text);f.truncate();f.flush();os.fsync(f.fileno())

def post(path,data,send,validate_actions):
    if path=="/ui/catalog":
        from action_catalog import validate_catalog
        with LOCK:
            with open(os.environ.get("ACTIONS_CONFIG","/config/actions.yaml")) as f: server=yaml.safe_load(f).get("actions",{})
            with open(VOICE_PATH) as f: current_voice=yaml.safe_load(f)
            entries=validate_catalog(data.get("entries"),server,current_voice.get("responses",{}).get("action"))
            default_response=data.get("default_response",current_voice.get("default_response","Готово."))
            validate_voice({**current_voice,"default_response":default_response})
            seconds=data.get("display_seconds",3)
            if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=86400:raise ValueError("invalid default display duration")
            speaker=yaml.safe_load(rpc(["config","effective"],send))
            speaker["display_seconds"]=seconds
            for key,entry in entries.items():
                if key.startswith("speaker:") and key[8:] in speaker.get("actions",{}):
                    action=speaker["actions"][key[8:]]
                    if entry.get("display_seconds") is None:action.pop("display_seconds",None)
                    else:action["display_seconds"]=entry["display_seconds"]
            rpc(["config","apply",yaml.safe_dump(speaker,sort_keys=False,allow_unicode=True)],send)
            with open(VOICE_PATH) as f: config=yaml.safe_load(f)
            config["action_catalog"]=entries
            config["default_response"]=default_response
            validate_voice(config)
            write_in_place(VOICE_PATH,yaml.safe_dump(config,sort_keys=False,allow_unicode=True))
        return "Action phrases and responses saved"
    if path=="/ui/recordings":
        operation=data.get("operation")
        if operation not in ("start","stop","replay","delete","compare"):raise ValueError("unknown recording operation")
        return voice_request("record_"+operation,**{k:v for k,v in data.items() if k in ("id","expected")})
    if path=="/ui/listen-test":return voice_request("start" if data.get("enabled") else "stop")
    if path=="/ui/control":return control(data.get("args"),send)
    if path=="/ui/speaker-config-yaml":
        text=data.get("yaml")
        if not isinstance(text,str) or len(text.encode())>65536:raise ValueError("configuration exceeds64KiB")
        return rpc(["config","apply",text],send)
    if path=="/ui/speaker-config":
        text=yaml.safe_dump(data["config"],sort_keys=False,allow_unicode=True)
        if len(text.encode())>65536:raise ValueError("speaker configuration exceeds64KiB")
        return rpc(["config","apply",text],send)
    if path=="/ui/voice-config":
        with LOCK:
            with open(VOICE_PATH) as f:current=yaml.safe_load(f)
            # UI can edit behavior and voices; runtime/model installation is separate.
            permitted={"wake_word","wake_confirmations","speech_language","tts_speaker","tts_volume","aliases","intent_triggers","whisper_model","microphone_gain","speech_threshold","silence_seconds","max_listen_seconds","auto_threshold","microphone_channel","assistant_face","responses"}
            if set(data)-permitted:raise ValueError("unsupported voice setting")
            if data.get("wake_word",current["wake_word"])!=current["wake_word"]:
                validate_voice({**current,**data})
                try:voice_request("validate_wake",phrase=data["wake_word"].lower())
                except OSError:raise ValueError("voice vocabulary validation unavailable; previous phrase retained")
            if "assistant_face" in data and data["assistant_face"]:
                config=yaml.safe_load(rpc(["config","get"],send))
                if data["assistant_face"] not in config.get("faces",{}):raise ValueError("unknown assistant face")
            current.update(data);validate_voice(current)
            import string
            result_fields={field[7:] for _,field,_,_ in string.Formatter().parse(current.get("responses",{}).get("action","")) if field and field.startswith("result_")}
            if result_fields:
                with open(os.environ.get("ACTIONS_CONFIG","/config/actions.yaml")) as f:actions=yaml.safe_load(f).get("actions",{})
                available={key for action in actions.values() for key in action.get("results",{})}
                if result_fields-available:raise ValueError("response uses an unmapped action result")
            write_in_place(VOICE_PATH,yaml.safe_dump(current,sort_keys=False,allow_unicode=True))
        return "voice settings saved; applied when idle"
    if path=="/ui/voice-preview":
        speaker=data.get("speaker")
        if speaker not in ("aidar","baya","kseniya","xenia","eugene"):raise ValueError("unknown voice")
        target=pathlib.Path("/state/preview.json")
        with LOCK:
            if target.exists():raise ValueError("a voice preview is already queued")
            temporary=target.with_suffix(".tmp")
            temporary.write_text(json.dumps({"speaker":speaker,"at":time.time()}))
            temporary.replace(target)
        return "voice sample queued"
    if path=="/ui/http-action":
        name=data.get("name");action=data.get("action")
        if not isinstance(name,str) or not name or len(name)>80 or not isinstance(action,dict) or action.get("type")!="http":raise ValueError("invalid HTTP action")
        target=os.environ.get("ACTIONS_CONFIG","/config/actions.yaml")
        with LOCK:
            with open(target) as f:config=yaml.safe_load(f)
            config.setdefault("actions",{})[name]=action;validate_actions(config)
            write_in_place(target,yaml.safe_dump(config,sort_keys=False,allow_unicode=True))
        return "HTTP action saved"
    if path=="/ui/actions-yaml":
        text=data.get("yaml")
        if not isinstance(text,str) or len(text.encode())>65536:raise ValueError("action YAML exceeds64KiB")
        validate_actions(yaml.safe_load(text))
        with LOCK:write_in_place(os.environ.get("ACTIONS_CONFIG","/config/actions.yaml"),text)
        return "server actions saved"
    raise ValueError("unknown dashboard endpoint")
