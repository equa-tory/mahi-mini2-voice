import string
RESPONSE_FIELDS = {"duration", "temperature", "city", "degrees", "time", "action"}
DEFAULT_RESPONSES = {
 "timer_start":"Таймер установлен. До сигнала: {duration}.",
 "cancel":"", "timer_cancel":"Таймер отменён.", "no_timer":"Сейчас нет таймера.",
 "timer_status":"До сигнала: {duration}.", "clarify_timer":"На сколько минут или секунд поставить таймер?",
 "weather":"По сохранённому прогнозу, в {city} {temperature} {degrees}.",
 "time":"Сейчас {time}.", "volume_down":"Хорошо.", "volume_up":"Хорошо.",
 "ambiguous":"Уточните, какую команду выполнить.", "action":"Готово.",
 "unknown":"Эту команду я пока не умею. Можно попросить таймер, погоду, время или изменить громкость.",
 "empty":"Слушаю.", "error":"Не удалось выполнить команду. Проверьте таймер или подключение."
}
def validate_templates(templates):
 if not isinstance(templates,dict) or len(templates)>100: raise ValueError("invalid response templates")
 for name,text in templates.items():
  if name not in DEFAULT_RESPONSES or not isinstance(text,str) or len(text)>2000: raise ValueError("invalid response template")
  allowed={"timer_start":{"duration"},"timer_status":{"duration"},"weather":{"temperature","city","degrees"},"time":{"time"},"action":{"action"}}.get(name,set())
  for _,field,spec,conversion in string.Formatter().parse(text):
   if field is not None and (field not in allowed and not (name=="action" and field.startswith("result_") and field[7:].isidentifier()) or spec or conversion): raise ValueError("unsupported response placeholder")
 return templates

def render_response(config,name,**values):
 from action_catalog import response
 return response(config,name,**values)

"""Validation shared by the dashboard and the idle voice worker."""
from voice_intents import DEFAULT_TRIGGERS

def validate_voice(config):
    if not isinstance(config,dict):raise ValueError("voice config must be an object")
    word=config.get("wake_word")
    if not isinstance(word,str) or not 1<=len(word)<=80 or not word.strip():raise ValueError("wake phrase required, max80 characters")
    if config.get("speech_language","ru") not in ("ru","en","auto"):raise ValueError("language must be ru,en,auto")
    if not 0<=float(config.get("tts_volume",.65))<=1:raise ValueError("speech volume must be 0..1")
    if config.get("tts_speaker","baya") not in ("aidar","baya","kseniya","xenia","eugene"):raise ValueError("unknown voice")
    if not 1<=int(config.get("wake_confirmations",2))<=5:raise ValueError("wake confirmations must be1..5")
    if config.get("whisper_model","base") not in ("base","small","small-cpp"):raise ValueError("recognition model must be base, small or small-cpp")
    if not .25<=float(config.get("microphone_gain",4))<=16:raise ValueError("microphone gain must be0.25..16")
    if not 10<=int(config.get("speech_threshold",80))<=3000:raise ValueError("speech threshold must be10..3000")
    if not .3<=float(config.get("silence_seconds",.65))<=2:raise ValueError("silence duration must be0.3..2seconds")
    if not 3<=float(config.get("max_listen_seconds",8))<=15:raise ValueError("listening duration must be3..15seconds")
    if not isinstance(config.get("auto_threshold",True),bool):raise ValueError("auto threshold must be true or false")
    if config.get("microphone_channel",0) not in (0,2,4,6):raise ValueError("invalid microphone channel")
    limit=config.get("history_limit",10)
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200:raise ValueError("voice history must be 1..200 requests")
    validate_templates(config.get("responses",{}))
    validate_templates({"unknown":config.get("default_response","Готово.")})
    from action_catalog import validate_catalog
    validate_catalog(config.get("action_catalog",{}))
    if not isinstance(config.get("assistant_face","eyes"),str) or len(config.get("assistant_face","eyes"))>60:raise ValueError("invalid assistant face")
    aliases=config.get("aliases",{})
    if not isinstance(aliases,dict) or len(aliases)>100:raise ValueError("max100 aliases")
    for phrase,action in aliases.items():
        if not isinstance(phrase,str) or not 1<=len(phrase)<=160 or not isinstance(action,str) or not 1<=len(action)<=80:
            raise ValueError("invalid named-action alias")
    triggers=config.get("intent_triggers",{})
    if not isinstance(triggers,dict):raise ValueError("intent triggers must be an object")
    for intent,phrases in triggers.items():
        if intent not in DEFAULT_TRIGGERS or not isinstance(phrases,list) or len(phrases)>30:raise ValueError("invalid intent triggers")
        if any(not isinstance(p,str) or not 1<=len(p)<=80 for p in phrases):raise ValueError("invalid trigger phrase")
    return config

def read_voice(path):
    import fcntl,yaml
    with open(path) as f:
        fcntl.flock(f,fcntl.LOCK_SH)
        raw=f.read()
    config=yaml.safe_load(raw)
    validate_voice(config)
    return raw,config
