import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from action_args import Clarification, resolve, validate_schema, http_values, mapped_results, voice_arguments
from recordings import Recordings
from voice_config import validate_templates, render_response
from voice_ipc import VoiceControl

class CustomizationTests(unittest.TestCase):
    def test_templates(self):
        for template in ('{duration.__class__}','{temperature}','{duration!r}','{duration:04}'):
            with self.assertRaises(ValueError):validate_templates({'timer_start':template})
        validate_templates({'action':'{result_temperature}','weather':'{city}: {temperature}'})
        self.assertEqual(render_response({'responses':{'timer_start':''}},'timer_start',duration='две минуты'),'')
        self.assertIn('две минуты',render_response({},'timer_start',duration='две минуты'))

    def test_http_encoding_and_types(self):
        action={'type':'http','url':'http://localhost/items/{text}','method':'POST','arguments':{'text':{'required':True},'n':{'type':'number','default':2}},'query':{'q':'{text}'},'json':{'n':'{n}','text':'{text}'},'results':{'temperature':'data.temperature'}}
        validate_schema(action)
        with self.assertRaises(Clarification):resolve(action,{})
        with self.assertRaises(Clarification):resolve(action,{'text':'ok','n':float('nan')})
        value='a/b?x="&ю'
        url,headers,body=http_values(action,resolve(action,{'text':value}))
        self.assertIn('a%2Fb%3Fx%3D%22%26%D1%8E',url)
        self.assertEqual(body,{'n':2,'text':value})
        self.assertEqual(mapped_results(action,'{"data":{"temperature":-2}}'),{'temperature':-2})
        with self.assertRaises(ValueError):validate_schema({**action,'url':'http://{text}/'})
        with self.assertRaises(ValueError):validate_schema({**action,'json':{'x':'{unknown}'}})

    def test_voice_slots_and_ambiguity(self):
        a={'arguments':{'n':{'type':'number','required':True,'voice':{'prefix':'установи'}}}}
        self.assertEqual(voice_arguments(a,'установи двадцать пять'),{'n':25})
        with self.assertRaises(Clarification):voice_arguments(a,'установи 2 или 3')
        a={'arguments':{'delay':{'type':'duration','voice':{'prefix':'через'}}}}
        self.assertEqual(voice_arguments(a,'через две минуты'),{'delay':120})
        with self.assertRaises(Clarification):voice_arguments(a,'через две или три минуты')
        with self.assertRaises(Clarification):voice_arguments(a,'через две минуты три минуты')
        a={'arguments':{'room':{'type':'enum','values':['кухня','зал'],'voice':{}}}}
        self.assertEqual(voice_arguments(a,'выбери кухня'),{'room':'кухня'})
        a['arguments']['room'].update(required=True,voice={'prefix':'комната'})
        with self.assertRaises(Clarification):voice_arguments(a,'комната кухня или зал')

    def test_explicit_recordings_limits_privacy_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Recordings(directory)
            r.feed(b'normal command')
            self.assertFalse(list(Path(directory).iterdir()))
            r.start('спокойная речь');r.feed(b'\0'*1000000)
            entries=r.dispatch({'command':'record_list'})['recordings']
            self.assertEqual(entries[0]['seconds'],30)
            identifier=entries[0]['id']
            self.assertTrue(r.dispatch({'command':'record_replay','id':identifier})['audio'])
            r.dispatch({'command':'record_delete','id':identifier})
            self.assertFalse(list(Path(directory).iterdir()))
            r.start('');r.feed(b'\0'*3200);r.feed(b'',False)
            self.assertIsNone(r.buffer)
            for i in range(49):r.start('');r.stop()
            with self.assertRaises(ValueError):r.start('')
            with self.assertRaises(ValueError):r.file('../secret','.wav')

    def test_http_failure_and_no_request_on_missing_input(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import urllib.error
        import runner
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                calls.append(self.path);self.send_response(503);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        action={'type':'http','url':'http://127.0.0.1:%d/{n}'%server.server_port,'arguments':{'n':{'type':'number','required':True}}}
        try:
            with self.assertRaises(Clarification):runner.execute('test',{'test':action})
            self.assertEqual(calls,[])
            opener=runner.urllib.request.build_opener(runner.urllib.request.ProxyHandler({}))
            with patch('runner.urllib.request.urlopen',new=opener.open):
                with self.assertRaises(urllib.error.HTTPError):runner.execute('test',{'test':action},args={'n':2})
            self.assertEqual(calls,['/2'])
        finally:server.shutdown();server.server_close();thread.join()

    def test_custom_action_completion_for_legacy_string_result(self):
        import io,voice
        engine=voice.Engine.__new__(voice.Engine);engine.control=VoiceControl();engine.config={}
        privacy={"mode":"local","hardware_mic_muted":False,"mic_muted":False,"privacy":{"desired_muted":False,"applied_muted":False}}
        response=io.BytesIO(json.dumps({'ok':True,'result':'legacy speaker result'}).encode())
        with patch('voice.ALLOWED.is_set',return_value=True), patch('voice.ctl',return_value=privacy),patch('voice.os.environ',{'AUTOMATION_TOKEN':'test'}),patch('voice.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value=response
            self.assertEqual(engine.perform('action','test'),'Готово.')

    def test_face_uses_saved_choice_in_all_active_phases(self):
        import voice
        with patch('voice_config.read_voice',return_value=('',{'assistant_face':'saved'})),patch('voice.remote') as remote:
            for phase in ('listening','thinking','speaking'):voice.indicate(phase)
            faces=[call.args[0] for call in remote.call_args_list if 'face' in call.args[0]]
            self.assertEqual(len(faces),3)
            self.assertTrue(all(call[-2:]==['saved','30'] for call in faces))

    def test_numeric_template_pronunciation(self):
        from voice_numbers import pronounce_numbers
        spoken=pronounce_numbers('Температура -2.5, код 12345')
        self.assertIn('минус два точка пять',spoken)
        self.assertFalse(any(c.isdigit() for c in spoken))

    def test_wake_validation(self):
        control=VoiceControl();control.validate_wake=lambda text:text=='махи'
        self.assertTrue(control.dispatch({'command':'validate_wake','phrase':'махи'})['valid'])
        with self.assertRaises(ValueError):control.dispatch({'command':'validate_wake','phrase':'unsupported'})

if __name__=='__main__':unittest.main()
