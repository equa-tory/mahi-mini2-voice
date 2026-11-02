import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import runner

class RunnerTests(unittest.TestCase):
    def setUp(self):
        os.environ["AUTOMATION_TOKEN"] = "test-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), runner.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, body, token="test-token"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d/run" % self.server.server_port,
            json.dumps(body).encode(),
            {"Authorization": "Bearer " + token}, method="POST")
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_auth(self):
        with patch.object(runner, "execute") as execute:
            self.assertEqual(self.request({"action": "demo"}, "wrong")[0], 401)
            execute.assert_not_called()

    def test_allowlist_and_no_dynamic_arguments(self):
        with patch.object(runner, "load_config", return_value={"demo": {"type":"speaker"}}), patch.object(runner, "execute") as execute:
            self.assertEqual(self.request({"action":"missing"})[0],404)
            self.assertEqual(self.request({"action":"demo","args":["rm"]})[0],400)
            execute.assert_not_called()

    def test_action_result_and_failure(self):
        with patch.object(runner, "load_config", return_value={"demo": {"type":"speaker"}}), patch.object(runner, "execute",return_value="ok"):
            self.assertEqual(self.request({"action":"demo"})[1]["result"],"ok")
        with patch.object(runner, "load_config", return_value={"demo": {"type":"speaker"}}), patch.object(runner, "execute",side_effect=TimeoutError("secret")):
            status,body=self.request({"action":"demo"})
            self.assertEqual(status,502)
            self.assertNotIn("secret",json.dumps(body))

if __name__=="__main__":
    unittest.main()

class VoiceStatusTests(RunnerTests):
    def test_voice_status_auth_and_response(self):
        from unittest.mock import mock_open
        url="http://127.0.0.1:%d/voice/status"%self.server.server_port
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(url)
        self.assertEqual(error.exception.code,401)
        request=urllib.request.Request(url,headers={"Authorization":"Bearer test-token"})
        with patch("builtins.open",mock_open(read_data='{"phase":"idle"}')):
            with opener.open(request) as response:
                self.assertEqual(json.load(response)["phase"],"idle")

class SpeakerValidationTests(unittest.TestCase):
    def test_cli_validation_is_not_a_transport_failure(self):
        import subprocess
        process=subprocess.CompletedProcess([],1,stdout=b'{"ok":false,"error":"mute hold unsupported"}',stderr=b'')
        with patch.object(runner.subprocess,"run",return_value=process):
            output=runner.ssh_run("speaker",2222,"root","key",["command"],allow_rpc_error=True)
            self.assertFalse(json.loads(output)["ok"])
            with self.assertRaises(RuntimeError):
                runner.ssh_run("speaker",2222,"root","key",["command"])
