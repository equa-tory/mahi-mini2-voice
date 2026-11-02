"""Russian number words avoid synthesizers silently dropping numeric digits."""
SMALL="ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split()
TENS={20:"двадцать",30:"тридцать",40:"сорок",50:"пятьдесят",60:"шестьдесят",70:"семьдесят",80:"восемьдесят",90:"девяносто"}
HUNDREDS={100:"сто",200:"двести",300:"триста",400:"четыреста",500:"пятьсот",600:"шестьсот",700:"семьсот",800:"восемьсот",900:"девятьсот"}
def form(n,one,few,many):
    return many if 11<=abs(n)%100<=14 else one if abs(n)%10==1 else few if abs(n)%10 in (2,3,4) else many
def number(n,feminine=False):
    n=int(n)
    if n<0:return "минус "+number(-n,feminine)
    if n>=10000:raise ValueError("number outside supported range")
    if n<20:
        if feminine and n==1:return "одна"
        if feminine and n==2:return "две"
        return SMALL[n]
    parts=[]
    if n>=1000:
        k,n=divmod(n,1000)
        parts.extend([number(k,True),form(k,"тысяча","тысячи","тысяч")])
    if n>=100:
        h,n=divmod(n,100);parts.append(HUNDREDS[h*100])
    if n>=20:
        tens,n=divmod(n,10);parts.append(TENS[tens*10])
    if n:parts.append(number(n,feminine))
    return " ".join(parts)
def duration(seconds):
    seconds=int(seconds)
    if seconds<=0:return "ноль секунд"
    parts=[]
    for divisor,forms,feminine in ((3600,("час","часа","часов"),False),(60,("минута","минуты","минут"),True),(1,("секунда","секунды","секунд"),True)):
        count,seconds=divmod(seconds,divisor)
        if count:parts.append(number(count,feminine)+" "+form(count,*forms))
    return " и ".join(parts)
def clock(hours,minutes):
    return number(hours)+" "+form(hours,"час","часа","часов")+" "+number(minutes,True)+" "+form(minutes,"минута","минуты","минут")


def pronounce_numbers(text):
    import re
    def speak(match):
        token=match.group(0)
        sign="минус " if token.startswith('-') else ""
        parts=token.lstrip('-').replace(',','.').split('.')
        def whole(value):
            return number(int(value)) if len(value)<5 else " ".join(number(int(digit)) for digit in value)
        return sign+whole(parts[0])+(" точка "+" ".join(number(int(digit)) for digit in parts[1]) if len(parts)>1 else "")
    return re.sub(r"-?\d+(?:[.,]\d+)?",speak,str(text))
