"""Real HTTP request shaping and endpoint serialization, including separate repos/processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from factory import config, triage


class TriageRequests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(triage.__dict__))
        self.enterContext(patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
        }))
        for key in ("FACTORY_LLM_URL", "FACTORY_LLM_MODEL"):
            self.enterContext(patch.dict(os.environ))
            os.environ.pop(key, None)
        self.requests = []
        records = self.requests
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                row = {"body": body, "start": time.monotonic()}
                with lock:
                    records.append(row)
                time.sleep(0.1)  # Keep concurrent clients observable on the recording server.
                reply = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                with lock:
                    row["end"] = time.monotonic()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"

    def load(self, settings):
        (self.root / config.CONFIG_NAME).write_text('[repo]\nslug = "local/triage"\n')
        host_path = config.host_config_path()
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(f'[defaults.triage]\nurl = "{self.url}"\n{settings}')
        with patch.object(config, "repo_root", return_value=self.root):
            return config.load(self.root)

    def test_recorded_request_contract(self):
        for settings, tokens, kwargs in (
            ('max_tokens = 73\n[defaults.triage.chat_template_kwargs]\n'
             'enable_thinking = false\ncustom = { modes = ["brief", "json"] }\n',
             73, {"enable_thinking": False, "custom": {"modes": ["brief", "json"]}}),
            ('max_tokens = 29\n[defaults.triage.chat_template_kwargs]\n', 29, {}),
            ('max_tokens = 31\n', 31, None),
            ('', None, None),
        ):
            with self.subTest(settings=settings):
                triage.configure(self.load(settings))
                self.requests.clear()
                barrier = threading.Barrier(3)

                def ask(_):
                    barrier.wait(timeout=5)
                    return triage.call_llm([{"role": "user", "content": "hi"}])

                with ThreadPoolExecutor(max_workers=3) as pool:
                    self.assertEqual(list(pool.map(ask, range(3))), ["ok"] * 3)
                self.assertEqual(len(self.requests), 3)
                for row in self.requests:
                    if tokens is None:
                        self.assertIs(type(row["body"].get("max_tokens")), int)
                        self.assertGreater(row["body"]["max_tokens"], 0)
                    else:
                        self.assertEqual(row["body"].get("max_tokens"), tokens)
                    self.assertEqual("chat_template_kwargs" in row["body"], kwargs is not None)
                    if kwargs is not None:
                        self.assertEqual(row["body"]["chat_template_kwargs"], kwargs)
                ordered = sorted(self.requests, key=lambda row: row["start"])
                for before, after in zip(ordered, ordered[1:]):
                    self.assertLessEqual(before["end"], after["start"])

    def test_same_endpoint_serializes_across_repository_processes(self):
        self.load('max_tokens = 47\n')
        script = '''
import sys
from pathlib import Path
from unittest.mock import patch
from factory import config, triage
root = Path(sys.argv[1])
with patch.object(config, "repo_root", return_value=root):
    triage.configure(config.load(root))
print("ready", flush=True)
sys.stdin.readline()
assert triage.call_llm([{"role": "user", "content": "hi"}]) == "ok"
'''
        processes = []
        try:
            for index in range(3):
                repo = self.root / f"repo-{index}"
                repo.mkdir()
                (repo / config.CONFIG_NAME).write_text(f'[repo]\nslug = "local/repo-{index}"\n')
                processes.append(subprocess.Popen(
                    [sys.executable, "-c", script, str(repo)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), "ready")
            for process in processes:
                process.stdin.write("go\n")
                process.stdin.flush()
            for process in processes:
                out, err = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, out + err)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        self.assertEqual(len(self.requests), 3)
        ordered = sorted(self.requests, key=lambda row: row["start"])
        for before, after in zip(ordered, ordered[1:]):
            self.assertLessEqual(before["end"], after["start"])
        self.assertEqual([row["body"].get("max_tokens") for row in self.requests], [47] * 3)

    def test_invalid_request_settings_are_rejected(self):
        for settings, error in (
            ('max_tokens = 0', "triage.max_tokens"),
            ('max_tokens = -1', "triage.max_tokens"),
            ('max_tokens = true', "triage.max_tokens"),
            ('max_tokens = 1.5', "triage.max_tokens"),
            ('max_tokens = "100"', "triage.max_tokens"),
            ('chat_template_kwargs = false', "triage.chat_template_kwargs"),
        ):
            with self.subTest(settings=settings), self.assertRaisesRegex(config.ConfigError, error):
                self.load(settings)
