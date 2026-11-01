"""Exercise real CLI boundaries; all GitHub/model/worker commands are local stand-ins."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import __version__, lifecycle

GH = '''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
p = Path(os.environ["SMOKE_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
issue = {"number": 7, "title": "local change", "body": "Acceptance: make the local change", "comments": [], "labels": [{"name": "ready-for-agent"}], "assignees": [], "state": "OPEN"}
if s.get("sync"):  # the dispatcher filed a sync ticket; the only open issue until its PR merges
    issue = {**issue, "title": "upstream sync: 1 commit(s) at local", "body": s["sync"], "state": "CLOSED" if s.get("merged") else "OPEN"}
if a[:2] == ["issue", "view"]:
    print(json.dumps(issue))
elif a[:2] == ["issue", "create"]:
    s["sync"] = Path(a[a.index("--body-file") + 1]).read_text()
    p.write_text(json.dumps(s))
    print("https://example.invalid/issues/7")
elif a[:2] == ["issue", "list"]:
    print(json.dumps([issue]) if s.get("sync") and not s.get("merged") else "[]")
elif a[:2] == ["pr", "list"]:
    if s.get("pr") and not s.get("merged"):
        head = subprocess.run(["git", "-C", ".factory/wt-7", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        print(json.dumps([{"number": 70, "state": "OPEN", "headRefName": "agent/7", "headRefOid": head, "baseRefName": "main", "isDraft": False, "labels": [{"name": "factory-approved"}], "reviewRequests": [], "reviewDecision": "APPROVED"}]))
    else:
        print("[]")
elif a[:2] == ["pr", "create"]:
    s["pr"] = True
    p.write_text(json.dumps(s))
    print("https://example.invalid/pull/70")
elif a[:2] == ["pr", "view"]:
    head = subprocess.run(["git", "-C", ".factory/wt-7", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    print(json.dumps({"number": 70, "headRefName": "agent/7", "headRefOid": head, "baseRefName": "main", "state": "OPEN", "reviewDecision": "", "isDraft": False, "labels": [{"name": "factory-approved"}], "title": "local change"}))
elif a[:2] == ["pr", "checks"]:
    print(json.dumps([{"name": "ci", "bucket": "pass" if s.get("merge_ready") else "pending"}]))
elif a[:2] == ["pr", "merge"]:
    s["merged"] = a
    p.write_text(json.dumps(s))
elif a[0] == "api" and "/compare/" in a[1]:
    print(json.dumps({"behind_by": 0}))
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
verdict = "REVISE" if s["reviews"] == 1 else "APPROVE"
findings = [{"severity": "important", "pass": "bug", "path": "change.txt", "line": 1,
             "summary": "First review requires revision."}] if verdict == "REVISE" else []
print("```json\\n" + json.dumps({"verdict": verdict, "findings": findings})
      + "\\n```\\nVERDICT: " + verdict)
'''

# Records the head it ran on in argv[1]; FAILs its first argv[2] runs.
PR_CHECK = '''import subprocess, sys
from pathlib import Path
log = Path(sys.argv[1])
runs = log.read_text().split() if log.exists() else []
head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
log.write_text("".join(h + "\\n" for h in [*runs, head]))
print("pr-check-output")
raise SystemExit(0 if len(runs) >= int(sys.argv[2]) else 1)
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

    def write_config(self, check=None, worker=None, reviewer=None, extra=""):
        argv = check or [sys.executable, "-c", "raise SystemExit(0)"]
        (self.repo / ".factory.toml").write_text(
            '[repo]\nslug = "local/smoke"\n'
            '[dispatch]\nmax_attempts = 2\nreview_rounds = 1\n'
            '[workers]\ndefault = ' + json.dumps(worker or [sys.executable, str(self.worker)]) + '\n'
            '[review]\ncommand = ' + json.dumps(reviewer or [sys.executable, str(self.reviewer)]) + '\n'
            '[leak_scan]\npattern = ""\n'
            '[gate]\nlock = ' + json.dumps(str(self.base / "host.lock")) + '\n'
            '[[gate.check]]\nname = "code"\nrun = ' + json.dumps(argv) + '\n' + extra
        )

    def commit_config(self):
        """Land the fixture's `.factory.toml` on origin/main: the dispatcher reads the pinned tip, not local edits."""
        self.git("add", ".factory.toml")
        self.git("commit", "-qm", "config")
        self.git("push", "-q", "origin", "main")

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-m", "factory", *args], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def rows(self):
        return [r for r in lifecycle.read_events(self.events) if r.get("event") == "lifecycle"]

    def test_run_gate_uses_installed_runner_and_discards_stale_report(self):
        import shutil
        import venv
        from unittest import mock

        from factory import config, dispatch

        check_cwd = self.base / "check-cwd"
        check = self.base / "check.py"
        check.write_text(
            "from pathlib import Path\n"
            f"Path({str(check_cwd)!r}).write_text(str(Path.cwd()))\n"
        )
        self.write_config(check=[sys.executable, str(check)])

        stub = self.repo / "factory"
        stub.mkdir()
        (stub / "__init__.py").write_text("")
        (stub / "__main__.py").write_text(
            "import sys\n"
            "print('worktree factory rejected --bug', file=sys.stderr)\n"
            "raise SystemExit(64)\n"
        )
        stale = self.repo / ".factory" / "gate-report-7.md"
        stale.parent.mkdir(exist_ok=True)
        stale.write_text("STALE REPORT\n")

        runner = self.base / "runner"
        venv.EnvBuilder(with_pip=False).create(runner)
        runner_python = runner / "bin" / "python"
        purelib = subprocess.run(
            [
                str(runner_python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        shutil.copytree(Path(dispatch.__file__).parent, Path(purelib) / "factory")

        dispatch.configure(config.load(self.repo))
        with mock.patch.object(dispatch.sys, "executable", str(runner_python)), \
                mock.patch.dict(os.environ, {"PYTHONPATH": str(self.repo)}):
            ok, report = dispatch.run_gate(
                self.repo, 7, bug=True, no_repro_test="fixture exemption",
            )

        self.assertTrue(ok, report)
        self.assertIn("- code: PASS", report)
        self.assertNotIn("STALE REPORT", report)
        self.assertEqual(check_cwd.read_text(), str(self.repo))

        failed_runner = self.base / "failed-runner"
        failed_runner.write_text(
            "#!/bin/sh\n"
            "echo 'isolated runner failed' >&2\n"
            "exit 64\n"
        )
        failed_runner.chmod(0o755)
        stale.write_text("STALE REPORT\n")
        with mock.patch.object(dispatch.sys, "executable", str(failed_runner)):
            ok, report = dispatch.run_gate(self.repo, 7)
        self.assertFalse(ok)
        self.assertNotIn("STALE REPORT", report)
        self.assertIn("isolated runner failed", report)

        failed_runner.write_text("#!/bin/sh\nexit 0\n")
        with mock.patch.object(dispatch.sys, "executable", str(failed_runner)):
            ok, report = dispatch.run_gate(self.repo, 7)
        self.assertFalse(ok)
        self.assertIn("did not write .factory/gate-report-7.md", report)

    def test_host_worker_wrap_launches_real_worker(self):
        # Launch-contract proof only: a recording wrapper, NOT sandbox proof.
        log = self.base / "wrap.log"
        wrap = self.bin / "wrap"
        wrap.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$WRAP_LOG"\nexec "$@"\n')
        wrap.chmod(0o755)
        self.env["WRAP_LOG"] = str(log)
        seen = self.base / "prompt-seen"
        self.worker.write_text(
            'import subprocess\nfrom pathlib import Path\n'
            f'Path({str(seen)!r}).write_text(Path(".factory-prompt.md").read_text())\n'
            'Path("change.txt").write_text("change\\n")\n'
            'subprocess.run(["git", "add", "change.txt"], check=True)\n'
            'subprocess.run(["git", "commit", "-qm", "wrapped"], check=True)\n'
        )
        host = self.base / "config" / "factory" / "config.toml"
        host.parent.mkdir(parents=True)
        host.write_text('[defaults.worker_wrap]\ncommand = ["wrap", "{cwd}"]\n'
                        '[repo."local/smoke".worker_wrap]\ncommand = ["wrap"]\n')
        self.cli("dispatch", "--ticket", "7")
        lines = log.read_text().splitlines()
        self.assertEqual(lines[:2], [sys.executable, str(self.worker)])
        self.assertIn("Issue #7", seen.read_text())
        wt = self.repo / ".factory" / "wt-7"
        subjects = subprocess.run(["git", "-C", str(wt), "log", "--format=%s"], env=self.env,
                                  check=True, capture_output=True, text=True).stdout
        self.assertIn("wrapped", subjects)

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
        evidence = lifecycle.read_events(self.events)
        approval = next(r for r in evidence if r.get("event") == "approved")
        self.assertEqual(
            (approval["head"], approval["gate_head"], approval["review_head"]),
            (approval["head"],) * 3,
        )
        self.assertTrue(any(
            r.get("event") == "attempt"
            and r.get("gate") == "PASS"
            and r.get("head") == approval["head"] == r.get("actual_head")
            for r in evidence
        ))
        self.assertTrue(any(
            r.get("event") == "review"
            and r.get("accepted") is True
            and r.get("head") == approval["head"] == r.get("actual_head")
            for r in evidence
        ))
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

    def retained_page(self, head, offset=0, expected=0):
        request = {"schema_version": 1, "repository": "local/smoke", "op": "investigate",
                   "kind": "result", "number": 7, "head": head, "offset": offset}
        result = subprocess.run(
            [sys.executable, "-m", "factory", "evidence", "--root", str(self.repo)],
            input=json.dumps(request), cwd=self.repo, env=self.env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def merge_accepted(self):
        state = json.loads(self.state.read_text())
        state["merge_ready"] = True
        self.state.write_text(json.dumps(state))
        self.cli("dispatch")
        self.assertTrue(json.loads(self.state.read_text())["merged"])
        self.assertFalse((self.repo / ".factory" / "wt-7").exists())

    def test_retained_handoff_survives_cleanup(self):
        text = "Accepted handoff\n" + "λ" * 16_000 + "\nComplete final paragraph.\n"
        self.worker.write_text(WORKER + "\nPath('.factory/handoff-7.md').write_text(" + repr(text) + ", encoding='utf-8')\n")
        self.cli("dispatch", "--ticket", "7")
        approval = next(row for row in lifecycle.read_events(self.events) if row.get("event") == "approved")
        head = approval["head"]
        self.merge_accepted()
        parts, offset = [], 0
        while offset is not None:
            page = self.retained_page(head.upper(), offset)
            detail = page["investigation"]
            self.assertEqual(detail["status"], "complete")
            self.assertEqual(detail["manifest"]["accepted_head"], head)
            self.assertEqual(detail["manifest"]["source"]["head"], head)
            source = next(source for source in page["sources"] if source["id"] == detail["handoff_source_id"])
            self.assertLessEqual(len(source["text"].encode("utf-8")), 20_000)
            parts.append(source["text"])
            following = detail["next_offset"]
            if following is not None:
                self.assertGreater(following, offset)
                self.assertTrue(source["truncated"])
            offset = following
        self.assertEqual("".join(parts), text)

    def test_merge_keeps_the_only_copy_of_an_oversized_handoff(self):
        self.worker.write_text(WORKER + "\nPath('.factory/handoff-7.md').write_bytes(b'x' * (256 * 1024 + 1))\n")
        self.cli("dispatch", "--ticket", "7")
        state = json.loads(self.state.read_text())
        state["merge_ready"] = True
        self.state.write_text(json.dumps(state))
        self.cli("dispatch")
        self.assertTrue(json.loads(self.state.read_text())["merged"])
        source = self.repo / ".factory/wt-7/.factory/handoff-7.md"
        self.assertEqual(source.read_bytes(), b"x" * (256 * 1024 + 1))

    def test_missing_handoff_remains_explicit_after_cleanup(self):
        self.cli("dispatch", "--ticket", "7")
        approval = next(row for row in lifecycle.read_events(self.events) if row.get("event") == "approved")
        self.merge_accepted()
        page = self.retained_page(approval["head"], expected=1)
        self.assertEqual(page["investigation"]["manifest"]["source"]["status"], "missing")
        self.assertNotEqual(page["investigation"]["status"], "complete")
        self.assertIsNone(page["investigation"]["next_offset"])

    def test_ticketless_invocations_and_side_effect_free_dry_run(self):
        self.cli("dispatch", "--dry-run")
        self.cli("dispatch", "--ticket", "7", "--dry-run")
        self.cli("triage", "--dry-run")
        self.assertFalse((self.repo / ".factory").exists())
        self.cli("dispatch")
        self.cli("triage")
        # Nothing to claim, triage or merge: the idle invocations journal no lifecycle trail (#173).
        self.assertEqual(self.rows(), [])

    def test_dispatcher_pass_enter_row_records_engine(self):
        sha = "0123456789abcdef0123456789abcdef01234567"
        host = self.base / "config" / "factory" / "config.toml"
        host.parent.mkdir(parents=True)
        host.write_text(f'[defaults.engine]\nsha = "{sha}"\n')
        self.cli("dispatch", "--ticket", "7")
        enter = next(r for r in self.rows() if r["stage"] == "dispatcher" and r["kind"] == "enter")
        self.assertEqual((enter["engine_commit"], enter["engine_version"]), (sha, __version__))

    def test_code_failure_missing_mechanism_and_skipped_check(self):
        self.write_config(check=[sys.executable, "-c", "raise SystemExit(1)"])
        self.cli("gate", expected=1)
        failures = [r for r in self.rows() if r["kind"] == "exit" and r["stage"] == "gate"]
        self.assertEqual(failures[-1]["outcome"], "product_feedback")
        self.write_config(check=[str(self.base / "absent-executable")])
        result = subprocess.run([sys.executable, "-m", "factory", "gate"], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=60, check=False)
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

    def upstream_fixture(self, check=None, worker=None):
        """A local upstream one commit ahead of the fork; returns its tip and the fork's main."""
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
        tip = git("-C", str(source), "rev-parse", "HEAD").stdout.strip()
        self.git("remote", "add", "upstream", str(upstream))
        self.write_config(check=check, worker=worker, reviewer=[sys.executable, "-c", "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')"])
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text().replace('slug = "local/smoke"', 'slug = "local/smoke"\nupstream = "upstream"'))
        self.commit_config()
        wrapper = self.bin / "git"
        wrapper.write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            "if sys.argv[-3:] == ['remote', 'get-url', 'upstream']:\n"
            "    print('https://github.com/local/upstream.git'); raise SystemExit(0)\n"
            f"os.execv({shutil.which('git')!r}, ['git', *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        return tip, self.git("rev-parse", "origin/main").stdout.strip()

    def test_failed_upstream_gate_is_repaired_on_the_candidate_branch_and_merged_as_a_pr(self):
        # The gate fails until a worker adds fix.txt; the detached candidate cannot have it.
        self.worker.write_text(
            'import subprocess\nfrom pathlib import Path\n'
            'Path("fix.txt").write_text("fixed\\n")\n'
            'subprocess.run(["git", "add", "fix.txt"], check=True)\n'
            'subprocess.run(["git", "commit", "-qm", "repair upstream gate"], check=True)\n'
        )
        tip, fork_main = self.upstream_fixture(
            check=[sys.executable, "-c", "import sys, pathlib; sys.exit(0 if pathlib.Path('fix.txt').exists() else 1)"],
        )
        self.cli("dispatch")
        rows = self.rows()
        staged = [r for r in rows if r["stage"] == "upstream-sync" and r["kind"] == "exit"]
        self.assertEqual([(r["ticket"], r["outcome"], r["reason"]) for r in staged], [(None, "completed", "upstream_staged")])
        self.assertFalse(any(r["stage"] == "merge" for r in rows))
        gates = [r for r in rows if r["stage"] == "gate" and r["kind"] == "exit"]
        self.assertEqual([r["outcome"] for r in gates], ["product_feedback", "completed"])
        self.assertEqual(gates[0]["parent_execution_id"], staged[0]["execution_id"])
        events = lifecycle.read_events(self.events)
        attempts = [r for r in events if r.get("event") == "attempt"]
        self.assertEqual([(r["ticket"], r["attempt"], r["gate"]) for r in attempts], [(7, 0, "FAIL"), (7, 1, "PASS")])
        self.assertNotEqual(attempts[0]["head"], attempts[1]["head"])
        state = json.loads(self.state.read_text())
        self.assertIn("gate on it FAILED", state["sync"])
        self.assertIn(tip, state["sync"])
        self.assertTrue(state["pr"])
        approval = next(r for r in events if r.get("event") == "approved")
        wt = self.repo / ".factory" / "wt-7"
        head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], env=self.env, check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(approval["head"], head)
        self.assertEqual(subprocess.run(["git", "-C", str(wt), "branch", "--show-current"], env=self.env, check=True, capture_output=True, text=True).stdout.strip(), "agent/7")
        self.assertEqual(subprocess.run(["git", "-C", str(wt), "merge-base", "--is-ancestor", tip, "HEAD"], env=self.env, check=False).returncode, 0)
        self.git("fetch", "origin", "main")
        self.assertEqual(self.git("rev-parse", "origin/main").stdout.strip(), fork_main)  # no main movement
        self.merge_accepted()
        merged = json.loads(self.state.read_text())["merged"]
        self.assertIn("--merge", merged)  # upstream ancestry kept, not squashed
        self.assertIn(head, merged)
        self.git("fetch", "origin", "main")
        self.assertEqual(self.git("rev-parse", "origin/main").stdout.strip(), fork_main)  # only the merge stage moves main

    def test_clean_upstream_candidate_needs_no_worker_before_its_pr(self):
        self.worker.write_text('from pathlib import Path\nPath("worker-ran").write_text("no")\n')
        _tip, fork_main = self.upstream_fixture()
        self.cli("dispatch")
        rows = self.rows()
        self.assertFalse(any(r["stage"] in {"worker", "merge"} for r in rows))
        self.assertFalse((self.repo / ".factory" / "wt-7" / "worker-ran").exists())
        events = lifecycle.read_events(self.events)
        attempts = [(r["ticket"], r["attempt"], r["gate"]) for r in events if r.get("event") == "attempt"]
        self.assertEqual(attempts, [(7, 0, "PASS")])
        approval = next(r for r in events if r.get("event") == "approved")
        attempt = next(r for r in events if r.get("event") == "attempt")
        self.assertEqual(approval["head"], attempt["head"])
        self.assertTrue(json.loads(self.state.read_text())["pr"])
        self.git("fetch", "origin", "main")
        self.assertEqual(self.git("rev-parse", "origin/main").stdout.strip(), fork_main)
        self.merge_accepted()
        self.assertIn("--merge", json.loads(self.state.read_text())["merged"])

    def test_worker_launch_failure_releases_ticket_lock_and_skips_downstream(self):
        self.write_config(worker=[str(self.base / "absent-worker")])
        self.commit_config()
        self.cli("dispatch", "--ticket", "7", expected=1)
        rows = self.rows()
        self.assertFalse(any(r["stage"] in {"gate", "review"} for r in rows))
        worker = [r for r in rows if r["stage"] == "worker" and r["kind"] == "exit"]
        self.assertEqual(worker[0]["outcome"], "mechanism_failure")
        from factory.dispatch import lock_held
        self.assertFalse(lock_held(self.repo / ".factory" / "locks" / "7.lock"))

    def test_setup_exception_records_unknown_and_releases_claim_lock(self):
        (self.repo / ".factory").mkdir()
        (self.repo / ".factory" / "wt-7").write_text("")  # `git worktree add` refuses an existing path
        self.cli("dispatch", "--ticket", "7", expected=1)
        rows = self.rows()
        self.assertFalse(any(r["stage"] in {"worker", "gate", "review"} for r in rows))
        terminal = [r for r in rows if r["stage"] == "ticket" and r["kind"] == "exit"]
        self.assertEqual(terminal[0]["outcome"], "unknown")
        from factory.dispatch import lock_held
        self.assertFalse(lock_held(self.repo / ".factory" / "locks" / "7.lock"))

    def test_worker_launch_failure_unclaims_ticket_and_completes_the_pass(self):
        # #260: a worker executable missing from the host PATH is a host defect, not a
        # ticket defect. The ticket journals it and gives its claim back; the dispatcher
        # pass itself must not die with that FileNotFoundError.
        self.write_config(worker=[str(self.base / "absent-worker")])
        self.commit_config()
        result = self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("#7: worker launch failed", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        rows = self.rows()
        exits = {r["stage"]: r["outcome"] for r in rows if r["kind"] == "exit"}
        self.assertEqual(exits["worker"], "mechanism_failure")
        self.assertEqual(exits["ticket"], "mechanism_failure")
        self.assertEqual(exits["dispatcher"], "completed")

    def test_worker_launch_failure_hands_back_the_claim(self):
        # #260: the unclaim is what lets the frontier retry once the host is fixed.
        calls = self.base / "gh-calls.log"
        real = self.bin / "gh-real"
        real.write_text(GH)
        real.chmod(0o755)
        (self.bin / "gh").write_text(
            "#!/usr/bin/env python3\nimport os, sys\n"
            f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"os.execv({str(real)!r}, ['gh', *sys.argv[1:]])\n"
        )
        self.write_config(worker=[str(self.base / "absent-worker")])
        self.commit_config()
        self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("issue edit 7 --repo local/smoke --remove-assignee @me", calls.read_text().splitlines())

    def test_worker_launch_failure_on_review_bounce_unclaims_and_completes_the_pass(self):
        # #260: the worker that vanishes between the first attempt and the review bounce
        # must take the same path as one missing from the start.
        vanish = self.bin / "vanish"
        vanish.write_text('#!/bin/sh\nrm "$0"\necho change >> change.txt\n')
        vanish.chmod(0o755)
        self.write_config(worker=[str(vanish)])
        self.commit_config()
        result = self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("#7: worker launch failed", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        rows = self.rows()
        reviews = [r for r in rows if r["stage"] == "review" and r["kind"] == "exit"]
        self.assertEqual([r["outcome"] for r in reviews], ["product_feedback"])
        workers = [r["outcome"] for r in rows if r["stage"] == "worker" and r["kind"] == "exit"]
        self.assertEqual(workers, ["completed", "mechanism_failure"])
        exits = {r["stage"]: r["outcome"] for r in rows if r["kind"] == "exit"}
        self.assertEqual(exits["ticket"], "mechanism_failure")
        self.assertEqual(exits["dispatcher"], "completed")

    def test_reviewer_launch_failure_is_rejected_evidence_not_a_crash(self):
        # #260: a reviewer executable missing from the host PATH is unaccepted evidence
        # (REVISE, reason FileNotFoundError); the ticket escalates and the pass completes.
        self.write_config(reviewer=[str(self.base / "absent-reviewer")])
        self.commit_config()
        result = self.cli("dispatch", "--ticket", "7")
        self.assertIn("#7: reviewer launch failed", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        rows = self.rows()
        reviews = [r for r in rows if r["stage"] == "review" and r["kind"] == "exit"]
        self.assertTrue(reviews)
        for row in reviews:
            self.assertEqual(row["outcome"], "unknown")
            self.assertIn("FileNotFoundError", row["reason"])
        exits = {r["stage"]: r["outcome"] for r in rows if r["kind"] == "exit"}
        self.assertEqual(exits["ticket"], "project_escalation")
        self.assertEqual(exits["dispatcher"], "completed")
        self.assertIn("REVISE verdict after 1 review round(s)", result.stdout + result.stderr)

    def assert_reviewer_unavailable(self, script: str, diagnostic: str) -> None:
        runs = self.base / "review-runs"
        reviewer = (
            "from pathlib import Path\n"
            f"p = Path({str(runs)!r})\n"
            "p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n"
            + script
        )
        self.write_config(reviewer=[sys.executable, "-c", reviewer])
        self.commit_config()

        self.cli("dispatch", "--ticket", "7")

        self.assertEqual(runs.read_text(), "xxx")
        events = lifecycle.read_events(self.events)
        self.assertEqual(len([e for e in events if e.get("event") == "attempt"]), 1)
        [review] = [e for e in events if e.get("event") == "review"]
        self.assertEqual((review["accepted"], review["verdict"]), (False, None))
        [escalation] = [e for e in events if e.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "reviewer_unavailable")
        findings = (self.repo / ".factory" / "review-7.md").read_text()
        self.assertIn(diagnostic, findings)
        self.assertIn(diagnostic, Path(escalation["packet"]).read_text())

    def test_reviewer_exit_does_not_consume_a_review_round(self):
        self.assert_reviewer_unavailable("raise SystemExit(1)\n", "review_exit:1")

    def test_unparseable_reviewer_does_not_consume_a_review_round(self):
        self.assert_reviewer_unavailable("print('unparseable retry-after-ms=0')\n", "unparseable")

    def test_manager_fix_review_failure_does_not_escalate_an_already_human_ticket(self):
        from unittest import mock

        from factory import config, dispatch, manage

        self.pr_config(fails=0)
        self.reviewer.write_text(
            "import sys\nprint('rate limited retry-after-ms=0', file=sys.stderr)\nraise SystemExit(1)\n"
        )
        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("fix it")
        issue = {
            "title": "local change", "body": "", "comments": [],
            "labels": [{"name": "ready-for-human"}],
        }
        pr = {
            "state": "OPEN", "headRefName": "agent/7", "headRefOid": head,
            "baseRefName": "main", "reviewDecision": "",
        }
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(
                    dispatch, "gh_json",
                    side_effect=lambda a: pr if a[:2] == ["pr", "view"] else issue,
                ), \
                mock.patch.object(dispatch, "push_agent", return_value=head), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr") as approve, \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch.configure(config.load(self.repo))
            manage.apply(
                7, issue, "FIX", "",
                {"worker": "default", "guidance": "fix"}, packet,
            )

        approve.assert_not_called()
        escalate.assert_not_called()

    def pr_config(self, fails, when="pr"):
        """One attempt check (`code`) plus one pr check (`slow`); prompts of worker and reviewer are kept."""
        self.pr_log = self.base / "pr-runs.log"
        check = self.base / "pr_check.py"
        check.write_text(PR_CHECK)
        self.worker_prompts = self.base / "worker-prompts"
        self.worker.write_text(WORKER + f"\nwith open({str(self.worker_prompts)!r}, 'a') as f:\n"
                               "    f.write(Path('.factory-prompt.md').read_text() + '\\0')\n")
        self.review_prompts = self.base / "review-prompts"
        self.reviewer.write_text(REVIEWER + f"\nimport sys\nwith open({str(self.review_prompts)!r}, 'a') as f:\n"
                                 "    f.write(sys.argv[1] + '\\0')\n")
        self.write_config(
            reviewer=[sys.executable, str(self.reviewer), "{prompt}"],
            extra='[[gate.check]]\nname = "slow"\nrun = '
            + json.dumps([sys.executable, str(check), str(self.pr_log), str(fails)]) + f'\nwhen = "{when}"\n',
        )
        self.commit_config()

    def pr_runs(self):
        return self.pr_log.read_text().split() if self.pr_log.exists() else []

    def prompts(self, path):
        return path.read_text().split("\0")[:-1] if path.exists() else []

    def test_attempt_gate_skips_pr_checks(self):
        self.pr_config(fails=0)
        self.cli("gate")
        report = (self.repo / ".factory" / "gate-report.md").read_text()
        self.assertIn("- code: PASS", report)
        self.assertIn("- slow: SKIP (pr tier)", report)
        self.assertEqual(self.pr_runs(), [])

    def configure_test_edit_authorization(self) -> None:
        manager = self.base / "manager.py"
        manager.write_text(
            "print('DECISION: AUTHORIZE')\n"
            "print('{\"justification\":\"The behavior remains asserted\","
            "\"guidance\":\"Replace the incidental assertion with the new behavior\"}')\n"
        )
        path = self.repo / "tests" / "test_old.py"
        path.parent.mkdir()
        path.write_text("def test_old():\n    assert outcome == 'ci_pending'\n")
        config_path = self.repo / ".factory.toml"
        config_path.write_text(
            config_path.read_text().replace(
                "[gate]\n", '[gate]\nadd_only_paths = ["tests/**"]\n',
            )
            + "\n[manager]\ncommand = "
            + json.dumps([sys.executable, str(manager), "{prompt}"])
            + "\n"
        )
        self.git("add", ".factory.toml", "tests/test_old.py")
        self.git("commit", "-qm", "configure manager test authorization")
        self.git("push", "-q", "origin", "main")

    def test_dispatch_routes_add_only_gate_gap_to_manager(self):
        self.configure_test_edit_authorization()
        self.worker.write_text(
            "from pathlib import Path\n"
            "Path('tests/test_old.py').write_text("
            "\"def test_old():\\n    assert outcome in {'ci_pending', 'ready'}\\n\")\n"
        )

        self.cli("dispatch", "--ticket", "7")

        events = lifecycle.read_events(self.events)
        authorization = next(
            row for row in events if row.get("event") == "test-edit-authorization"
        )
        self.assertEqual(authorization["decision"], "AUTHORIZE")
        self.assertEqual(authorization["files"], ["tests/test_old.py"])
        self.assertFalse(any(row.get("event") == "escalate" for row in events))

    def test_dispatch_routes_worker_test_edit_request_to_manager(self):
        self.configure_test_edit_authorization()
        self.worker.write_text(
            "from pathlib import Path\n"
            "Path('.factory').mkdir(exist_ok=True)\n"
            "Path('.factory/handoff-7.md').write_text("
            "\"Test edit authorization: tests/test_old.py\\n"
            "Replace the incidental wait-state assertion while retaining ci_pending behavior.\\n\")\n"
        )

        self.cli("dispatch", "--ticket", "7")

        events = lifecycle.read_events(self.events)
        authorization = next(
            row for row in events if row.get("event") == "test-edit-authorization"
        )
        self.assertEqual(authorization["files"], ["tests/test_old.py"])
        self.assertFalse(any(row.get("event") == "escalate" for row in events))
        packet = self.repo / ".factory" / "authorizations" / "7.md"
        self.assertIn("retaining ci_pending behavior", packet.read_text())

    def protected_path_escalation(self, *, delete=False, bounce=False):
        path = self.repo / "tests" / "test_old.py"
        path.parent.mkdir()
        path.write_text("def test_old():\n    assert True\n")
        config = self.repo / ".factory.toml"
        config.write_text(config.read_text().replace(
            "[gate]\n", '[gate]\nadd_only_paths = ["tests/**"]\n',
        ))
        self.git("add", ".factory.toml", "tests/test_old.py")
        self.git("commit", "-qm", "protect existing tests")
        self.git("push", "-q", "origin", "main")
        issue_setup = (
            'issue["labels"].append({"name": "bug"})\n'
            'issue["body"] += "\\nNo repro test: escalation guidance fixture"\n'
        )
        if delete:
            issue_setup += 'issue["labels"].append({"name": "factory-add-only-override"})\n'
        (self.bin / "gh").write_text(GH.replace(
            'if s.get("sync"):', issue_setup + 'if s.get("sync"):',
        ))
        change = "p.unlink()" if delete else 'p.write_text("# obsolete test removed\\n")'
        self.worker.write_text(
            "from pathlib import Path\n"
            'p = Path("tests/test_old.py")\n'
            + ("if Path('change.txt').exists():\n    " + change + "\n"
               "else:\n    Path('change.txt').write_text('first attempt\\n')\n"
               if bounce else change + "\n")
        )

        self.cli("dispatch", "--ticket", "7")

        events = lifecycle.read_events(self.events)
        [escalation] = [e for e in events if e.get("event") == "escalate"]
        self.assertEqual(escalation["code"], "protected_paths")
        self.assertEqual(escalation["reason"], "worker removed or changed existing tests")
        self.assertEqual(len([e for e in events if e.get("event") == "review"]), int(bounce))
        packet = Path(escalation["packet"]).read_text()
        self.assertIn("`add-only`", packet)
        if delete:
            self.assertIn("already present", packet)
            self.assertIn("human-only", packet.lower())
            self.assertIn("`factory-protected-override`", packet)
            self.assertIn("cannot", packet)
        else:
            self.assertIn("`factory-add-only-override`", packet)
            self.assertIn("AUTHORIZE", packet)

        wt = self.repo / ".factory" / "wt-7"
        result = subprocess.run(
            [sys.executable, "-m", "factory", "gate", "--bug", "--no-repro-test",
             "escalation guidance fixture", "--add-only-override"],
            cwd=wt, env=self.env, capture_output=True, text=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, int(delete), result.stdout + result.stderr)
        report = (wt / ".factory" / "gate-report.md").read_text()
        if delete:
            self.assertIn("- protected-paths: FAIL (add-only", report)
            self.assertIn("deleted file mode", report)
            result = subprocess.run(
                [sys.executable, "-m", "factory", "gate", "--bug", "--no-repro-test",
                 "escalation guidance fixture", "--protected-override"],
                cwd=wt, env=self.env, capture_output=True, text=True, timeout=60, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotIn(": FAIL", report)

    def test_add_only_text_edit_escalation_names_manager_authority(self):
        self.protected_path_escalation()

    def test_add_only_text_edit_review_bounce_names_manager_authority(self):
        self.protected_path_escalation(bounce=True)

    def test_authorized_file_deletion_escalates_to_human_authority(self):
        self.protected_path_escalation(delete=True)

    def test_authorized_file_deletion_review_bounce_escalates_to_human_authority(self):
        self.protected_path_escalation(delete=True, bounce=True)

    def test_invalid_when_is_a_config_error_naming_the_key(self):
        self.pr_config(fails=0, when="nightly")
        result = self.cli("gate", expected=1)
        self.assertIn("gate.check[1].when", result.stderr)

    def test_pr_checks_run_once_per_reviewed_head_before_review(self):
        self.pr_config(fails=0)
        self.cli("dispatch", "--ticket", "7")
        reviewed = [e["head"] for e in lifecycle.read_events(self.events) if e.get("event") == "review"]
        self.assertEqual(len(set(reviewed)), 2)
        self.assertEqual(self.pr_runs(), reviewed)
        prompts = self.prompts(self.review_prompts)
        self.assertEqual(len(prompts), 2)
        for prompt in prompts:
            self.assertIn("- slow: SKIP (pr tier)", prompt)
            self.assertIn("# Gate report (pr tier)", prompt)
            self.assertIn("- slow: PASS", prompt)
            # Report-only pr checks (e.g. mutation survivors) reach the reviewer only through the report.
            self.assertIn("## slow output\n```\npr-check-output\n```", prompt)

    def test_pr_checks_are_not_rerun_for_an_unchanged_head(self):
        self.pr_config(fails=0)
        # The bounce worker commits nothing, so the re-reviewed head is the one already checked.
        self.worker.write_text(self.worker.read_text().replace(
            'p.write_text(p.read_text() + "change\\n" if p.exists() else "change\\n")',
            'p.exists() or p.write_text("change\\n")'))
        self.cli("dispatch", "--ticket", "7")
        reviewed = [e["head"] for e in lifecycle.read_events(self.events) if e.get("event") == "review"]
        self.assertEqual(len(reviewed), 2)
        self.assertEqual(len(set(reviewed)), 1)
        self.assertEqual(self.pr_runs(), reviewed[:1])
        prompts = self.prompts(self.review_prompts)
        self.assertEqual(len(prompts), 2)
        for prompt in prompts:
            self.assertIn("- slow: PASS", prompt)

    def test_worker_that_commits_nothing_escalates_no_change_not_pr_unpublished(self):
        # #300: a passing worker that finds the ticket already fixed leaves nothing to publish.
        self.worker.write_text("from pathlib import Path\nPath('.factory').mkdir(exist_ok=True)\n"
                               "Path('.factory/handoff-7.md').write_text('already fixed')\n")
        self.cli("dispatch", "--ticket", "7")
        escalations = [e for e in lifecycle.read_events(self.events) if e.get("event") == "escalate"]
        self.assertEqual([e["code"] for e in escalations], ["no_change"])
        self.assertIn("committed nothing", escalations[0]["reason"])
        self.assertFalse(json.loads(self.state.read_text()).get("pr"))

    def test_early_ticket_exit_does_not_poison_later_manager_fix_push(self):
        from unittest import mock

        from factory import config, dispatch, manage

        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        self.git("-C", str(wt), "commit", "--allow-empty", "-qm", "remote work")
        self.git("-C", str(wt), "push", "-q", "origin", "agent/7")
        remote = self.git("rev-parse", "agent/7").stdout.strip()
        self.git("-C", str(wt), "checkout", "-q", "--detach", "main")
        issue = {"number": 7, "title": "local change", "body": "", "labels": []}
        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": remote,
              "baseRefName": "main", "reviewDecision": ""}
        packet = wt.parent / "packet.md"
        packet.write_text("fix it")

        with mock.patch.dict(os.environ, self.env):
            dispatch.configure(config.load(self.repo))
            with mock.patch.object(dispatch, "ensure_worktree", return_value=wt), \
                    mock.patch.object(dispatch, "worker_round", return_value=(
                        False, "- protected-tests: FAIL", None, remote,
                    )):
                dispatch.process_ticket(issue, 1, False, forced=True)

            escalations = [e for e in lifecycle.read_events(self.events) if e.get("event") == "escalate"]
            self.assertEqual([e["code"] for e in escalations], ["protected_paths"])
            self.assertEqual(
                self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], remote,
            )
            self.git("-C", str(wt), "checkout", "-q", "agent/7")

            def worker(*args, **kwargs):
                self.git("-C", str(wt), "reset", "-q", "main")
                self.git("-C", str(wt), "commit", "--allow-empty", "-qm", "manager worker rewrite")
                head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
                return True, "- code: PASS", None, head

            with mock.patch.object(dispatch, "gh_json", return_value=pr), \
                    mock.patch.object(dispatch, "worker_round", side_effect=worker), \
                    mock.patch.object(dispatch, "record_risk"), \
                    mock.patch.object(dispatch, "pr_gate_and_review", return_value=("APPROVE", "", False)), \
                    mock.patch.object(dispatch, "pr_comment"), \
                    mock.patch.object(dispatch, "approve_pr", return_value=True), \
                    mock.patch.object(dispatch, "escalate") as escalate:
                manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)

            head = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(head, remote)
            self.assertEqual(
                self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0], head,
            )
            escalate.assert_not_called()

    def test_worker_that_dies_without_commits_or_handoff_stays_pr_unpublished(self):
        # A crashed worker is a factory failure watch must still count, not a ticket outcome.
        self.worker.write_text("raise SystemExit(1)\n")
        self.cli("dispatch", "--ticket", "7")
        escalations = [e for e in lifecycle.read_events(self.events) if e.get("event") == "escalate"]
        self.assertEqual([e["code"] for e in escalations], ["pr_unpublished"])
        self.assertFalse(json.loads(self.state.read_text()).get("pr"))

    def test_pr_check_failure_bounces_to_worker_then_reviews_new_head(self):
        self.pr_config(fails=1)
        self.state.write_text(json.dumps({"reviews": 1}))  # the one review APPROVEs
        self.cli("dispatch", "--ticket", "7")
        runs = self.pr_runs()
        self.assertEqual(len(set(runs)), 2)
        stages = [r["stage"] for r in self.rows() if r["kind"] == "enter" and r["stage"] in {"worker", "gate", "review"}]
        self.assertEqual(stages, ["worker", "gate", "gate", "worker", "gate", "gate", "review"])
        bounce = self.prompts(self.worker_prompts)[1]
        self.assertIn("PR-tier gate report, round 1 (failed)", bounce)
        self.assertIn("pr-check-output", bounce)
        evidence = lifecycle.read_events(self.events)
        self.assertEqual([e["head"] for e in evidence if e.get("event") == "review"], runs[1:])
        self.assertTrue(any(e.get("event") == "approved" and e["head"] == runs[1] for e in evidence))

    def test_pr_check_failures_exhausting_review_rounds_escalate(self):
        self.pr_config(fails=99)
        self.cli("dispatch", "--ticket", "7")
        self.assertEqual(len(set(self.pr_runs())), 2)
        self.assertEqual(self.prompts(self.review_prompts), [])
        escalations = [e["reason"] for e in lifecycle.read_events(self.events) if e.get("event") == "escalate"]
        self.assertEqual(escalations, ["pr checks failed"])

    def manager_fix(self, fails):
        """`manage.apply` FIX in-process against the fixture repo; GitHub writes are mocked."""
        from unittest import mock

        from factory import config, dispatch, manage

        self.pr_config(fails=fails)
        self.state.write_text(json.dumps({"reviews": 1}))  # the one review APPROVEs
        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        head = self.git("rev-parse", "HEAD").stdout.strip()
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("fix it")
        issue = {"title": "local change", "body": "", "comments": [], "labels": []}
        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": head,
              "baseRefName": "main", "reviewDecision": ""}
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch, "gh_json", side_effect=lambda a: pr if a[:2] == ["pr", "view"] else issue), \
                mock.patch.object(dispatch, "push_agent", return_value=head), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch.configure(config.load(self.repo))
            manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
        return approve, escalate

    def test_manager_fix_runs_pr_checks_before_review(self):
        approve, escalate = self.manager_fix(fails=0)
        reviewed = [e["head"] for e in lifecycle.read_events(self.events) if e.get("event") == "review"]
        self.assertEqual(self.pr_runs(), reviewed)
        self.assertEqual(len(reviewed), 1)
        self.assertIn("- slow: PASS", self.prompts(self.review_prompts)[0])
        approve.assert_called_once()
        escalate.assert_not_called()

    def test_manager_fix_pr_check_failure_escalates(self):
        approve, escalate = self.manager_fix(fails=1)
        self.assertEqual(len(self.pr_runs()), 1)
        self.assertEqual(self.prompts(self.review_prompts), [])
        approve.assert_not_called()
        self.assertEqual(escalate.call_args.args[1], "pr checks failed")

    def test_pass_pins_root_to_origin_main_before_reading_config(self):
        marker = self.base / "new-worker-ran"
        new_worker = self.base / "new_worker.py"
        new_worker.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n{WORKER}")
        good = (self.repo / ".factory.toml").read_text()
        (self.repo / ".factory.toml").write_text(good + "[worker_wrap]\n")  # host-only table: the engine rejects it
        self.commit_config()
        other = self.base / "other"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.base / "origin.git"), str(other)], env=self.env, check=True)
        toml = good.replace(
            json.dumps([sys.executable, str(self.worker)]), json.dumps([sys.executable, str(new_worker)]))
        self.assertNotEqual(toml, (self.repo / ".factory.toml").read_text())
        (other / ".factory.toml").write_text(toml)
        subprocess.run(["git", "-c", "user.name=O", "-c", "user.email=o@example.invalid", "commit", "-qam", "switch worker"],
                       cwd=other, env=self.env, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=other, env=self.env, check=True)
        self.cli("dispatch", "--ticket", "7")
        self.assertTrue(marker.exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, self.git("rev-parse", "origin/main").stdout)
        detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=self.repo, env=self.env,
                                  capture_output=True, text=True, check=False)
        self.assertNotEqual(detached.returncode, 0)
        self.assertNotIn("switch worker", self.git("log", "--format=%s", "main").stdout)

    def test_dirty_root_exits_nonzero_and_claims_nothing(self):
        (self.repo / "junk.txt").write_text("untracked\n")
        with (self.repo / ".factory.toml").open("a") as f:
            f.write("garbage = [\n")  # local edit that does not even parse
        (self.repo / ".factory").mkdir()
        (self.repo / ".factory" / "scratch.txt").write_text("ignored state\n")
        result = self.cli("dispatch", "--ticket", "7", expected=1)
        self.assertIn("root_dirty", result.stdout + result.stderr)
        events = lifecycle.read_events(self.events)
        exits = [r for r in self.rows() if r["kind"] == "exit"]
        self.assertEqual([(r["stage"], r["outcome"], r["reason"]) for r in exits],
                         [("dispatcher", "project_escalation", "root_dirty")])
        [named] = [r for r in self.rows() if r["kind"] == "result"]
        self.assertEqual(named["paths"], [".factory.toml", "junk.txt"])
        self.assertFalse(any(r.get("event") == "claimed" for r in events))
        self.assertFalse((self.repo / ".factory" / "wt-7").exists())
        self.assertEqual(self.git("symbolic-ref", "HEAD").stdout.strip(), "refs/heads/main")


