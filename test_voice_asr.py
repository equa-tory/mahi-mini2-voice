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
        with patch("voice_asr.ResilientWhisperCpp") as factory:
            self.assertIs(load_asr("small-cpp"), factory.return_value)
        with patch.dict("os.environ", {"WHISPER_CPP_URL": "http://example.org/inference"}):
            with self.assertRaises(ValueError):load_asr("small-cpp")

    def test_compatible_profiles(self):
        for name in ("base", "small", "small-cpp"):
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
            segments, _ = backend.transcribe(np.array([0., .5, -2.]), language="ru")
        self.assertEqual(segments[0].text, "две минуты")
        request = opener.open.call_args.args[0]
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + request.get_header("Content-type") + "\r\n\r\n").encode() + request.data)
        fields = {p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
                  for p in message.iter_parts()}
        self.assertEqual(fields["language"], b"ru")
        self.assertEqual(fields["temperature_inc"], b"0")
        with wave.open(io.BytesIO(fields["file"]), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()), (1, 2, 16000, 3))
        opener.open.return_value = io.BytesIO(b'{"error":"failed"}')
        with patch("voice_asr.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(ValueError):backend.transcribe(np.zeros(10))
        opener.open.side_effect = urllib.error.URLError("offline")
        with patch("voice_asr.urllib.request.build_opener", return_value=opener):
            with self.assertRaises(urllib.error.URLError):backend.transcribe(np.zeros(10))

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
        backend.gpu=Mock();backend.cpu=Mock()
        backend.gpu.transcribe.side_effect=OSError("offline")
        backend.cpu.transcribe.return_value=(iter(["cpu"]),None)
        self.assertEqual(backend.transcribe([],language="ru"),(["cpu"],None))
        self.assertEqual(backend.backend,"faster-whisper-small-cpu-fallback")
        backend.gpu.transcribe.side_effect=None
        backend.gpu.transcribe.return_value=(["gpu"],None)
        self.assertEqual(backend.transcribe([],language="ru"),(["gpu"],None))
        self.assertNotIn("fallback",backend.backend)
        self.assertEqual(backend.cpu.transcribe.call_count,1)
