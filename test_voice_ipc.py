import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import voice_ipc

class VoiceIPCTests(unittest.TestCase):
    def test_interrupt_sets_cancel_signal_and_callback(self):
        c=voice_ipc.VoiceControl();calls=[];c.interrupt_callback=lambda:calls.append(True)
        result=c.dispatch({"command":"interrupt"})
        self.assertTrue(c.cancelled.is_set());self.assertEqual(result,{"interrupted":True});self.assertEqual(calls,[True])

    def test_memory_only_transcript_and_stop(self):
        c=voice_ipc.VoiceControl()
        c.transcript("ignored")
        self.assertEqual(c.dispatch({"command":"status"})["text"],"")
        c.dispatch({"command":"start"});c.transcript("погода")
        self.assertEqual(c.dispatch({"command":"status"})["text"],"погода")
        c.dispatch({"command":"stop"})
        self.assertEqual(c.dispatch({"command":"status"})["text"],"")
        c.dispatch({"command":"start"});c.lease_until=time.monotonic()-1
        self.assertFalse(c.active())

    def test_private_socket(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(voice_ipc,"SOCKET_PATH",directory+"/voice.sock"):
            c=voice_ipc.VoiceControl();stop=threading.Event()
            thread=threading.Thread(target=c.serve,args=(stop,),daemon=True);thread.start()
            path=pathlib.Path(voice_ipc.SOCKET_PATH)
            for _ in range(100):
                if path.exists():break
                time.sleep(.01)
            try:
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                self.assertTrue(voice_ipc.request("start")["testing"])
                self.assertFalse(voice_ipc.request("stop")["testing"])
            finally:stop.set();thread.join(2)
            self.assertFalse(path.exists())