class LeaseRefusedCLI(unittest.TestCase):
    """A refused `agent/7` push (the remote gained a commit the worktree lacks) escalates
    with `lease_refused`, un-assigns the ticket, and keeps the worktree: never a crash."""

    git, cli, write_config = LocalCLI.git, LocalCLI.cli, LocalCLI.write_config

    def setUp(self):
        LocalCLI.setUp(self)
        gh = self.bin / "gh"
        gh.write_text(GH.replace(
            'elif a[:2] not in (["issue", "edit"]',
            'elif a[:2] in (["issue", "edit"], ["issue", "comment"]):\n'
            '    s.setdefault("gh", []).append(a)\n'
            '    p.write_text(json.dumps(s))\n'
            'elif a[:2] not in (["issue", "edit"]'))
        wt = self.repo / ".factory" / "wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.git("worktree", "add", str(wt), "-b", "agent/7")
        self.wt = wt

    def push_foreign(self):
        """Land a commit on origin's `agent/7` that no worktree has, like GitHub "Update branch"."""
        tree = self.git("rev-parse", "HEAD^{tree}").stdout.strip()
        sha = self.git("commit-tree", tree, "-p", "HEAD", "-m", "Update branch").stdout.strip()
        self.git("push", "--force", "origin", f"{sha}:refs/heads/agent/7")
        return sha

    def gh_calls(self, *prefix):
        calls = json.loads(self.state.read_text()).get("gh", [])
        return [c for c in calls if c[:len(prefix)] == list(prefix)]

    def assert_escalated(self, foreign):
        edits = self.gh_calls("issue", "edit")
        self.assertEqual(edits[0][-2:], ["--add-assignee", "@me"])
        self.assertEqual(edits[-1][5:], ["--remove-assignee", "@me", "--remove-label", "ready-for-agent",
                                         "--add-label", "ready-for-human"])
        comments = self.gh_calls("issue", "comment")
        self.assertEqual(len(comments), 1)
        self.assertIn("Factory dispatcher escalating: agent/7 push refused", comments[0][-1])
        escalations = [e for e in lifecycle.read_events(self.events) if e.get("event") == "escalate"]
        self.assertEqual([e["code"] for e in escalations], ["lease_refused"])
        self.assertTrue(self.wt.is_dir())
        subjects = subprocess.run(["git", "-C", str(self.wt), "log", "--format=%s"], env=self.env,
                                  check=True, capture_output=True, text=True).stdout
        self.assertIn("agent/7: local change", subjects)
        remote = self.git("ls-remote", "origin", "refs/heads/agent/7").stdout.split()[0]
        self.assertEqual(remote, foreign)

    def test_refused_first_push_escalates_and_unassigns(self):
        foreign = self.push_foreign()
        self.cli("dispatch", "--ticket", "7")
        self.assert_escalated(foreign)
        self.assertFalse(json.loads(self.state.read_text()).get("pr"))

    def test_refused_review_bounce_push_escalates_and_unassigns(self):
        # The first review REVISEs and moves origin's agent/7 underneath the dispatcher, so the
        # bounce push (leased on the first pushed head) is refused.
        foreign_file = self.base / "foreign"
        self.reviewer.write_text(REVIEWER.replace(
            'print(',
            'if s["reviews"] == 1:\n'
            '    import subprocess\n'
            f'    os.chdir({str(self.wt)!r})\n'
            '    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True).stdout.strip()\n'
            '    sha = subprocess.run(["git", "commit-tree", tree, "-p", "HEAD", "-m", "Update branch"], check=True, capture_output=True, text=True).stdout.strip()\n'
            '    subprocess.run(["git", "push", "--force", "origin", sha + ":refs/heads/agent/7"], check=True, capture_output=True)\n'
            f'    Path({str(foreign_file)!r}).write_text(sha)\n'
            'print('))
        self.cli("dispatch", "--ticket", "7")
        self.assert_escalated(foreign_file.read_text())
        self.assertTrue(json.loads(self.state.read_text()).get("pr"))
        reviews = [e for e in lifecycle.read_events(self.events) if e.get("event") == "review"]
        self.assertEqual(len(reviews), 1)


