"""Public evidence CLI trust boundaries, revision identity, and non-persisting reads."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from factory import briefing, config, lifecycle

ROOT = Path(__file__).resolve().parents[1]
REPO = "example/evidence"
AT = "2026-01-02T03:04:05Z"
PREFIX = f"repos/{REPO}/"

# The actual CLI runs under a Python audit hook, not patched Factory collectors.
# Child executables use -S so their fixture transcript is outside this guard.
READ_GUARD = '''
import os, sys

def guard(event, args):
    if (event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.truncate",
                  "os.chmod", "os.chown", "fcntl.flock"}
        or event == "open" and args[0] != os.devnull
        and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        or event in {"socket.connect", "socket.bind", "socket.sendto", "os.system"}):
        print("EVIDENCE_FORBIDDEN:" + event, file=sys.stderr)
        raise AssertionError("read-only boundary crossed")
sys.addaudithook(guard)
'''

GH = '''
import json, os, sys, time
from urllib.parse import parse_qsl, urlencode, urlsplit
args = sys.argv[1:]
endpoint = next((a for a in args if a.startswith("repos/")), "")
parts = urlsplit(endpoint)
query = dict(parse_qsl(parts.query))
method = args[args.index("--method") + 1] if "--method" in args else "GET"
with open(os.environ["EVIDENCE_CALLS"], "a") as stream:
    stream.write(json.dumps({"path": parts.path, "query": query, "method": method}) + "\\n")
if not args or args[0] != "api" or method != "GET" or not parts.path.startswith("repos/example/evidence/"):
    print("forbidden fixture command", file=sys.stderr)
    raise SystemExit(97)
with open(os.environ["EVIDENCE_RESPONSES"]) as stream:
    responses = json.load(stream)
key = parts.path + ("?" + urlencode(sorted(query.items())) if query else "")
response = responses.get(key, responses.get(parts.path))
if response is None:
    print("unregistered fixture endpoint", file=sys.stderr)
    raise SystemExit(96)
time.sleep(response.get("sleep", 0))
if response.get("stderr"):
    print(response["stderr"], file=sys.stderr)
if "repeat" in response:
    for _ in range(response["repeat"]):
        sys.stdout.write("x" * 65536)
        sys.stdout.flush()
elif "raw" in response:
    sys.stdout.write(response["raw"])
else:
    json.dump(response.get("json"), sys.stdout)
raise SystemExit(response.get("exit", 0))
'''


class EvidenceCliTest(unittest.TestCase):
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
        self.guard = self.directory / "guard"
        self.guard.mkdir()
        (self.guard / "sitecustomize.py").write_text(READ_GUARD)
        (self.tools / "git").symlink_to(shutil.which("git"))
        self.executable("gh", GH)
        self.executable("systemctl", 'print("ActiveState=inactive\\nNextElapseUSecRealtime=0")\n')
        self.responses_path = self.directory / "responses.json"
        self.calls_path = self.directory / "calls.jsonl"
        self.env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(self.guard), str(ROOT))),
                    "PYTHONDONTWRITEBYTECODE": "1", "PATH": str(self.tools),
                    "HOME": str(self.directory / "home"),
                    "XDG_CONFIG_HOME": str(self.directory / "host"),
                    "GH_CONFIG_DIR": str(self.directory / "gh"),
                    "GH_TOKEN": "", "GITHUB_TOKEN": "", lifecycle.CONTEXT_ENV: "",
                    "EVIDENCE_RESPONSES": str(self.responses_path),
                    "EVIDENCE_CALLS": str(self.calls_path)}
        self.git("init", "-q", "-b", "main")
        (self.root / ".factory.toml").write_text(
            f'[repo]\nslug = "{REPO}"\n[gate]\nlock = "{self.root / "gpu.lock"}"\n')
        (self.root / "README.txt").write_text("Repository input at PR head\n")
        (self.root / "link").symlink_to("private")
        self.git("add", ".")
        self.git("-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid",
                 "commit", "-qm", "PR head without workflow")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.git("branch", "topic")
        (self.root / ".github/workflows").mkdir(parents=True)
        self.workflow = "name: CI\non: [push, pull_request]\njobs: {}\n"
        (self.root / ".github/workflows/ci.yml").write_text(self.workflow)
        self.git("add", ".github")
        self.git("-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid",
                 "commit", "-qm", "Workflow on main only")
        self.main = self.git("rev-parse", "HEAD").strip()
        self.responses = {}
        self.issue = {"number": 7, "title": "Operator decision", "state": "open",
                      "html_url": f"https://github.com/{REPO}/issues/7", "body": "Keep the public API.",
                      "labels": [{"name": "ready-for-human"}], "assignees": [],
                      "created_at": AT, "updated_at": AT, "closed_at": None}
        self.pr = {"number": 17, "title": "Pending CI", "state": "open", "draft": False,
                   "html_url": f"https://github.com/{REPO}/pull/17", "updated_at": AT,
                   "created_at": AT, "closed_at": None, "merged_at": None,
                   "merged": False, "changed_files": 2, "body": "", "labels": [],
                   "head": {"sha": self.head, "ref": "topic", "label": "example:topic",
                            "repo": {"full_name": REPO}},
                   "base": {"sha": self.main, "ref": "main", "label": "example:main",
                            "repo": {"full_name": REPO}}}
        self.response("issues", [self.issue])
        self.response("pulls", [])
        self.response("issues/7", self.issue)
        self.response("issues/7/comments", [{"body": "Factory human decision: Preserve compatibility.",
                                           "created_at": AT, "updated_at": AT,
                                           "html_url": f"https://github.com/{REPO}/issues/7#issuecomment-1"}])
        self.response("issues/7/timeline", [])
        self.response("pulls/17", self.pr)
        self.response("pulls/17/files", [{"filename": "README.txt", "status": "modified", "patch": "@@ -1 +1 @@\n-old\n+new"},
                                         {"filename": "large.bin", "status": "added"}])
        self.response(f"commits/{self.head}/check-runs", {"total_count": 0, "check_runs": []})
        self.response(f"commits/{self.head}/status", {"sha": self.head, "state": "pending", "total_count": 0, "statuses": []})
        self.response("actions/runs", {"total_count": 0, "workflow_runs": []}, head_sha=self.head, per_page=100, page=1)
        self.run = {"id": 55, "head_sha": self.main, "head_branch": "main", "status": "completed",
                    "conclusion": "success", "run_attempt": 2, "created_at": AT, "updated_at": AT,
                    "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
                    "html_url": f"https://github.com/{REPO}/actions/runs/55"}
        self.response("actions/runs/55", self.run)
        self.response("actions/runs/55/jobs", {"total_count": 1, "jobs": [{"id": 501, "name": "unit", "conclusion": "success"}]})
        self.response("actions/jobs/501/logs", raw="\x1b[32mPASS\x1b[0m\nunit finished\n")
        self.response("actions/workflows", {"total_count": 1, "workflows": [{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}]})
        for revision, sha in (("main", self.main), ("topic", self.head), (self.main, self.main), (self.head, self.head)):
            tree = self.git("rev-parse", f"{sha}^{{tree}}").strip()
            self.response(f"commits/{revision}", {"sha": sha, "commit": {"tree": {"sha": tree}, "committer": {"date": AT}}})
            self.tree_response(tree)
        self.response("contents/.github/workflows/ci.yml", {"type": "file", "encoding": "base64",
                      "sha": self.git("rev-parse", f"{self.main}:.github/workflows/ci.yml").strip(),
                      "content": base64.b64encode(self.workflow.encode()).decode()}, ref=self.main)
        self.response("contents/README.txt", {"type": "file", "encoding": "base64",
                      "sha": self.git("rev-parse", f"{self.head}:README.txt").strip(),
                      "content": base64.b64encode(b"Repository input at PR head\n").decode()}, ref=self.head)
        self.journal = self.root / ".factory/events.jsonl"

    def executable(self, name, content):
        path = self.tools / name
        path.write_text(f"#!{sys.executable} -S\n" + content)
        path.chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                              text=True, check=True).stdout

    def response(self, endpoint, value=None, *, raw=None, **query):
        key = PREFIX + endpoint
        if query:
            key += "?" + urlencode(sorted((key, str(value)) for key, value in query.items()))
        self.responses[key] = {"json": value} if raw is None else {"raw": raw}
        return key

    def tree_response(self, sha):
        if PREFIX + f"git/trees/{sha}" in self.responses:
            return
        entries = []
        for row in self.git("ls-tree", "-z", sha).split("\0"):
            if not row:
                continue
            metadata, path = row.split("\t", 1)
            mode, kind, child = metadata.split()
            entries.append({"mode": mode, "type": kind, "sha": child, "path": path})
            if kind == "tree":
                self.tree_response(child)
        self.response(f"git/trees/{sha}", {"sha": sha, "truncated": False, "tree": entries})

    def state(self):
        result = {}
        for path in self.root.rglob("*"):
            info = path.lstat()
            content = path.read_bytes() if stat.S_ISREG(info.st_mode) else os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
            result[str(path.relative_to(self.root))] = (info.st_ino, info.st_mtime_ns, info.st_mode, content)
        return result

    def invoke(self, op="capabilities", *, raw=None, root=None, code=0, timeout=30, **fields):
        self.responses_path.write_text(json.dumps(self.responses))
        before = self.state()
        payload = raw if raw is not None else json.dumps({"schema_version": 1, "repository": REPO, "op": op, **fields}).encode()
        if isinstance(payload, str):
            payload = payload.encode()
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-B", "-m", "factory.cli", "evidence", "--root", str(root or self.root)],
                              cwd=self.directory, env=self.env, input=payload, capture_output=True, timeout=timeout)
        self.elapsed = time.monotonic() - started
        self.assertNotIn(b"EVIDENCE_FORBIDDEN:", proc.stderr, proc.stderr.decode())
        self.assertEqual(before, self.state(), "Evidence read changed the selected checkout")
        self.assertEqual(proc.returncode, code, (proc.stdout.decode(), proc.stderr.decode()))
        self.assertLess(len(proc.stdout), 1_048_576)
        data = json.loads(proc.stdout)
        self.assertEqual(data["schema_version"], 1)
        self.assertIsInstance(data["observed_at"], str)
        self.assertIsInstance(data["observation_id"], str)
        self.assertIn(data["coverage"]["status"], ("complete", "bounded", "partial", "unavailable"))
        self.assertIsInstance(data["coverage"]["notices"], list)
        self.assertIsInstance(data["errors"], list)
        for error in data["errors"]:
            self.assertTrue(all(isinstance(error[key], str) for key in ("source", "scope", "code")))
        for source in data["sources"]:
            self.assertTrue(all(isinstance(source[key], str) for key in ("id", "label", "text")))
            self.assertIs(type(source["truncated"]), bool)
            self.assertLessEqual(len(source["text"].encode()), 20_000)
        self.assertEqual(data["ok"], code == 0)
        if code != 2:
            self.assertEqual(data["scope"], {"repository": REPO, "root": str(self.root)})
        self.last_output = data
        return data

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []

    def values(self, data):
        values = []
        for source in data["sources"]:
            try:
                values.append(json.loads(source["text"]))
            except ValueError:
                pass
        return values

    def error_codes(self, data):
        return {row["code"] for row in data["errors"]} | ({data["error"]["code"]} if "error" in data else set())

    def test_capabilities_are_explicit_and_do_not_collect_cases(self):
        data = self.invoke()
        menu = {(row["op"], row.get("kind")) for row in data["capabilities"]["reads"]}
        self.assertEqual(menu, {("capabilities", None), ("observe", None), ("inspect", None),
                               *(("investigate", kind) for kind in ("workflows", "file", "pr", "checks", "runs", "run", "log"))})
        self.assertEqual(data["capabilities"]["actions"], [])
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.journal.parent.exists())

    def test_strict_requests_never_collect_external_evidence(self):
        request = {"schema_version": 1, "repository": REPO, "op": "observe"}
        invalid = [b"", b"{", b"[]", b"{} {}", b"\xff", b" " * 4097,
                   '{"schema_version":1,"schema_version":1,"repository":"example/evidence","op":"observe"}',
                   {**request, "schema_version": True}, {**request, "schema_version": 2},
                   {**request, "op": "dispatch"}, {**request, "command": "touch forbidden"},
                   {**request, "repository": "https://github.com/example/evidence"},
                   {**request, "op": "inspect", "number": True},
                   {**request, "op": "inspect", "number": 0},
                   {**request, "op": "inspect", "number": 2**63},
                   {**request, "op": "investigate", "kind": "log", "run_id": False},
                   {**request, "op": "investigate", "kind": "workflows", "number": 7},
                   {**request, "op": "investigate", "kind": "checks", "number": 17, "ref": "main"}]
        for path, ref in (("../private", "main"), ("/private", "main"), ("a//b", "main"),
                          ("a%2fb", "main"), ("a\\b", "main"), ("a?ref=x", "main"),
                          ("a", "main~1"), ("a", "HEAD@{1}"), ("a", "--help"),
                          ("a", "main:private"), ("a", "a..b")):
            invalid.append({**request, "op": "investigate", "kind": "file", "path": path, "ref": ref})
        for payload in invalid:
            with self.subTest(payload=payload):
                data = self.invoke(raw=json.dumps(payload) if isinstance(payload, dict) else payload, code=2)
                self.assertIn("invalid_request", self.error_codes(data))
                self.assertEqual(data["sources"], [])
        self.assertEqual(self.calls(), [])

    def test_explicit_main_checkout_and_repository_scope_are_required(self):
        data = self.invoke(repository="other/repository", code=2)
        self.assertIn("scope_mismatch", self.error_codes(data))
        (self.root / "subdir").mkdir()
        for root in (self.root / "subdir", self.directory, self.directory / "absent"):
            with self.subTest(root=root):
                data = self.invoke(root=root, code=2)
                self.assertIn("invalid_scope", self.error_codes(data))
        worktree = self.directory / "worktree"
        self.git("worktree", "add", "-q", "--detach", str(worktree), self.head)
        data = self.invoke(root=worktree, code=2)
        self.assertIn("invalid_scope", self.error_codes(data))
        self.assertEqual(self.calls(), [])

    def test_invalid_cli_invocation_is_a_machine_readable_error(self):
        for arguments in ([], ["--root"], ["--root", str(self.root), "--shell"]):
            proc = subprocess.run([sys.executable, "-B", "-m", "factory.cli", "evidence", *arguments],
                                  env=self.env, cwd=self.directory, capture_output=True, timeout=5)
            self.assertEqual(proc.returncode, 2)
            data = json.loads(proc.stdout)
            self.assertFalse(data["ok"])
            self.assertIn(data["error"]["code"], ("invalid_request", "invalid_scope"))
        self.assertEqual(self.calls(), [])

    def test_main_workflow_and_success_do_not_establish_pr_head_ci(self):
        workflows = self.invoke("investigate", kind="workflows")
        self.assertEqual(self.values(workflows)[0]["workflows"][0]["path"], ".github/workflows/ci.yml")
        present = self.invoke("investigate", kind="file", path=".github/workflows/ci.yml", ref="main")
        self.assertEqual(present["investigation"]["commit_sha"], self.main)
        self.assertIn(self.workflow, [row["text"] for row in present["sources"]])
        absent = self.invoke("investigate", kind="file", path=".github/workflows/ci.yml", ref=self.head, code=1)
        self.assertIn("file_not_found", self.error_codes(absent))
        negative = next(value for value in self.values(absent) if isinstance(value, dict) and value.get("tree_complete"))
        self.assertEqual(negative["commit_sha"], self.head)
        self.assertEqual(negative["path"], ".github/workflows/ci.yml")
        self.assertEqual(negative["missing_component"], ".github")
        for kind, key in (("checks", "check_runs"), ("runs", "workflow_runs")):
            data = self.invoke("investigate", kind=kind, number=17)
            self.assertEqual(data["investigation"]["head_sha"], self.head)
            self.assertEqual(next(value[key] for value in self.values(data) if isinstance(value, dict) and key in value), [])
        run = self.invoke("investigate", kind="run", run_id=55)
        detail = next(value for value in self.values(run) if isinstance(value, dict) and value.get("id") == 55)
        self.assertEqual((detail["head_sha"], detail["conclusion"]), (self.main, "success"))
        calls = self.calls()
        self.assertTrue(all(row["query"].get("head_sha") == self.head for row in calls if row["path"] == PREFIX + "actions/runs"))
        self.assertFalse(any(self.main in row["path"] and row["path"].endswith(("check-runs", "/status")) for row in calls))
        content = [row for row in calls if "/contents/" in row["path"]]
        self.assertEqual([row["query"]["ref"] for row in content], [self.main])

    def test_pr_diff_keeps_head_base_and_omitted_patch_uncertainty(self):
        data = self.invoke("investigate", kind="pr", number=17)
        summary = next(value for value in self.values(data) if isinstance(value, dict) and "head" in value)
        self.assertEqual(summary["head"]["sha"], self.head)
        self.assertEqual(summary["base"]["sha"], self.main)
        files = next(value for value in self.values(data) if isinstance(value, list))
        self.assertIn("patch", files[0])
        self.assertNotIn("patch", files[1])
        self.assertTrue(data["coverage"]["notices"])

    def test_truncated_tree_is_not_cited_as_absence_and_links_are_never_dereferenced(self):
        tree = self.git("rev-parse", f"{self.head}^{{tree}}").strip()
        self.responses[PREFIX + f"git/trees/{tree}"]["json"]["truncated"] = True
        data = self.invoke("investigate", kind="file", path="missing", ref="topic", code=1)
        self.assertIn("incomplete_tree", self.error_codes(data))
        self.assertNotIn("file_not_found", self.error_codes(data))
        self.assertFalse(any(isinstance(value, dict) and value.get("tree_complete") for value in self.values(data)))
        self.responses[PREFIX + f"git/trees/{tree}"]["json"]["truncated"] = False
        for path, ref in (("link", "topic"), ("link/secret", "topic"), (".github", "main")):
            with self.subTest(path=path):
                data = self.invoke("investigate", kind="file", path=path, ref=ref, code=1)
                self.assertIn("unsupported_file", self.error_codes(data))
        self.assertFalse(any("/contents/" in row["path"] for row in self.calls()))

    def test_submodules_and_non_utf8_files_are_not_returned_as_text(self):
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.head},submodule")
        self.git("-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid",
                 "commit", "-qm", "Record gitlink without checking it out")
        sha = self.git("rev-parse", "HEAD").strip()
        tree = self.git("rev-parse", "HEAD^{tree}").strip()
        self.response(f"commits/{sha}", {"sha": sha, "commit": {"tree": {"sha": tree}}})
        self.tree_response(tree)
        data = self.invoke("investigate", kind="file", path="submodule", ref=sha, code=1)
        self.assertIn("unsupported_file", self.error_codes(data))
        self.assertFalse(any("/contents/" in row["path"] for row in self.calls()))
        self.response("contents/README.txt", {"type": "file", "encoding": "base64",
                      "sha": self.git("rev-parse", f"{self.head}:README.txt").strip(),
                      "content": base64.b64encode(b"\xff\xfe").decode()}, ref=self.head)
        data = self.invoke("investigate", kind="file", path="README.txt", ref=self.head, code=1)
        self.assertIn("unsupported_file", self.error_codes(data))

    def test_independent_check_status_survives_permission_and_oversize_failures(self):
        endpoint = PREFIX + f"commits/{self.head}/check-runs"
        for failure, expected in (({"exit": 1, "stderr": "HTTP 403 credential=PRIVATE_TOKEN"}, "github_forbidden"),
                                  ({"repeat": 32}, "response_too_large"),
                                  ({"raw": "not json"}, "github_unavailable")):
            with self.subTest(failure=failure):
                self.responses[endpoint] = failure
                data = self.invoke("investigate", kind="checks", number=17, code=1)
                self.assertIn(expected, self.error_codes(data))
                self.assertEqual(data["coverage"]["status"], "partial")
                self.assertTrue(any(isinstance(value, dict) and "statuses" in value for value in self.values(data)))
                self.assertNotIn("PRIVATE_TOKEN", json.dumps(data))

    def test_hung_command_is_killed_and_other_head_evidence_survives(self):
        self.responses[PREFIX + f"commits/{self.head}/check-runs"] = {"sleep": 60}
        data = self.invoke("investigate", kind="checks", number=17, code=1, timeout=27)
        self.assertLess(self.elapsed, 25)
        self.assertIn("collection_timeout", self.error_codes(data))
        self.assertEqual(data["investigation"]["head_sha"], self.head)
        self.assertTrue(any(isinstance(value, dict) and "statuses" in value for value in self.values(data)))

    def test_log_prefixes_are_safe_capped_failed_first_and_partial(self):
        jobs = [{"id": index, "name": f"job-{index}", "conclusion": "failure" if index == 507 else "success"}
                for index in range(501, 508)]
        self.response("actions/runs/55/jobs", {"total_count": 7, "jobs": jobs})
        for job in jobs:
            self.response(f"actions/jobs/{job['id']}/logs", raw="\x1b[32mPASS\x1b[0m\x1b]0;untrusted title\x07\n" + "line\n" * 6000)
        self.responses[PREFIX + "actions/jobs/502/logs"] = {"exit": 1, "stderr": "expired PRIVATE_TOKEN"}
        data = self.invoke("investigate", kind="log", run_id=55, code=1)
        logs = [source for source in data["sources"] if source.get("url", "").split("?", 1)[0].endswith("/logs")]
        self.assertEqual(len(logs), 4)
        self.assertTrue(all(source["truncated"] for source in logs))
        self.assertTrue(all("PASS" in source["text"] and "\x1b" not in source["text"] and "\x07" not in source["text"] for source in logs))
        calls = [row for row in self.calls() if row["path"].endswith("/logs")]
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0]["path"], PREFIX + "actions/jobs/507/logs")
        self.assertTrue(all(row["query"].get("filter") == "latest" for row in self.calls() if row["path"].endswith("/jobs")))
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(data))

    def test_successful_log_capture_and_bounded_workflow_page(self):
        data = self.invoke("investigate", kind="log", run_id=55)
        self.assertIn("PASS\nunit finished\n", [row["text"] for row in data["sources"]])
        self.response("actions/workflows", {"total_count": 101, "workflows": [{"id": n, "path": f".github/workflows/{n}.yml"} for n in range(100)]})
        data = self.invoke("investigate", kind="workflows")
        self.assertTrue(data["sources"][0]["truncated"])
        self.assertEqual(len([row for row in self.calls() if row["path"].endswith("/workflows")]), 1)

    def test_cross_repository_response_cannot_relabel_foreign_run(self):
        self.run["repository"]["full_name"] = "other/project"
        data = self.invoke("investigate", kind="run", run_id=55, code=1)
        self.assertFalse(any(isinstance(value, dict) and value.get("id") == 55 for value in self.values(data)))
        self.assertFalse(any(row["path"].endswith("/jobs") for row in self.calls()))

    def test_observe_is_compact_and_absent_state_is_never_created(self):
        first = self.invoke("observe", code=1)
        second = self.invoke("observe", code=1)
        self.assertEqual(first["cases"][0]["number"], 7)
        self.assertEqual(first["cases"][0]["stage"], "escalated")
        self.assertEqual(first["attention_count"], 1)
        self.assertNotIn("Keep the public API.", json.dumps(first["sources"]))
        self.assertNotIn("case", first)
        self.assertFalse(self.journal.parent.exists())
        self.assertNotEqual(first["observation_id"], second["observation_id"])
        self.assertGreater(second["observed_at"], first["observed_at"])
        summary = next(row for row in first["sources"] if json.loads(row["text"]) == first["cases"])
        self.assertIn(summary, second["sources"])

    def test_complete_local_state_supports_successful_case_reads(self):
        (self.journal.parent / "locks").mkdir(parents=True)
        self.journal.touch()
        (self.journal.parent / "locks/merge.lock").touch()
        (self.root / "gpu.lock").touch()
        observed = self.invoke("observe")
        self.assertEqual(observed["attention_count"], 1)
        self.assertEqual(observed["cases"][0]["updated_at"], AT)
        inspected = self.invoke("inspect", number=7)
        self.assertEqual(inspected["case"], observed["cases"][0])
        self.assertIn(self.issue["body"], [row["text"] for row in inspected["sources"]])

    def test_unavailable_github_is_unknown_attention_not_empty_success(self):
        self.responses[PREFIX + "issues"] = {"exit": 1, "stderr": "PRIVATE_TOKEN denied"}
        data = self.invoke("observe", code=1)
        self.assertIsNone(data["attention_count"])
        self.assertNotIn("PRIVATE_TOKEN", json.dumps(data))
        inspect = self.invoke("inspect", number=7, code=1)
        self.assertIsNone(inspect["case"])

    def test_missing_gh_retains_local_history_and_unknown_attention(self):
        self.journal.parent.mkdir()
        execution = lifecycle.Execution(self.journal, "review", ticket=7)
        entered = execution.emit("enter")
        terminal = execution.emit("exit")
        (self.tools / "gh").unlink()
        data = self.invoke("observe", code=1)
        self.assertIsNone(data["attention_count"])
        self.assertIn("github_unavailable", self.error_codes(data))
        runtime = next(value for value in self.values(data) if isinstance(value, dict) and "executions" in value)
        self.assertEqual(runtime["executions"][0]["state"], "completed")
        self.assertEqual([row["event_id"] for row in runtime["events"]], [entered["event_id"], terminal["event_id"]])

    def test_full_candidate_page_is_unknown_attention_not_a_zero(self):
        self.response("issues", [{**self.issue, "number": number} for number in range(1, 102)])
        data = self.invoke("observe", code=1)
        self.assertEqual(len(data["cases"]), 100)
        self.assertIsNone(data["attention_count"])
        self.assertTrue(data["coverage"]["notices"])
        self.assertEqual(len([row for row in self.calls() if row["path"] == PREFIX + "issues"]), 1)

    def test_inspect_preserves_artifact_decisions_and_runtime_history_without_writes(self):
        self.journal.parent.mkdir()
        entry = lifecycle.Execution(self.journal, "worker", ticket=7, attempt=1)
        entry.process = {**entry.process, "boot_id": "previous-boot"}
        entered = entry.emit("enter")
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"event": "human-decision", "ticket": 7, "at": AT,
                                    "decision_id": "d1", "status": "applied", "comment": "Keep compatibility"}) + "\n")
            stream.write('{"event":"lifecycle"')
        packet = self.journal.parent / "escalations/7.md"
        packet.parent.mkdir()
        packet.write_text(f"# Escalation #7\n\nCheck failed\n\nRecorded at {AT}\n")
        (self.journal.parent / "manager-8.md").write_text("OTHER TICKET SECRET")
        (self.journal.parent / "review-7.md").symlink_to("manager-8.md")
        (self.journal.parent / "wt-7/.factory").mkdir(parents=True)
        (self.journal.parent / "wt-7/.factory/handoff-7.md").write_text("Actual worker handoff")
        first = self.invoke("inspect", number=7, code=1)
        second = self.invoke("inspect", number=7, code=1)
        self.assertEqual(first["case"]["updated_at"], AT)
        self.assertEqual(first["case"]["number"], 7)
        sources = {row.get("path"): row for row in first["sources"] if row.get("path")}
        self.assertEqual(sources["escalations/7.md"]["text"], packet.read_text())
        self.assertEqual(sources["wt-7/.factory/handoff-7.md"]["text"], "Actual worker handoff")
        self.assertIn("Keep compatibility", json.dumps(first["sources"]))
        self.assertIn(entered["event_id"], json.dumps(first["sources"]))
        self.assertNotIn("OTHER TICKET SECRET", json.dumps(first))
        historical = [row for row in first["sources"] if row.get("path") or row.get("url") == self.issue["html_url"]]
        self.assertTrue(historical)
        for source in historical:
            self.assertIn(source, second["sources"])
        self.assertNotEqual(first["observation_id"], second["observation_id"])
        self.assertGreater(second["observed_at"], first["observed_at"])
        self.assertTrue(first["errors"])
        runtime = next(value for value in self.values(first) if isinstance(value, dict) and "executions" in value)
        self.assertEqual(runtime["executions"][0]["state"], "interrupted")
        self.assertIsNone(runtime["executions"][0]["ended_at"])
        self.assertFalse(runtime["history"]["complete"])
        self.assertEqual(runtime["events"][0]["at"], entered["at"])
        # The dashboard's briefing consumer must still select the same producer
        # artifacts, not the prototype's obsolete escalation-7.json path.
        ticket = {"number": 7, "title": self.issue["title"], "body": self.issue["body"],
                  "url": self.issue["html_url"], "events": [], "attempts": []}
        dashboard_sources = briefing.sources_for(config.Config(root=self.root, repo=REPO), ticket, [])
        selected = {row["path"]: row for row in dashboard_sources if row.get("path")}
        for path in ("escalations/7.md", "wt-7/.factory/handoff-7.md"):
            self.assertEqual(sources[path], selected[path])


if __name__ == "__main__":
    unittest.main()
