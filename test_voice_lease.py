import unittest
from unittest.mock import patch
from voice_lease import VoiceLease

class VoiceLeaseTests(unittest.TestCase):
    def test_health_failure_never_renews(self):
        calls=[]
        def failed():raise OSError('offline')
        with patch.dict('os.environ',{'VOICE_PUBLIC_URL':'http://127.0.0.1:8787'}):lease=VoiceLease(lambda *args:calls.append(args),failed)
        self.assertFalse(lease.renew());self.assertEqual(calls,[])

    def test_grant_denial_and_session_isolation(self):
        calls=[]
        replies=iter(({'granted':True},{'granted':False}))
        def ctl(*args):calls.append(args);return next(replies)
        with patch.dict('os.environ',{'VOICE_PUBLIC_URL':'http://127.0.0.1:8787','VOICE_ROLE':'standby'}):
            lease=VoiceLease(ctl,lambda:None);other=VoiceLease(ctl,lambda:None)
        self.assertNotEqual(lease.node,other.node)
        self.assertTrue(lease.renew());self.assertFalse(lease.renew())
        self.assertEqual(calls[0][:3],('voice','lease','acquire'))
        self.assertEqual(calls[1][:3],('voice','lease','renew'))
        self.assertFalse(lease.owned)

    def test_speaker_mutation_is_fenced_after_ownership_loss(self):
        import voice
        from types import SimpleNamespace
        with patch.object(voice,'LEASE',SimpleNamespace(node='old-session')),patch.object(voice,'remote',return_value=b'{"ok":true,"data":false}') as remote:
            with self.assertRaisesRegex(RuntimeError,'ownership expired'):voice.ctl('timer','start','120')
            self.assertEqual(remote.call_count,1)
            self.assertEqual(remote.call_args.args[0][-4:],['voice','lease','check','old-session'])