if __name__ == "__main__":
    unittest.main()


class RetryDecisionTraceTest(unittest.TestCase):
    setUp = LocalCLI.setUp
    git = LocalCLI.git
    write_config = LocalCLI.write_config
    commit_config = LocalCLI.commit_config
    cli = LocalCLI.cli
    pr_config = LocalCLI.pr_config

    def test_pr_gate_failure_retry_cites_gate_instead_of_previous_review(self):
        self.pr_config(fails=0)
        check = self.base / "pr_check.py"
        check.write_text(PR_CHECK.replace(
            'raise SystemExit(0 if len(runs) >= int(sys.argv[2]) else 1)',
            'raise SystemExit(1 if len(runs) == 1 else 0)',
        ))
        config_path = self.repo / ".factory.toml"
        config_path.write_text(config_path.read_text().replace("review_rounds = 1", "review_rounds = 2"))
        self.commit_config()

        self.cli("dispatch", "--ticket", "7")

        decisions = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision"
        ]
        revise = next(row for row in decisions if row.get("stage") == "review"
                      and row.get("chosen") == "REVISE")
        failed_gate = next(row for row in decisions if row.get("stage") == "gate"
                           and row.get("chosen") == "slow")
        retry_sources = [
            row["causes"][0]["decision_id"]
            for row in decisions
            if row.get("stage") == "worker_select"
            and row.get("causes")
            and row["causes"][0].get("relation") == "retries"
        ]
        self.assertEqual(retry_sources, [revise["decision_id"], failed_gate["decision_id"]])


