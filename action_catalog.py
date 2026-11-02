"""One editable voice catalog, with lossless legacy configuration import."""
import copy
import random
import string
from collections import defaultdict
from voice_intents import DEFAULT_TRIGGERS
from voice_config import DEFAULT_RESPONSES, validate_templates

DEFAULT_ACTION_RESPONSE = "Готово."


def catalog(config, speaker=None, server=None):
    entries = {}
    for name, text in DEFAULT_RESPONSES.items():
        key = ('builtin:' if name in DEFAULT_TRIGGERS else 'feedback:') + name
        entries[key] = {'phrases': list(config.get('intent_triggers', {}).get(name, DEFAULT_TRIGGERS.get(name, []))),
                        'responses': [config.get('responses', {}).get(name, text)], 'display_seconds': None}
    for target, actions in (('speaker', speaker or {}), ('server', server or {})):
        for name in actions:
            entries[target+':'+name] = {'phrases': [], 'responses': [config.get('responses', {}).get('action', '')], 'display_seconds': None}
    for phrase, name in config.get('aliases', {}).items():
        key = 'server:'+name
        entries.setdefault(key, {'phrases': [], 'responses': [config.get('responses', {}).get('action', '')], 'display_seconds': None})['phrases'].append(phrase)
    entries.update(copy.deepcopy(config.get('action_catalog', {})))
    migrate_cancel_phrases(entries)
    return entries


def migrate_cancel_phrases(entries):
    generic=set(DEFAULT_TRIGGERS["cancel"])
    old=entries.get("builtin:timer_cancel",{})
    moved=[p for p in old.get("phrases",[]) if p.strip().lower() in generic]
    if moved:
        old["phrases"]=[p for p in old["phrases"] if p not in moved]
        new=entries.setdefault("builtin:cancel", {"phrases":list(DEFAULT_TRIGGERS["cancel"]),"responses":[""],"display_seconds":None})
        new["phrases"]=list(dict.fromkeys(new.get("phrases",[])+moved))

def validate_catalog(entries, server=None, legacy_response=None):
    if not isinstance(entries, dict) or len(entries) > 500:
        raise ValueError('invalid action catalog')
    for key, entry in entries.items():
        if not isinstance(key,str):raise ValueError("invalid catalog action")
        target, sep, name = key.partition(':')
        if not sep or target not in ('builtin', 'feedback', 'speaker', 'server') or not name or len(name)>80:
            raise ValueError('invalid catalog action')
        if target == 'builtin' and name not in DEFAULT_TRIGGERS or target == 'feedback' and name not in DEFAULT_RESPONSES:
            raise ValueError('unknown built-in action')
        if not isinstance(entry, dict) or set(entry)-{'phrases','responses','display_seconds'}:
            raise ValueError('invalid catalog entry')
        phrases, responses = entry.get('phrases', []), entry.get('responses', [])
        if not isinstance(phrases, list) or len(phrases)>30 or any(not isinstance(p,str) or not p.strip() or len(p)>160 for p in phrases):
            raise ValueError('invalid action phrases')
        if target == 'feedback' and phrases:
            raise ValueError('feedback has no trigger')
        if not isinstance(responses,list) or not 0<=len(responses)<=30:
            raise ValueError('use at most 30 response lines')
        for response in responses:
            validate_templates({name if target in ('builtin','feedback') else 'action': response})
            if target in ('speaker','server') and server is not None:
                fields = {f[7:] for _,f,_,_ in string.Formatter().parse(response) if f and f.startswith('result_')}
                available = set(server.get(name,{}).get('results',{})) if target=='server' else set()
                if response==legacy_response:available|={field for action in server.values() for field in action.get('results',{})}
                if fields-available: raise ValueError('response uses an unmapped action result')
        seconds=entry.get('display_seconds')
        if seconds is not None and (isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=86400):
            raise ValueError('display duration must be default or 1..86400 seconds')
    return entries


def voice_routes(config):
    aliases = dict(config.get('aliases', {}))
    triggers = dict(config.get('intent_triggers', {}))
    # Explicit entries replace the imported legacy phrases for that action.
    entries=catalog(config)
    named={key[7:] for key in entries if key.startswith('server:')}
    aliases={p:n for p,n in aliases.items() if n not in named}
    for key, entry in entries.items():
        target, name = key.split(':',1)
        if target=='builtin': triggers[name]=entry.get('phrases',[])
        if target in ('server','speaker'):
            for phrase in entry.get('phrases',[]):
                # Preserve collisions so classify returns ambiguous instead of choosing a host.
                if phrase in aliases and aliases[phrase]!=key:
                    aliases[phrase+' ']=key
                else: aliases[phrase]=key
    return aliases, triggers


def response(config, name, **values):
    entries=config.get('action_catalog',{})
    key=name if ':' in name else ('builtin:' if name in DEFAULT_TRIGGERS else 'feedback:')+name
    fallback=config.get('responses',{}).get(name,config.get('responses',{}).get('action','') if ':' in name else DEFAULT_RESPONSES[name])
    lines=entries.get(key,{}).get('responses',[fallback])
    selected=random.choice(lines or [''])
    default=config.get("default_response",DEFAULT_ACTION_RESPONSE)
    text=selected if selected.strip() else default.strip()
    return text.format_map(defaultdict(str,values))
