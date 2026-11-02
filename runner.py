"""Small configured action runner. No dynamic shell expressions or arbitrary targets."""
import hmac
import pathlib
import dashboard
import json
import os
import shlex
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
import weather
from voice_ipc import request as voice_request
from action_args import validate_schema, resolve, http_values, mapped_results, Clarification, voice_arguments, substitute

LIMIT = threading.BoundedSemaphore(4)
CONFIG_PATH = os.environ.get("ACTIONS_CONFIG", "/config/actions.yaml")

def load_config():
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)
    return validate_actions(config)

def validate_actions(config):
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("configuration version must be 1")
    actions = config.get("actions", {})
    for name, action in actions.items():
        validate_schema(action)
        if action.get("type") not in ("speaker", "http", "ssh", "sequence", "weather.refresh"):
            raise ValueError("invalid action type: " + name)
        if action["type"] == "sequence":
            if not 1 <= len(action.get("steps", [])) <= 8:
                raise ValueError("sequence must contain 1..8 named steps")
            for step in action["steps"]:
                if step not in actions or actions[step]["type"] == "sequence":
                    raise ValueError("steps must reference non-sequence actions")
    return actions

def ssh_run(host, port, user, key, argv, timeout=8,max_output=4096,allow_rpc_error=False):
    if not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("SSH argv must be a nonempty string list")
    command = ["ssh", "-T", "-i", key, "-p", str(port),
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
               "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=/secrets/known_hosts",
               user + "@" + host, shlex.join(argv)]
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    output=result.stdout[:max_output].decode(errors="replace")
    if result.returncode:
        if allow_rpc_error:
            try:
                reply=json.loads(output)
                if isinstance(reply,dict) and reply.get("ok") is False and isinstance(reply.get("error"),str):
                    return output
            except ValueError:pass
        raise RuntimeError("SSH action failed (exit %d)" % result.returncode)
    return output

def execute(name, actions, deadline=None, args=None):
    if deadline is None:
        deadline = time.monotonic() + 8
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("action deadline exceeded")
    a = actions[name]
    values=resolve(a,args)
    kind = a["type"]
    if kind == "weather.refresh":
        return weather.refresh(send_speaker)
    if kind == "sequence":
        return [execute(step, actions, deadline) for step in a["steps"]]
    if kind == "speaker":
        command=[str(v) for v in substitute(a["args"],values)]
        try:
            with open(dashboard.VOICE_PATH) as f: voice_config=yaml.safe_load(f)
        except FileNotFoundError:voice_config={}
        seconds=voice_config.get("action_catalog",{}).get("server:"+name,{}).get("display_seconds")
        if seconds and len(command)>=2:
            size={"clock text":3,"clock face":3,"clock draw":3,"clock segments":6,"weather show":2}.get(" ".join(command[:2]))
            if size and len(command) in (size,size+1):command=command[:size]+[str(seconds)]
        return ssh_run(os.environ["SPEAKER_HOST"], 2222, "root",
                       "/secrets/speaker_key", ["/data/opt/bin/mini2ctl"] + command, timeout=remaining)
    if kind == "ssh":
        return ssh_run(a["host"], a.get("port", 22), a["user"], a["key"], [str(v) for v in substitute(a["argv"],values)], timeout=remaining)
    if kind == "http":
        url, headers, json_body = http_values(a,values)
        for header, variable in a.get("secret_headers", {}).items():
            headers[header] = os.environ[variable]
        body = None
        if "json" in a:
            body = json.dumps(json_body).encode()
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method=a.get("method", "GET"))
        with urllib.request.urlopen(request, timeout=min(5, remaining)) as response:
            body=response.read(65537)
            if len(body)>65536:raise ValueError("HTTP response exceeds 64KiB")
            text=body.decode(errors="replace")
            return {"status": response.status, "body": text,"results":mapped_results(a,text)}
    raise ValueError("unknown action type")

