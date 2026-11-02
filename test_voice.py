import unittest
from voice_intents import classify, duration_from_text, strip_wake
from voice import privacy_allowed

class VoiceTests(unittest.TestCase):
    def test_bilingual_timers(self):
        for text,expected in [
            ("поставь таймер на две минуты",120),
            ("таймер на двадцать пять секунд",25),
            ("таймер на один час тридцать минут",5400),
            ("set a timer for two minutes",120),
            ("set a timer for twenty five seconds",25),
            ("таймер на минуту",60),
            ("засеки две минуты",120),
        ]:
            self.assertEqual(classify(text),("timer_start",expected))
        for text in ("таймер на сто часов","timer for zero seconds","таймер на сколько минут"):
            self.assertEqual(classify(text)[0],"clarify_timer")

    def test_intents(self):
        for text,intent in [
            ("отмени таймер","timer_cancel"),("stop timer","timer_cancel"),
            ("сколько времени осталось","timer_status"),
            ("какая погода","weather"),("what's the weather","weather"),
            ("который час","time"),("сделай тише","volume_down"),
            ("volume up","volume_up"),("rm -rf something","unknown"),
        ]:
            self.assertEqual(classify(text)[0],intent)

    def test_aliases_are_exact(self):
        aliases={"включи компьютер":"pc_on"}
        self.assertEqual(classify("Включи компьютер!",aliases),("action","pc_on"))
        self.assertEqual(classify("не включи компьютер",aliases),("unknown",None))
        self.assertEqual(strip_wake("Махи, поставь таймер","махи"),"поставь таймер")
        self.assertEqual(strip_wake("махина", "махи"),"махина")

    def test_privacy_fails_closed(self):
        good={"mode":"local","hardware_mic_muted":False,"mic_muted":False,
              "privacy":{"desired_muted":False,"applied_muted":False}}
        self.assertTrue(privacy_allowed(good))
        self.assertFalse(privacy_allowed({}))
        for key in ("hardware_mic_muted","mic_muted"):
            self.assertFalse(privacy_allowed({**good,key:True}))
        self.assertFalse(privacy_allowed({**good,"privacy":{"desired_muted":True}}))
        self.assertFalse(privacy_allowed({**good,"mode":"yandex"}))
        self.assertFalse(privacy_allowed({**good,"privacy":{"error":"failed"}}))

if __name__=="__main__":unittest.main()

class TriggerConfigTests(unittest.TestCase):
    def test_custom_triggers(self):
        from voice_config import validate_voice
        self.assertEqual(classify("засеки две минуты"),("timer_start",120))
        self.assertEqual(classify("отмерь две минуты",triggers={"timer_start":["отмерь"]}),("timer_start",120))
        self.assertEqual(classify("засеки две минуты",triggers={"timer_start":["отмерь"]}),("unknown",None))
        with self.assertRaises(ValueError):validate_voice({"wake_word":"махи","intent_triggers":{"bad":["x"]}})

class NumberAndKeywordTests(unittest.TestCase):
    def test_context_keywords_and_numbers(self):
        from voice_numbers import number,duration,clock
        self.assertEqual(classify("сказала плохо какая погода"),("weather",None))
        self.assertEqual(classify("отмени"),("timer_cancel",None))
        self.assertEqual(classify("запусти таймер 5мин"),("timer_start",300))
        self.assertEqual(number(-12),"минус двенадцать")
        self.assertEqual(duration(125),"две минуты и пять секунд")
        self.assertEqual(clock(18,5),"восемнадцать часов пять минут")
        self.assertFalse(any(c.isdigit() for c in duration(5999)))
        self.assertEqual(classify("don't stop timer"),("unknown",None))

    def test_keyword_aliases_and_ambiguity(self):
        self.assertEqual(classify("компьютер пожалуйста включи",{"включи компьютер":"pc_on"}),("action","pc_on"))
        self.assertEqual(classify("включи компьютер свет",{"включи компьютер":"pc_on","включи свет":"light_on"}),("ambiguous",None))

class SpokenReplyTests(unittest.TestCase):
    def test_numbers_are_words_and_cancel_is_contextual(self):
        from unittest.mock import patch
        from voice import Engine
        from voice_ipc import VoiceControl
        e=Engine.__new__(Engine);e.control=VoiceControl()
        privacy={"mode":"local","hardware_mic_muted":False,"mic_muted":False,
                 "privacy":{"desired_muted":False,"applied_muted":False}}
        def call(*args):
            if args==("status",):return privacy
            if args==("timer","status"):return {"active":False}
            return {}
        with patch("voice.ALLOWED.is_set",return_value=True), patch("voice.ctl",side_effect=call) as command:
            reply=e.perform("timer_start",120)
            self.assertIn("две минуты",reply)
            self.assertFalse(any(c.isdigit() for c in reply))
            command.reset_mock()
            self.assertEqual(e.perform("timer_cancel",None),"Сейчас нет таймера.")
            self.assertNotIn(("timer","cancel"),[c.args for c in command.call_args_list])
        e.control.dispatch({"command":"start"})
        with patch("voice.ctl") as command:
            self.assertEqual(e.perform("timer_start",120),"")
            command.assert_not_called()

class VoiceHistoryTests(unittest.TestCase):
    def test_history_is_bounded_and_updates_current_request(self):
        import voice
        from unittest.mock import patch
        state={}
        with patch.object(voice, "STATE", state), patch.object(voice, "status", side_effect=lambda **values:state.update(values)):
            for index in range(12):voice.voice_event(new=True, wake=str(index))
            voice.voice_event(text="таймер", intent="timer_start", outcome="completed")
        self.assertEqual(len(state["history"]),10)
        self.assertEqual(state["history"][0]["wake"],"11")
        self.assertEqual(state["history"][0]["text"],"таймер")
        self.assertEqual(state["history"][-1]["wake"],"2")

    def test_silent_reply_does_not_invoke_synthesis(self):
        from voice import Engine
        from unittest.mock import Mock
        assistant=object.__new__(Engine)
        assistant.speech=Mock()
        assistant.say("")
        assistant.say("   ")
        assistant.speech.wav.assert_not_called()

    def test_request_audio_is_valid_wav_and_retention_is_bounded(self):
        import voice, tempfile, pathlib, wave
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory, patch.object(voice,"HISTORY_AUDIO",pathlib.Path(directory)), patch.object(voice,"voice_event") as event:
            for _ in range(12):voice.record_request_audio(b"\x01\x00"*160)
            self.assertEqual(len(list(pathlib.Path(directory).glob("*.wav"))),10)
            name=event.call_args.kwargs["audio_id"]
            with wave.open(str(pathlib.Path(directory)/(name+".wav"))) as audio:
                self.assertEqual(audio.getframerate(),16000)
                self.assertEqual(audio.readframes(160),b"\x01\x00"*160)
