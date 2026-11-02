import importlib.util
import io
import json
import unittest
import urllib.error
import wave
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock, patch

from voice_asr import WhisperCpp, ResilientWhisperCpp, load_asr
from voice_config import validate_voice


class BackendTests(unittest.TestCase):
    def test_local_readiness(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"status":"ok"}')
        with patch("voice_asr.Path.is_file",return_value=True), patch("voice_asr.ResilientWhisperCpp") as factory:
            self.assertIs(load_asr("small-cpp"), factory.return_value)
        with patch.dict("os.environ", {"WHISPER_CPP_URL": "http://example.org/inference"}):
            with self.assertRaises(ValueError):load_asr("small-cpp")

    def test_compatible_profiles(self):
        for name in ("base", "small", "small-cpp", "turbo-cpp", "off"):
            validate_voice({"wake_word": "махи", "whisper_model": name})
        with self.assertRaises(ValueError):
            validate_voice({"wake_word": "махи", "whisper_model": "other"})

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "run in voice image")
    def test_context_covers_long_audio(self):
        import numpy as np
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"text":"test"}')
        with patch.dict("os.environ", {"WHISPER_CPP_AUDIO_CONTEXT": "512"}), patch(
                "voice_asr.urllib.request.build_opener", return_value=opener):
            WhisperCpp().transcribe(np.zeros(16000 * 12), language="ru")
        self.assertIn(b'name="audio_ctx"\r\n\r\n650\r\n', opener.open.call_args.args[0].data)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "run in voice image")
    def test_wav_request_and_response(self):
        import numpy as np
        backend = WhisperCpp()
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({"text": "две минуты"}).encode())
        with patch("voice_asr.urllib.request.build_opener", return_value=opener):
            segments, _ = backend.transcribe(np.array([0., .5, -2.]), language="ru", beam_size=5,
                                             initial_prompt="Махи. Таймер на одну минуту.")
        self.assertEqual(segments[0].text, "две минуты")
        request = opener.open.call_args.args[0]
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + request.data)
        fields = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
                  for p in message.iter_parts()}
        self.assertEqual(fields["language"], b"ru")
        self.assertEqual(fields["temperature_inc"], b"0")
        self.assertEqual(fields["beam_size"], b"5")
        self.assertEqual(fields["prompt"], "Махи. Таймер на одну минуту.".encode())
        with wave.open(io.BytesIO(fields["file"]), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 16000, 3))
        opener.open.return_value = io.BytesIO(b'{"error":"failed"}')
        with patch("voice_asr.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(ValueError):backend.transcribe(np.zeros(10))
        opener.open.side_effect = urllib.error.URLError("offline")
        with patch("voice_asr.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.URLError):backend.transcribe(np.zeros(10))

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "run in voice image")
    def test_unclear_command_gets_one_prompted_retry(self):
        from types import SimpleNamespace
        import voice
        import numpy as np
        engine=voice.Engine.__new__(voice.Engine)
        engine.config={"speech_language":"ru-only"};engine.wake="махи"
        engine.control=Mock()
        engine.asr=Mock()
        engine.asr.transcribe.side_effect=[
            ([SimpleNamespace(text=" какой-то шум")],None),
            ([SimpleNamespace(text=" таймер на одну минуту")],None)]
        with patch.object(voice,"status"), patch.object(voice,"voice_event") as event, patch.object(voice,"LEASE",None):
            self.assertEqual(engine.decode(np.zeros(16000,dtype=np.int16).tobytes()),("timer_start",60))
        self.assertEqual(engine.asr.transcribe.call_count,2)
        self.assertEqual(engine.asr.transcribe.call_args.kwargs["beam_size"],5)
        self.assertTrue(engine.asr.transcribe.call_args.kwargs["initial_prompt"])
        self.assertEqual(event.call_args_list[0].kwargs["command"],"таймер на одну минуту")

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "run in voice image")
    def test_known_hallucination_does_not_get_retried(self):
        from types import SimpleNamespace
        import voice
        import numpy as np
        engine=voice.Engine.__new__(voice.Engine)
        engine.config={"speech_language":"ru-only"};engine.wake="махи"
        engine.control=Mock();engine.asr=Mock()
        engine.asr.transcribe.return_value=([SimpleNamespace(text=" Продолжение следует...")],None)
        with patch.object(voice,"status"), patch.object(voice,"voice_event"), patch.object(voice,"LEASE",None):
            self.assertEqual(engine.decode(np.zeros(16000,dtype=np.int16).tobytes()),("ignored",None))
        self.assertEqual(engine.asr.transcribe.call_count,1)