def send_speaker(args):
    output=ssh_run(os.environ["SPEAKER_HOST"],2222,"root","/secrets/speaker_key",
                   ["/data/opt/bin/mini2ctl"]+args,max_output=262144,allow_rpc_error=True)
    reply=json.loads(output)
    if not reply.get("ok"):raise ValueError(reply.get("error","speaker rejected command"))
    return output

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log bearer tokens, request arguments, or API output.

    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization",""),
                                   "Bearer "+os.environ["AUTOMATION_TOKEN"])

    def dashboard_get(self):
        if not self.authorized():
            self.reply(401,{"error":"unauthorized"});return
        if not LIMIT.acquire(blocking=False):
            self.reply(429,{"error":"runner busy"});return
        try:self.reply(200,{"ok":True,"data":dashboard.get(self.path,send_speaker,load_config)})
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{"error":str(e)[:200]})
        except Exception:self.reply(502,{"error":"speaker or configuration unavailable"})
        finally:LIMIT.release()

    def dashboard_post(self):
        if not LIMIT.acquire(blocking=False):
            self.reply(429,{"error":"runner busy"});return
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=131072:raise ValueError("request must be1..131072bytes")
            self.connection.settimeout(5)
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError("request must be an object")
            result=dashboard.post(self.path,data,send_speaker,validate_actions)
            self.reply(200,{"ok":True,"data":result})
        except (ValueError,KeyError,TypeError) as e:self.reply(400,{"error":str(e)[:200]})
        except Exception:self.reply(502,{"error":"speaker or configuration unavailable"})
        finally:LIMIT.release()

    def do_GET(self):
        static={"/":"index.html","/index.html":"index.html","/app.js":"app.js","/style.css":"style.css"}
        if self.path in static:
            path=pathlib.Path("/app/dist")/static[self.path]
            try:body=path.read_bytes()
            except OSError:self.reply(404,{"error":"dashboard not installed"});return
            mime={"index.html":"text/html; charset=utf-8","app.js":"text/javascript; charset=utf-8","style.css":"text/css; charset=utf-8"}
            self.send_response(200);self.send_header("Content-Type",mime[path.name])
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Security-Policy","default-src 'self'; media-src 'self' data:; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("X-Content-Type-Options","nosniff");self.end_headers();self.wfile.write(body);return
        if self.path.startswith("/ui/"):
            self.dashboard_get();return
        if self.path == "/health":
            self.reply(200, {"ok": True})
        elif self.path == "/voice/status":
            expected = "Bearer " + os.environ["AUTOMATION_TOKEN"]
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.reply(401, {"error": "unauthorized"})
                return
            try:
                with open("/state/status.json") as f:
                    self.reply(200, json.load(f))
            except (OSError, ValueError):
                self.reply(503, {"error": "voice not ready"})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        expected = "Bearer " + os.environ["AUTOMATION_TOKEN"]
        if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            self.reply(401, {"error": "unauthorized"})
            return
        if self.path == "/voice/interrupt":
            try:
                voice_request("interrupt")
                self.reply(200,{"ok":True,"interrupted":True})
            except Exception:
                self.reply(503,{"error":"voice service unavailable"})
            return
        if self.path.startswith("/ui/"):
            self.dashboard_post();return
        if self.path != "/run":
            self.reply(404, {"error": "not found"})
            return
        if not LIMIT.acquire(blocking=False):
            self.reply(429, {"error": "runner busy"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                self.reply(400, {"error": "request length must be 1..8192"})
                return
            self.connection.settimeout(5)
            data = json.loads(self.rfile.read(length))
            name = data["action"]
            actions = load_config()  # Validated file edits take effect on the next request.
            if name not in actions:
                self.reply(404, {"error": "unknown action"})
                return
            if "voice_owner" in data:
                owned=json.loads(send_speaker(["voice","lease","check",data["voice_owner"]])).get("data")
                if owned is not True:
                    self.reply(409,{"error":"voice ownership expired"});return
            args=voice_arguments(actions[name],data["voice_text"]) if "voice_text" in data else resolve(actions[name],data.get("args"))
            result = execute(name, actions,args=args)
            self.reply(200, {"ok": True, "result": result})
        except Clarification as error:
            self.reply(400,{"error":str(error),"clarification":True})
        except (ValueError, KeyError, TypeError):
            self.reply(400, {"error": "invalid request or action configuration"})
        except Exception:
            self.reply(502, {"error": "configured action failed or target unavailable"})
        finally:
            LIMIT.release()

if __name__ == "__main__":
    load_config()
    if not os.environ.get("AUTOMATION_TOKEN"):
        raise RuntimeError("AUTOMATION_TOKEN required")
    threading.Thread(target=weather.loop,args=(send_speaker,),daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8787), Handler).serve_forever()
