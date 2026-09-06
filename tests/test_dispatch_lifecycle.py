"""Exercise real CLI boundaries; all GitHub/model/worker commands are local stand-ins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from factory import lifecycle


GH = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["SMOKE_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
issue = {"number": 7, "title": "local change", "body": "Acceptance: make the local change", "comments": [], "labels": [{"name": "ready-for-agent"}], "assignees": [], "state": "OPEN"}
if a[:2] == ["issue", "view"]:
    print(json.dumps(issue))
elif a[:2] == ["issue", "list"]:
    print("[]")
elif a[:2] == ["pr", "list"]:
    print(json.dumps([{"number": 70, "headRefName": "agent/7", "isDraft": False, "labels": [{"name": "factory-approved"}], "reviewDecision": "APPROVED"}] if s.get("pr") else []))
elif a[:2] == ["pr", "create"]:
    s["pr"] = True
    p.write_text(json.dumps(s))
    print("https://example.invalid/pull/70")
elif a[:2] == ["pr", "checks"]:
    print(json.dumps([{"name": "ci", "bucket": "pending"}]))
elif a[:2] not in (["issue", "edit"], ["issue", "comment"], ["pr", "edit"], ["pr", "comment"]):
    raise SystemExit("Unexpected external operation: " + repr(a))
'''

WORKER = '''from pathlib import Path
p = Path("change.txt")
p.write_text(p.read_text() + "change\\n" if p.exists() else "change\\n")
'''

REVIEWER = '''import json, os
from pathlib import Path
p = Path(os.environ["SMOKE_STATE"])
s = json.loads(p.read_text())
s["reviews"] = s.get("reviews", 0) + 1
p.write_text(json.dumps(s))
print("VERDICT: REVISE" if s["reviews"] == 1 else "VERDICT: APPROVE")
'''


class LocalCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.state = self.base / "state.json"
        self.state.write_text("{}")
        self.worker = self.base / "worker.py"
        self.worker.write_text(WORKER)
        self.reviewer = self.base / "reviewer.py"
        self.reviewer.write_text(REVIEWER)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    "XDG_CONFIG_HOME": str(self.base / "config"), "SMOKE_STATE": str(self.state),
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        self.env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Lifecycle Smoke")
        self.git("config", "user.email", "smoke@example.invalid")
        self.write_config()
        (self.repo / ".gitignore").write_text(".factory/\n.factory-prompt.md\n")
        self.git("add", ".factory.toml", ".gitignore")
        self.git("commit", "-m", "disposable fixture")
        origin = self.base / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)], env=self.env, check=True, capture_output=True)
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "-u", "origin", "main")
        self.events = self.repo / ".factory" / "events.jsonl"

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True, capture_output=True, text=True)

    def write_config(self, check=None, worker=None):
        argv = check or [sys.executable, "-c", "raise SystemExit(0)"]
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "local/smoke"\n'
            '[dispatch]\nmax_attempts = 2\nreview_rounds = 1\n'
            '[workers]\ndefault = ' + json.dumps(worker or [sys.executable, str(self.worker)]) + '\n'
            '[review]\ncommand = ' + json.dumps([sys.executable, str(self.reviewer)]) + '\n'
            '[leak_scan]\npattern = ""\n'
            '[gate]\nlock = ' + json.dumps(str(self.base / "host.lock")) + '\n'
            '[[gate.check]]\nname = "code"\nrun = ' + json.dumps(argv) + '\n'
        )

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-m", "factory", *args], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def rows(self):
        return [r for r in lifecycle.read_events(self.events) if r.get("event") == "lifecycle"]

    def test_revision_loop_and_later_pr_revisit(self):
        self.cli("dispatch", "--ticket", "7")
        rows = self.rows()
        entries = [r for r in rows if r["kind"] == "enter" and r["stage"] in {"worker", "gate", "review"}]
        self.assertEqual([r["stage"] for r in entries], ["worker", "gate", "review", "worker", "gate", "review"])
        self.assertEqual([r["attempt"] for r in entries], [1, 1, 1, 3, 3, 3])
        self.assertEqual([r["review_round"] for r in entries], [None, None, 1, 2, 2, 2])
        self.assertEqual(len({r["execution_id"] for r in entries}), 6)
        first_run = entries[0]["dispatcher_run_id"]
        self.assertTrue(all(r["ticket"] == 7 and r["dispatcher_run_id"] == first_run for r in entries))
        review_exits = [r["outcome"] for r in rows if r["stage"] == "review" and r["kind"] == "exit"]
        self.assertEqual(review_exits, ["product_feedback", "approved"])
        self.assertFalse(any(r["stage"] == "merge" for r in rows))
        self.cli("dispatch")
        rows = self.rows()
        revisit = [r for r in rows if r["stage"] == "merge-eligibility" and r["kind"] == "exit"]
        self.assertEqual(len(revisit), 1)
        self.assertEqual((revisit[0]["ticket"], revisit[0]["outcome"], revisit[0]["reason"]), (7, "not_eligible", "ci_pending"))
        self.assertNotEqual(revisit[0]["dispatcher_run_id"], first_run)
        self.assertNotIn(revisit[0]["execution_id"], {r["execution_id"] for r in entries})
        self.assertEqual(len({r["event_id"] for r in rows}), len(rows))
        for execution_id in {r["execution_id"] for r in rows}:
            order = [r["sequence"] for r in rows if r["execution_id"] == execution_id]
            self.assertEqual(order, list(range(1, len(order) + 1)))
        legacy = [r for r in lifecycle.read_events(self.events) if r.get("event") == "attempt"]
        self.assertEqual(len(legacy), 2)

    def test_ticketless_invocations_and_side_effect_free_dry_run(self):
        self.cli("dispatch", "--dry-run")
        self.cli("dispatch", "--ticket", "7", "--dry-run")
        self.cli("triage", "--dry-run")
        self.assertFalse((self.repo / ".factory").exists())
        self.cli("dispatch")
        self.cli("triage")
        rows = self.rows()
        dispatcher = [r for r in rows if r["stage"] == "dispatcher" and r["kind"] == "exit"]
        triage = [r for r in rows if r["stage"] == "triage" and r["kind"] == "exit"]
        self.assertEqual(len(dispatcher), 1)
        self.assertEqual(len(triage), 1)
        self.assertIsNone(dispatcher[0]["ticket"])
        self.assertIsNotNone(dispatcher[0]["dispatcher_run_id"])
        self.assertIsNone(triage[0]["dispatcher_run_id"])
        self.assertIsNone(triage[0]["ticket"])
        self.assertFalse(any(r["stage"] in {"worker", "gate", "review", "merge"} for r in rows))

    def test_code_failure_missing_mechanism_and_skipped_check(self):
        self.write_config(check=[sys.executable, "-c", "raise SystemExit(1)"])
        self.cli("gate", expected=1)
        failures = [r for r in self.rows() if r["kind"] == "exit" and r["stage"] == "gate"]
        self.assertEqual(failures[-1]["outcome"], "product_feedback")
        self.write_config(check=[str(self.base / "absent-executable")])
        result = subprocess.run([sys.executable, "-m", "factory", "gate"], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        failures = [r for r in self.rows() if r["kind"] == "exit" and r["stage"] == "gate"]
        self.assertEqual(failures[-1]["outcome"], "mechanism_failure")
        self.write_config(check=[sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"])
        self.cli("gate", expected=1)
        failures = [r for r in self.rows() if r["kind"] == "exit" and r["stage"] == "gate"]
        self.assertEqual(failures[-1]["outcome"], "unknown")
        count = len(self.rows())
        self.cli("gate", "--skip", "code,conflict-markers")
        self.assertFalse(any(r["stage"] == "gate-check" for r in self.rows()[count:]))

    def test_upstream_dry_run_does_not_fetch_or_create_runtime_state(self):
        import shutil
        self.git("remote", "add", "upstream", "https://github.com/local/upstream.git")
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text().replace('slug = "local/smoke"', 'slug = "local/smoke"\nupstream = "upstream"'))
        git = self.bin / "git"
        git.write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            "if sys.argv[1] == 'fetch':\n"
            "    raise SystemExit('dry-run attempted a mutating fetch')\n"
            f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n"
        )
        git.chmod(0o755)
        self.cli("dispatch", "--dry-run")
        self.assertFalse((self.repo / ".factory").exists())
        self.assertFalse((self.repo / ".git" / "FETCH_HEAD").exists())

    def test_upstream_merge_records_completion_only_after_local_push(self):
        import shutil
        upstream = self.base / "upstream.git"
        source = self.base / "upstream-source"
        def git(*args):
            return subprocess.run(["git", *args], env=self.env, check=True, capture_output=True, text=True)
        git("clone", "--bare", str(self.base / "origin.git"), str(upstream))
        git("clone", "-b", "main", str(upstream), str(source))
        git("-C", str(source), "config", "user.name", "Local Upstream")
        git("-C", str(source), "config", "user.email", "upstream@example.invalid")
        (source / "upstream.txt").write_text("upstream change\n")
        git("-C", str(source), "add", "upstream.txt")
        git("-C", str(source), "commit", "-m", "local upstream change")
        git("-C", str(source), "push", "origin", "main")
        self.git("remote", "add", "upstream", str(upstream))
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text().replace('slug = "local/smoke"', 'slug = "local/smoke"\nupstream = "upstream"'))
        wrapper = self.bin / "git"
        wrapper.write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            "if sys.argv[-3:] == ['remote', 'get-url', 'upstream']:\n"
            "    print('https://github.com/local/upstream.git'); raise SystemExit(0)\n"
            f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        self.cli("dispatch")
        rows = self.rows()
        merges = [r for r in rows if r["stage"] == "merge" and r["kind"] == "exit"]
        self.assertEqual([(r["ticket"], r["outcome"], r["reason"]) for r in merges],
                         [(None, "merged", "upstream_sync")])
        gates = [r for r in rows if r["stage"] == "gate" and r["kind"] == "enter"]
        self.assertEqual(gates[0]["parent_execution_id"], merges[0]["execution_id"])
        self.assertEqual(self.git("show", "origin/main:upstream.txt").stdout, "upstream change\n")

    def test_worker_launch_failure_releases_ticket_lock_and_skips_downstream(self):
        self.write_config(worker=[str(self.base / "absent-worker")])
        self.cli("dispatch", "--ticket", "7", expected=1)
        rows = self.rows()
        self.assertFalse(any(r["stage"] in {"gate", "review"} for r in rows))
        worker = [r for r in rows if r["stage"] == "worker" and r["kind"] == "exit"]
        self.assertEqual(worker[0]["outcome"], "mechanism_failure")
        from factory.dispatch import lock_held
        self.assertFalse(lock_held(self.repo / ".factory" / "locks" / "7.lock"))

    def test_setup_exception_records_unknown_and_releases_claim_lock(self):
        self.git("remote", "set-url", "origin", str(self.base / "absent-origin"))
        self.cli("dispatch", "--ticket", "7", expected=1)
        rows = self.rows()
        self.assertFalse(any(r["stage"] in {"worker", "gate", "review"} for r in rows))
        terminal = [r for r in rows if r["stage"] == "ticket" and r["kind"] == "exit"]
        self.assertEqual(terminal[0]["outcome"], "unknown")
        from factory.dispatch import lock_held
        self.assertFalse(lock_held(self.repo / ".factory" / "locks" / "7.lock"))


if __name__ == "__main__":
    unittest.main()
