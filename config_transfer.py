"""Manual, private configuration snapshots. Never copies keys, env, audio or models."""
import argparse
import json
import os
from pathlib import Path
import yaml
from voice_config import validate_voice
from runner import validate_actions, send_speaker
from dashboard import write_in_place

FILES=('actions.yaml','voice.yaml','weather.yaml')

def validate(bundle):
    if not isinstance(bundle,dict) or bundle.get('version')!=1 or set(bundle)-{'version','files','speaker'}:
        raise ValueError('invalid snapshot')
    files=bundle.get('files',{})
    if set(files)!=set(FILES):raise ValueError('snapshot must contain actions, voice and weather')
    for text in files.values():
        if not isinstance(text,str) or len(text.encode())>65536:raise ValueError('configuration exceeds 64KiB')
    validate_actions(yaml.safe_load(files['actions.yaml']))
    validate_voice(yaml.safe_load(files['voice.yaml']))
    weather=yaml.safe_load(files['weather.yaml'])
    if not isinstance(weather,dict) or not -90<=float(weather['latitude'])<=90 or not -180<=float(weather['longitude'])<=180:
        raise ValueError('invalid weather coordinates')
    if 'speaker' in bundle and (not isinstance(bundle['speaker'],str) or len(bundle['speaker'].encode())>65536):
        raise ValueError('invalid speaker configuration')
    return bundle

def export_config(root):
    return validate({'version':1,'files':{name:(root/name).read_text() for name in FILES}})

def import_config(root,bundle):
    validate(bundle)
    # Validation precedes all writes; no dependency on the primary host.
    if 'speaker' in bundle:send_speaker(['config','apply',bundle['speaker']])
    for name,text in bundle['files'].items():write_in_place(root/name,text)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('export','import'))
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--config-dir',type=Path,default=Path('/config'))
    parser.add_argument('--speaker',action='store_true',help='include speaker config on export (import applies it if present)')
    args=parser.parse_args()
    if args.operation=='export':
        bundle=export_config(args.config_dir)
        if args.speaker:bundle['speaker']=json.loads(send_speaker(['config','effective']))['data']
        fd=os.open(args.snapshot,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(bundle,f,ensure_ascii=False)
    else:
        if args.snapshot.stat().st_size>262144:raise ValueError('snapshot too large')
        import_config(args.config_dir,json.loads(args.snapshot.read_text()))
