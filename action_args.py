"""Typed action inputs and context-specific HTTP substitutions."""
import copy
import json
import math
import re
import string
import urllib.parse

class Clarification(ValueError):
    pass

def coerce(value,spec):
    kind=spec.get('type','text')
    if kind in ('number','duration'):
        if isinstance(value,bool):raise Clarification('number required')
        try:value=float(value)
        except (TypeError,ValueError):raise Clarification('number required')
        if not math.isfinite(value):raise Clarification('finite number required')
        if kind=='duration' and (value<1 or value>5999 or value!=int(value)):raise Clarification('duration must be 1..5999 seconds')
        if 'min' in spec and value<spec['min'] or 'max' in spec and value>spec['max']:raise Clarification('number outside allowed range')
        return int(value) if value==int(value) else value
    if not isinstance(value,str) or len(value)>4096:raise Clarification('text required, maximum 4096 characters')
    if kind=='enum' and value not in spec['values']:raise Clarification('choose one of: '+', '.join(spec['values']))
    return value

def validate_schema(action):
    schema=action.get('arguments',{})
    if not isinstance(schema,dict) or len(schema)>32:raise ValueError('invalid argument schema')
    for name,spec in schema.items():
        if not isinstance(name,str) or not name.isidentifier() or not isinstance(spec,dict):raise ValueError('invalid argument name')
        if spec.get('type','text') not in ('text','number','duration','enum'):raise ValueError('invalid argument type')
        if not isinstance(spec.get('required',False),bool):raise ValueError('required must be boolean')
        if spec.get('type')=='enum' and (not isinstance(spec.get('values'),list) or not spec['values'] or any(not isinstance(x,str) for x in spec['values'])):raise ValueError('enum values required')
        if 'voice' in spec and (not isinstance(spec['voice'],dict) or set(spec['voice'])-{'prefix','suffix'} or any(not isinstance(v,str) or len(v)>160 for v in spec['voice'].values())):raise ValueError('invalid voice slot boundaries')
        if 'default' in spec:coerce(spec['default'],spec)
    mapping=action.get('results',{})
    if not isinstance(mapping,dict) or any(not k.isidentifier() or not isinstance(v,str) or not v or len(v)>200 for k,v in mapping.items()):raise ValueError('invalid result mapping')
    if action.get('type')=='http':
        if action.get('method','GET') not in ('GET','POST'):raise ValueError('HTTP method must be GET or POST')
        url=urllib.parse.urlsplit(action.get('url',''))
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:raise ValueError('HTTP URL required')
        if '{' in url.netloc or '}' in url.netloc:raise ValueError('URL host cannot contain arguments')
        for field in ('headers','query','secret_headers'):
            if not isinstance(action.get(field,{}),dict):raise ValueError(field+' must be an object')
        for key,value in action.get('secret_headers',{}).items():
            if not isinstance(value,str) or not value.isidentifier():raise ValueError('secret header requires environment variable name')
        def check(value):
            if isinstance(value,dict):
                for item in value.values():check(item)
            elif isinstance(value,list):
                for item in value:check(item)
            elif isinstance(value,str):
                for _,name,spec,conversion in string.Formatter().parse(value):
                    if name is not None and (name not in schema or spec or conversion):raise ValueError('unknown HTTP placeholder')
        for key in ('url','headers','query','json'):check(action.get(key))

def resolve(action,args=None):
    args={} if args is None else args
    if not isinstance(args,dict):raise Clarification('args must be an object')
    schema=action.get('arguments',{})
    if set(args)-set(schema):raise Clarification('unknown arguments')
    values={}
    for name,spec in schema.items():
        if name in args:values[name]=coerce(args[name],spec)
        elif 'default' in spec:values[name]=coerce(spec['default'],spec)
        elif spec.get('required'):raise Clarification('specify '+name)
        else:values[name]=''
    return values

def substitute(value,args):
    if isinstance(value,dict):return {k:substitute(v,args) for k,v in value.items()}
    if isinstance(value,list):return [substitute(v,args) for v in value]
    if not isinstance(value,str):return value
    if value.startswith('{') and value.endswith('}') and value[1:-1] in args:return args[value[1:-1]]
    return value.format_map(args)

def http_values(action,args):
    url=action['url'].format_map({k:urllib.parse.quote(str(v),safe='') for k,v in args.items()})
    query=substitute(action.get('query',{}),args)
    if query:url+=('&' if '?' in url else '?')+urllib.parse.urlencode(query,doseq=True)
    return url,substitute(action.get('headers',{}),args),substitute(action.get('json'),args)

def mapped_results(action,body):
    result={}
    if not action.get('results'):return result
    data=json.loads(body)
    for name,path in action['results'].items():
        value=data
        for part in path.split('.'):
            value=value[int(part)] if isinstance(value,list) else value[part]
        if not isinstance(value,(str,int,float,bool,type(None))):raise ValueError('mapped result must be scalar')
        result[name]=value
    return result

def voice_arguments(action,text):
    from voice_intents import normalize, duration_from_text, NUMBERS
    normalized=normalize(text)
    values={}
    for name,spec in action.get('arguments',{}).items():
        slot=spec.get('voice')
        if slot is None:continue
        prefix=normalize(slot.get('prefix',''))
        suffix=normalize(slot.get('suffix',''))
        captured=normalized
        if prefix:
            parts=captured.split(prefix)
            if len(parts)!=2:raise Clarification('уточните '+name)
            captured=parts[1].strip()
        if suffix:
            parts=captured.split(suffix)
            if len(parts)!=2:raise Clarification('уточните '+name)
            captured=parts[0].strip()
        kind=spec.get('type','text')
        if kind in ('duration','number','enum') and set(captured.split()) & {'или','or'}:raise Clarification('уточните '+name)
        if kind=='duration':
            from voice_intents import UNIT
            units=[('hour' if token.startswith(('час','hour','hr')) else 'minute' if token.startswith(('мин','min')) else 'second') for token in captured.split() if UNIT.match(token)]
            if len(units)!=len(set(units)):raise Clarification('уточните длительность '+name)
            value=duration_from_text(captured)
            if value is None:raise Clarification('уточните длительность '+name)
        elif kind=='number':
            tokens=captured.split();numbers=[];current=[]
            for token in tokens+['']:
                if token.isdigit():
                    if current:numbers.append(sum(current));current=[]
                    numbers.append(int(token))
                elif token in NUMBERS:
                    n=NUMBERS[token]
                    if current and not (len(current)==1 and current[0]>=20 and current[0]%10==0 and 0<n<10):raise Clarification('уточните число '+name)
                    current.append(n)
                elif current:numbers.append(sum(current));current=[]
            if len(numbers)!=1:raise Clarification('уточните число '+name)
            if re.search(r'\d[.,]\d',text):raise Clarification('уточните целое число '+name)
            value=-numbers[0] if 'минус' in captured.split() or re.search(r'-\d',text) else numbers[0]
        elif kind=='enum':
            matches=[v for v in spec['values'] if ' '+normalize(v)+' ' in ' '+captured+' ']
            if len(matches)!=1:raise Clarification('уточните '+name)
            value=matches[0]
        else:
            if not prefix and not suffix:raise Clarification('настройте границы текста '+name)
            if not captured:raise Clarification('уточните '+name)
            value=captured
        values[name]=value
    return resolve(action,values)
