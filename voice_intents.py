"""Deterministic voice intents. Never turn model output into shell commands."""
import re

NUMBERS = {
    "ноль":0,"один":1,"одна":1,"одну":1,"два":2,"две":2,"три":3,"четыре":4,
    "пять":5,"шесть":6,"семь":7,"восемь":8,"девять":9,"десять":10,
    "одиннадцать":11,"двенадцать":12,"тринадцать":13,"четырнадцать":14,
    "пятнадцать":15,"шестнадцать":16,"семнадцать":17,"восемнадцать":18,
    "девятнадцать":19,"двадцать":20,"тридцать":30,"сорок":40,"пятьдесят":50,
    "шестьдесят":60,"zero":0,"one":1,"a":1,"an":1,"two":2,"three":3,"four":4,
    "five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,
    "twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
    "seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,
    "forty":40,"fifty":50,"sixty":60,
}
UNIT = re.compile(r"^(секунд\w*|сек|seconds?|secs?|минут\w*|мин|minutes?|mins?|час\w*|hours?|hrs?)$")
def normalize(text):
    text=re.sub(r"(?<=\d)(?=[а-яa-z])"," ",text.lower().replace("ё","е").replace("don't","dont"))
    return re.sub(r"[^\w\s]", " ", text).strip()

def duration_from_text(text):
    tokens=normalize(text).split()
    total=0
    for index,word in enumerate(tokens):
        if not UNIT.match(word):
            continue
        values=[]
        for token in reversed(tokens[max(0,index-2):index]):
            if token.isdigit():
                if values: break
                values=[int(token)];break
            if token in NUMBERS:
                values.insert(0,NUMBERS[token])
            else: break
        if not values:
            if index>0 and tokens[index-1] in ("на","for"):
                values=[1]
            else:
                continue
        # Only tens+units is a valid two-word number.
        if len(values)==2 and not (values[0]>=20 and values[0]%10==0 and 0<values[1]<10):
            return None
        number=sum(values)
        factor=3600 if word.startswith(("час","hour","hr")) else 60 if word.startswith(("мин","min")) else 1
        total+=number*factor
    return total if 1<=total<=5999 else None

DEFAULT_TRIGGERS={
 "timer_start":["таймер","timer","засеки","засекай","засечь","отсчитай"],
 "cancel":["стоп","останови","отмени","отмена","хватит","stop","cancel"],
 "timer_cancel":["отмени таймер","отменить таймер","выключи таймер","останови таймер","убери таймер","stop timer","cancel timer","dismiss timer"],
 "timer_status":["сколько осталось","сколько времени осталось","timer status","remaining time"],
 "weather":["погода","погоду","погоды","пагода","температура","сколько градусов","weather"],
 "time":["который час","сколько времени","what time","time is it"],
 "volume_down":["тише","потише","quieter","volume down"],
 "volume_up":["громче","погромче","louder","volume up"],
}
def classify(text,aliases=None,triggers=None):
    text=normalize(text)
    if not text:return ("empty",None)
    if set(text.split()) & {"не","not","dont"}:return ("unknown",None)
    matches=[]
    for phrase,action in (aliases or {}).items():
        required=normalize(phrase).split()
        if required and all(word in text.split() for word in required):
            matches.append((len(required),action))
    if matches:
        longest=max(n for n,_ in matches)
        actions={action for n,action in matches if n==longest}
        return ("action",actions.pop()) if len(actions)==1 else ("ambiguous",None)
    configured={**DEFAULT_TRIGGERS,**(triggers or {})}
    def specificity(intent):
        return max((len(normalize(phrase).split()) for phrase in configured[intent]
                    if normalize(phrase) and all(word in text.split() for word in normalize(phrase).split())),default=0)
    scores={intent:specificity(intent) for intent in configured}
    longest=max(scores.values(),default=0)
    matched=[intent for intent,score in scores.items() if score==longest and score>0]
    # A cancellation/status phrase naturally includes the word "timer".
    if "timer_start" in matched and any(i in matched for i in ("timer_cancel","timer_status")):
        matched.remove("timer_start")
    if len(matched)>1:return ("ambiguous",None)
    for intent in matched:
        if intent=="timer_start":
            duration=duration_from_text(text)
            return ("timer_start",duration) if duration else ("clarify_timer",None)
        return (intent,None)
    return ("unknown",None)

def strip_wake(text,wake):
    # Only remove a leading wake word. Preserve any later word in the request.
    text=text.strip()
    return re.sub(r"^\s*"+re.escape(wake)+r"\b[\s,.!?]*","",text,flags=re.IGNORECASE).strip()
