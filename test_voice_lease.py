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
