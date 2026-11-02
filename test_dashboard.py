import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import yaml
import dashboard

class DashboardTests(unittest.TestCase):
    def test_controls_do_not_allow_shell_or_mode_switch(self):
        for args in (["mode","yandex"],["sh","-c","anything"],["config","apply","bad"],["weather","update","{}"],["mute","restore"]):
            with self.assertRaises(ValueError):dashboard.control(args,lambda _:None)
        value=dashboard.control(["clock","segments","G","","","G","2"],lambda _:json.dumps({"ok":True,"data":"drawn"}))
        self.assertEqual(value,"drawn")

    def test_speaker_config_validation_failure_is_reported(self):
        with self.assertRaisesRegex(ValueError,"unknown action"):
            dashboard.post("/ui/speaker-config",{"config":{"version":1}},
                lambda _:json.dumps({"ok":False,"error":"unknown action"}),lambda _:None)

    def test_voice_edit_preserves_model_and_rejects_bad_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/"voice.yaml"
            original={"wake_word":"махи","whisper_model":"small","tts_engine":"silero","tts_speaker":"baya"}
            path.write_text(yaml.safe_dump(original))
            with patch.object(dashboard,"VOICE_PATH",str(path)):
                dashboard.post("/ui/voice-config",{"tts_speaker":"xenia","intent_triggers":{"timer_start":["засеки"]}},None,None)
                saved=yaml.safe_load(path.read_text())
                self.assertEqual(saved["whisper_model"],"small")
                self.assertEqual(saved["tts_speaker"],"xenia")
                before=path.read_text()
                with self.assertRaises(ValueError):dashboard.post("/ui/voice-config",{"tts_volume":2},None,None)
                self.assertEqual(path.read_text(),before)
                with self.assertRaises(ValueError):dashboard.post("/ui/voice-config",{"tts_engine":"bad"},None,None)
                with self.assertRaises(ValueError):dashboard.post("/ui/voice-config",{"wake_word":"unverified"},None,None)

    def test_unknown_preview_voice_rejected(self):
        with self.assertRaises(ValueError):dashboard.post("/ui/voice-preview",{"speaker":"arbitrary-file"},None,None)

if __name__=="__main__":unittest.main()
