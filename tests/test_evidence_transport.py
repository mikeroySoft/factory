"""Consumer-visible regression checks for the Astra transport findings on C1.

Producer (the real `factory evidence` CLI via subprocess, controlled `gh`
fixture, no live credentials):
- a full 100-case page whose wire response approaches 1 MiB must still emit
  one ASCII JSON envelope of at most 500000 bytes plus one newline (the
  `RESPONSE_CAP` contract), retaining useful partial evidence and reporting
  a truthful null attention count / honest omission notice;
- a hung GitHub read (fixture sleeps and forks a marked descendant) must
  return a bounded `collection_timeout` partial and leave no live
  descendant processes behind;
- a direct SIGINT during the same hung read must unwind cleanly (bounded
  outcome, no live descendants), matching production Ctrl+C behavior.

The C0 extension's real `collect` path is exercised by
console/c0-prototype/check-transport.ts (healthy read accepted, hung read
cancelled through an actual AbortSignal with no surviving descendants, and
a byte-bounded partial accepted instead of being discarded).

The byte-boundary assertions fail before the bounded transport fix and pass
after it; the hung-read and direct-SIGINT tests guard descendant reaping and
unwinding that the producer's finally block already covers on the baseline.

This class is standalone: no subclassing of `EvidenceCliTest` (discovery would
otherwise re-run its 19 tests); reusable helpers are bound from it narrowly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REPO = "example/evidence"
AT = "2026-01-02T03:04:05Z"
PREFIX = f"repos/{REPO}/"
# Transport contract: exactly one ASCII JSON object of at most 500000 bytes
# (evidence.py RESPONSE_CAP) plus one trailing newline on stdout.
RESPONSE_TOTAL = 500001

from factory import lifecycle  # noqa: E402
try:
    import test_evidence as _base
except ImportError:  # full-suite discovery imports tests as a package
    from tests import test_evidence as _base

# Contract label shape: the ready-for-human marker plus 19 label slots, each
# exactly 50 chars ("中" * 48 + two-digit slot position). CJK labels carry the
# multibyte weight, so the 100-case page's ASCII-escaped wire form exceeds the
# 500000-byte envelope cap while the raw gh input stays under the 1 MiB bound.

# Controlled `gh` stand-in. `fork` spawns a marked descendant in the gh
# child's own session (the producer isolates each gh call in a new
# session), so surviving-marker checks prove the producer reaped its whole
# descent — the unhandled Ctrl+C / cancel regression surface.
GH = '''
import json, os, sys, time
from urllib.parse import parse_qsl, urlencode, urlsplit
args = sys.argv[1:]
endpoint = next((a for a in args if a.startswith("repos/")), "")
parts = urlsplit(endpoint)
query = dict(parse_qsl(parts.query))
with open(os.environ["EVIDENCE_CALLS"], "a") as stream:
    stream.write(json.dumps({"path": parts.path}) + "\\n")
with open(os.environ["EVIDENCE_RESPONSES"]) as stream:
    responses = json.load(stream)
key = parts.path + ("?" + urlencode(sorted(query.items())) if query else "")
response = responses.get(key, responses.get(parts.path))
if response is None:
    raise SystemExit(96)
if response.get("fork"):
    if os.fork() == 0:
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.execvpe("python3", ["python3", "-c", "import time; time.sleep(120)", os.environ["EVIDENCE_FORK_MARKER"]], os.environ)
if response.get("sleep"):
    time.sleep(response["sleep"])
if "raw" in response:
    sys.stdout.write(response["raw"])
else:
    json.dump(response.get("json"), sys.stdout)
raise SystemExit(response.get("exit", 0))
'''


def expected_labels(number: int) -> list[str]:
    return ["ready-for-human"] + ["中" * 48 + f"{p:02d}" for p in range(1, 20)]


def build_issue(number: int) -> dict:
    # A realistic GitHub issue: 20 labels at the 50-char API cap — 19 unique
    # CJK labels per issue at the GitHub cap, plus the ready-for-human marker.
    return {"number": number, "title": f"Case {number:03d} transport-boundary probe",
            "state": "open", "html_url": f"https://github.com/{REPO}/issues/{number}",
            "labels": [{"name": name} for name in expected_labels(number)],
            "created_at": AT, "updated_at": AT}


def build_issues(count: int) -> list[dict]:
    return [build_issue(n) for n in range(1, count + 1)]


class EvidenceTransportTest(unittest.TestCase):
    def setUp(self):
        context = patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""})
        context.start()
        self.addCleanup(context.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.tools = self.directory / "bin"
        self.tools.mkdir()
        for name in ("git", "python3"):
            (self.tools / name).symlink_to(shutil.which(name))
        self.responses_path = self.directory / "responses.json"
        self.calls_path = self.directory / "calls.jsonl"
        self.marker = uuid.uuid4().hex
        self.env = {**os.environ, "PYTHONPATH": str(ROOT),
                    "PYTHONDONTWRITEBYTECODE": "1", "PATH": str(self.tools),
                    "HOME": str(self.directory / "home"),
                    "XDG_CONFIG_HOME": str(self.directory / "host"),
                    "GH_CONFIG_DIR": str(self.directory / "gh"),
                    "GH_TOKEN": "", "GITHUB_TOKEN": "", lifecycle.CONTEXT_ENV: "",
                    "EVIDENCE_FORK_MARKER": self.marker,
                    "EVIDENCE_RESPONSES": str(self.responses_path),
                    "EVIDENCE_CALLS": str(self.calls_path)}
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)],
                       check=True, capture_output=True)
        (self.root / ".factory.toml").write_text(
            f'[repo]\nslug = "{REPO}"\n[gate]\nlock = "{self.root / "gpu.lock"}"\n')
        self.responses = {}
        # Narrow bindings of reusable fixture helpers from the shared suite.
        for name in ("executable", "state", "error_codes"):
            setattr(self, name, getattr(_base.EvidenceCliTest, name).__get__(self, type(self)))
        self.executable("gh", GH)
        self.executable("systemctl", 'print("ActiveState=inactive\\nNextElapseUSecRealtime=0")\n')
        self.addCleanup(self.kill_marker_pids)

    def invoke(self, op="observe", *, code=0, timeout=45, **fields):
        self.responses_path.write_text(json.dumps(self.responses))
        before = self.state()
        payload = json.dumps({"schema_version": 1, "repository": REPO, "op": op, **fields}).encode()
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "factory.cli", "evidence", "--root", str(self.root)],
            cwd=self.root, env=self.env, input=payload, capture_output=True, timeout=timeout)
        self.elapsed = time.monotonic() - started
        self.raw = proc.stdout
        self.assertEqual(before, self.state(), "Evidence read changed the selected checkout")
        self.assertEqual(proc.returncode, code, (proc.stdout[:4000], proc.stderr.decode()))
        self.assertLess(len(proc.stdout), 1_048_576)
        data = json.loads(proc.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["ok"], code == 0)
        self.assertEqual(data["scope"], {"repository": REPO, "root": str(self.root)})
        for source in data["sources"]:
            self.assertTrue(all(isinstance(source[key], str) for key in ("id", "label", "text")))
        self.last = data
        return data

    def marker_pids(self) -> list[int]:
        """Live PIDs whose cmdline carries this run's unique marker (ours only)."""
        found = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as stream:
                    command = stream.read(8192)
            except OSError:
                continue
            if self.marker.encode() in command:
                found.append(int(entry))
        return found

    def kill_marker_pids(self) -> None:
        # Self-owned processes identified by this run's unique marker only.
        for pid in self.marker_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def reap_process(self, proc: subprocess.Popen) -> None:
        # Cleanup: an assertion failure must not orphan the CLI it spawned.
        if proc.poll() is None:
            proc.kill()
        proc.wait()


    def test_complete_page_reports_known_attention_not_null(self):
        self.responses[PREFIX + "issues"] = {"json": build_issues(8)}
        self.responses[PREFIX + "pulls"] = {"json": []}
        (self.root / ".factory").mkdir()
        (self.root / ".factory" / "events.jsonl").touch()
        (self.root / ".factory" / "locks").mkdir()
        (self.root / ".factory" / "locks" / "merge.lock").touch()
        (self.root / "gpu.lock").touch()
        data = self.invoke("observe")
        self.assertEqual(data["coverage"]["status"], "bounded")
        self.assertEqual(len(data["cases"]), 8)
        self.assertEqual(data["attention_count"], 8, "complete page must keep the grounded count")
        self.assertTrue(any(row["label"].startswith("Local runtime") for row in data["sources"]))
        self.assertLessEqual(len(self.raw), RESPONSE_TOTAL)
        first = data["cases"][0]
        self.assertEqual(first["number"], 8)
        self.assertEqual(first["stage"], "escalated")
        self.assertIn(expected_labels(8)[8], first["labels"])
        self.assertEqual(first["labels"], sorted(expected_labels(8)),
                         "labels must round-trip the full Unicode text unchanged")

    def test_hung_read_kills_every_descendant_without_false_success(self):
        self.responses[PREFIX + "issues"] = {"json": [], "sleep": 60, "fork": True}
        self.responses[PREFIX + "pulls"] = {"json": []}
        data = self.invoke("observe", code=1)
        self.assertLess(self.elapsed, 30, "one hung read must stay within the command bound")
        self.assertIn("collection_timeout", self.error_codes(data))
        self.assertEqual(data["coverage"]["status"], "partial")
        self.assertTrue(any(row["label"].startswith("Local runtime") for row in data["sources"]),
                        "independent local evidence must survive the hung read")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.marker_pids():
            time.sleep(0.2)
        self.assertEqual(self.marker_pids(), [], "hung GitHub read left live descendant processes")

    def test_byte_bounded_partial_preserves_useful_evidence(self):
        issues = build_issues(100)
        self.responses[PREFIX + "issues"] = {"json": issues}
        self.responses[PREFIX + "pulls"] = {"json": []}
        # The multilingual wire input sits within the per-read 1 MiB bound; the
        # ASCII-escaped producer output alone must exceed the 500000-byte cap.
        self.assertLess(len(json.dumps(self.responses).encode()), 1_048_576)
        self.assertGreater(len(json.dumps(issues, ensure_ascii=True, separators=(",", ":"))), 500_000,
                           "the ASCII-escaped page alone must exceed the response cap")
        data = self.invoke("observe", code=1)
        # Each retained source is byte-bounded (20k), not char-bounded: a
        # char-vs-byte bug in the producer would clip this boundary case.
        for source in data["sources"]:
            self.assertLessEqual(len(source["text"].encode("utf-8")), 20_000,
                                 source["label"])
        # ... but the emitted envelope stays at or under the contract cap.
        self.assertLessEqual(len(self.raw), RESPONSE_TOTAL)
        self.assertEqual(data["coverage"]["status"], "partial")
        self.assertIsNone(data["attention_count"], "reduced case coverage must not report a grounded count")
        count_errors = [error for error in data["errors"]
                        if error["scope"] == "attention_count"]
        self.assertEqual({error["code"] for error in count_errors}, {"issues_incomplete"})
        self.assertIn("output_truncated", self.error_codes(data))
        source_ids = {source["id"] for source in data["sources"]}
        self.assertTrue(all(error["source"] in source_ids for error in count_errors))
        self.assertTrue(any(notice.startswith("Attention count unavailable:")
                            for notice in data["coverage"]["notices"]))
        self.assertTrue(any(row["label"].startswith("Local runtime") for row in data["sources"]),
                        "the local runtime source must survive byte trimming")
        self.assertLess(len(data["cases"]), 100, "case clipping must be visible and honest")
        if data["cases"]:
            self.assertEqual(data["cases"][0]["number"], 100, "trimming keeps the newest cases")

    def test_direct_cli_sigint_unwounds_without_orphans(self):
        """Ctrl+C during a hung read: the producer already unwinds through its
        per-read finally block on the baseline; this guards that path for no
        published envelope, prompt signal exit (-2 unhandled, 130 handled), and
        no orphaned marker descendant in the gh descent."""
        self.responses[PREFIX + "issues"] = {"json": [], "sleep": 60, "fork": True}
        self.responses[PREFIX + "pulls"] = {"json": []}
        self.responses_path.write_text(json.dumps(self.responses))
        payload = json.dumps({"schema_version": 1, "repository": REPO, "op": "observe"}).encode()
        proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "factory.cli", "evidence", "--root", str(self.root)],
            cwd=self.root, env=self.env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.reap_process, proc)
        proc.stdin.write(payload)  # far under the pipe buffer; the child closes on EOF
        proc.stdin.close()
        served = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not served:
            if self.calls_path.exists():
                served = "issues" in self.calls_path.read_text()
            if not served:
                time.sleep(0.05)
        self.assertTrue(served, "the hung fixture was reached before interrupt")
        descended = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not descended:
            descended = bool(self.marker_pids())
            if not descended:
                time.sleep(0.05)
        self.assertTrue(descended, "marker descendant was not visible before interrupt; coverage gap")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("the CLI did not exit within 15s after SIGINT")
        self.assertEqual(proc.stdout.read(), b"", "a cancelled read must not publish an envelope")
        proc.stdout.close()
        proc.stderr.close()
        self.assertIn(proc.returncode, (-2, 130), "interrupt exits the CLI promptly (signum 130 once handled, -2 on an unhandled one)")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.marker_pids():
            time.sleep(0.2)
        self.assertEqual(self.marker_pids(), [], "direct SIGINT left live descendant processes")
if __name__ == "__main__":
    unittest.main()