class LanguageTests(unittest.TestCase):
    def test_russian_only_has_no_english_retry(self):
        from voice import recognition_languages
        for testing in (False, True):
            self.assertEqual(recognition_languages("ru-only", testing), ["ru"])
        self.assertEqual(recognition_languages("ru"), ["ru", "en"])
        self.assertEqual(recognition_languages("auto"), [None])
        validate_voice({"wake_word": "махи", "speech_language": "ru-only"})

class FallbackTests(unittest.TestCase):
    def test_service_failure_and_recovery(self):
        backend=ResilientWhisperCpp.__new__(ResilientWhisperCpp)
        backend.gpu=Mock();backend.gpu.model_file="ggml-small-q5_1.bin";backend.cpu=Mock()
        backend.gpu.transcribe.side_effect=OSError("offline")
        backend.cpu.transcribe.return_value=(iter(["cpu"]),None)
        self.assertEqual(backend.transcribe([],language="ru"),(["cpu"],None))
        self.assertEqual(backend.backend,"faster-whisper-small-cpu-fallback")
        backend.gpu.transcribe.side_effect=None
        backend.gpu.transcribe.return_value=(["gpu"],None)
        self.assertEqual(backend.transcribe([],language="ru"),(["gpu"],None))
        self.assertNotIn("fallback",backend.backend)
        self.assertEqual(backend.cpu.transcribe.call_count,1)

class ModelControlTests(unittest.TestCase):
    def test_gpu_model_mapping_and_missing_model(self):
        from voice_asr import GPU_MODELS
        with patch("voice_asr.Path.is_file",return_value=True), patch("voice_asr.ResilientWhisperCpp") as factory:
            for profile,filename in GPU_MODELS.items():
                load_asr(profile)
                factory.assert_called_with(filename)
        with patch("voice_asr.Path.is_file",return_value=False):
            with self.assertRaisesRegex(ValueError,"not installed"):load_asr("turbo-cpp")
        self.assertIsNone(load_asr("off"))

    def test_known_hallucinations(self):
        from voice_asr import hallucinated_transcript
        for text in ("Продолжение следует...", "Субтитры сделал Алексей", "Редактор субтитров Н.Закомолдина", "[BLANK_AUDIO]"):
            self.assertTrue(hallucinated_transcript(text),text)
        for text in ("Махи, который час?", "Махи стоп", "таймер на минуту", "включи субтитры"):
            self.assertFalse(hallucinated_transcript(text),text)

    def test_unloaded_lease_does_not_acquire(self):
        from voice_lease import VoiceLease
        ctl=Mock()
        lease=VoiceLease(ctl,model="off")
        self.assertFalse(lease.renew())
        ctl.assert_not_called()

class ServerOperationTests(unittest.TestCase):
    def test_unload_has_a_valid_empty_body(self):
        from voice_asr import server_operation
        opener=Mock();opener.open.return_value=io.BytesIO(b"Unloaded")
        with patch("voice_asr.urllib.request.build_opener",return_value=opener):
            server_operation("unload")
        req=opener.open.call_args.args[0]
        self.assertEqual(req.data,b"")
        self.assertEqual(req.get_header("Content-type"),"application/octet-stream")
