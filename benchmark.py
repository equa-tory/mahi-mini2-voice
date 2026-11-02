"""Benchmark an explicitly supplied mono 16 kHz WAV against local Whisper.cpp."""
import argparse
import json
import time
import urllib.request
import wave
from pathlib import Path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:8788/inference')
    parser.add_argument('--language',default='ru')
    parser.add_argument('--runs',type=int,default=3)
    args=parser.parse_args()
    with wave.open(str(args.wav),'rb') as stream:
        if stream.getnchannels()!=1 or stream.getframerate()!=16000 or stream.getsampwidth()!=2:
            raise ValueError('use mono signed 16-bit 16 kHz WAV')
        seconds=stream.getnframes()/16000
    boundary='mahi-benchmark'
    fields={'language':args.language,'response_format':'json','temperature':'0','temperature_inc':'0','best_of':'1','beam_size':'1','audio_ctx':str(min(1500,max(512,int(seconds+1)*50+50)))}
    body=b''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in fields.items())
    body+=f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode()+args.wav.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for run in range(args.runs):
        started=time.monotonic()
        with opener.open(urllib.request.Request(args.url,body,{'Content-Type':f'multipart/form-data; boundary={boundary}'}),timeout=120) as response:result=json.load(response)
        print(json.dumps({'run':run+1,'audio_seconds':seconds,'seconds':round(time.monotonic()-started,3),'text':result['text']},ensure_ascii=False),flush=True)