class TriageExemptionLifecycleTest(unittest.TestCase):
    setUp = LocalCLI.setUp
    git = LocalCLI.git
    write_config = LocalCLI.write_config
    commit_config = LocalCLI.commit_config
    cli = LocalCLI.cli

    def configure_bug(self, author="factory-bot"):
        gh = self.bin / "gh"
        gh.write_text(GH.replace(
            'if s.get("sync"):',
            'issue["labels"].append({"name": "bug"})\n'
            'issue["comments"] = [{"author": {"login": ' + repr(author) + '}, '
            '"body": "Triage: investigate hardware failure\\n\\n'
            'Agent brief: fix it\\nNo repro test: requires unavailable hardware"}]\n'
            'if a[:2] == ["api", "user"]:\n'
            '    print("factory-bot")\n'
            '    raise SystemExit(0)\n'
            'if s.get("sync"):',
        ))

    def test_trusted_triage_exemption_survives_retry_and_review_bounce(self):
        self.configure_bug()
        marker = self.base / "first-check"
        check = self.base / "check.py"
        check.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(marker)!r})\n"
            "exists = p.exists()\np.touch()\nraise SystemExit(0 if exists else 1)\n"
        )
        self.write_config(check=[sys.executable, str(check)])
        self.commit_config()

        self.cli("dispatch", "--ticket", "7")

        events = lifecycle.read_events(self.events)
        attempts = [row for row in events if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["FAIL", "PASS", "PASS"])
        gates = [row for row in events if row.get("stage") == "gate" and row.get("event") == "decision"]
        self.assertEqual(len(gates), 3)
        for row in gates:
            summaries = [item["summary"] for item in row["inputs"] if item["kind"] == "repro_test"]
            self.assertEqual(summaries, [
                "bug-repro: SKIP (No repro test: requires unavailable hardware)",
            ])
        self.assertTrue(any(row.get("event") == "approved" for row in events))

    def test_spoofed_triage_comment_cannot_skip_real_bug_gate(self):
        self.configure_bug(author="untrusted")

        self.cli("dispatch", "--ticket", "7")

        events = lifecycle.read_events(self.events)
        attempts = [row for row in events if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["FAIL", "FAIL"])
        report = (self.repo / ".factory" / "wt-7" / ".factory" / "gate-report-7.md").read_text()
        self.assertIn("bug ticket needs a `Repro test:` handoff line", report)
        self.assertNotIn("- bug-repro: SKIP", report)
        self.assertFalse(any(row.get("event") == "approved" for row in events))
