"""#520: configured mechanical commands repair gates without a human handoff."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage, results
from tests import test_dispatch_lifecycle as support


class GateFixupsTest(unittest.TestCase):
    def setUp(self):
        self.f = support.LocalCLI()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.state.write_text(json.dumps({"reviews": 1}))
        self.count = self.f.base / "fixup-runs"
        gh = self.f.bin / "gh"
        gh.write_text(gh.read_text().replace(
            'elif a[0] == "api"',
            'elif a[:2] == ["issue", "comment"]:\n'
            '    s.setdefault("comments", []).append(a[a.index("--body") + 1])\n'
            '    p.write_text(json.dumps(s))\n'
            'elif a[0] == "api"',
        ))
        self.checks = self.f.base / "gate-heads"

    def configure(self, script=None, *, pr=False, attempts=1):
        check = (
            "import subprocess\nfrom pathlib import Path\n"
            f"p = Path({str(self.checks)!r})\n"
            "head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()\n"
            "p.write_text((p.read_text() if p.exists() else '') + head + '\\n')\n"
            "raise SystemExit(0 if Path('fixed.txt').exists() else 1)\n"
        )
        self.f.write_config(
            check=[sys.executable, "-c", "pass"] if pr else [sys.executable, "-c", check],
            extra=("[[gate.check]]\nname = 'mechanical'\nwhen = 'pr'\nrun = "
                   + json.dumps([sys.executable, "-c", check]) + "\n") if pr else "",
        )
        path = self.f.repo / ".factory.toml"
        text = path.read_text().replace("max_attempts = 2", f"max_attempts = {attempts}")
        if script is not None:
            command = [sys.executable, "-c", (
                "from pathlib import Path\n"
                f"p = Path({str(self.count)!r})\n"
                "p.write_text((p.read_text() if p.exists() else '') + 'ran\\n')\n" + script
            )]
            text = text.replace("[gate]\n", "[gate]\nfixups = " + json.dumps([command]) + "\n")
        path.write_text(text)
        self.f.commit_config()

    def events(self, name):
        return [row for row in lifecycle.read_events(self.f.events) if row.get("event") == name]

    def test_configured_fixup_commits_and_regates_before_review(self):
        self.configure("Path('fixed.txt').write_text('mechanical fix\\n')\n")
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.events("escalate"), [])
        [approved] = self.events("approved")
        head = approved["head"]
        self.assertEqual(self.f.git("show", f"{head}:fixed.txt").stdout, "mechanical fix\n")
        self.assertEqual(self.count.read_text(), "ran\n")
        self.assertEqual(self.checks.read_text().splitlines()[-1], head)
        [attempt] = self.events("attempt")
        self.assertEqual((attempt["head"], attempt["actual_head"], attempt["gate"]), (head, head, "PASS"))
        self.assertEqual(self.f.git("log", "-1", "--format=%P", head).stdout.split(),
                         [self.checks.read_text().splitlines()[0]])

    def test_no_configuration_preserves_escalation(self):
        self.configure()
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual([row["code"] for row in self.events("escalate")], ["gate_failed"])
        self.assertFalse(self.count.exists())
        self.assertFalse(self.events("approved"))
        self.assertEqual(len(self.checks.read_text().splitlines()), 1)

    def assert_failed_fixup(self, script, expected):
        self.configure(script)
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual([row["code"] for row in self.events("escalate")], ["gate_failed"])
        self.assertFalse(self.events("approved"))
        self.assertEqual(self.count.read_text(), "ran\n")
        packet = (self.f.repo / ".factory/escalations/7.md").read_text()
        self.assertIn("Mechanical fixups", packet)
        self.assertIn(expected, packet)
        self.assertIn("Mechanical fixups", "\n".join(json.loads(self.f.state.read_text())["comments"]))
        self.assertEqual(len(self.checks.read_text().splitlines()), 1)

    def test_noop_fixup_escalates_and_comments_that_it_ran(self):
        self.assert_failed_fixup("pass\n", "no changes")

    def test_failed_fixup_escalates_and_comments_that_it_ran(self):
        self.assert_failed_fixup("raise SystemExit(9)\n", "exit 9")

    def test_missing_fixup_executable_escalates_with_attempt_evidence(self):
        self.configure()
        path = self.f.repo / ".factory.toml"
        path.write_text(path.read_text().replace("[gate]\n", '[gate]\nfixups = [["/nonexistent/factory-fixup"]]\n'))
        self.f.commit_config()
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual([row["code"] for row in self.events("escalate")], ["gate_failed"])
        self.assertIn("Mechanical fixups", (self.f.repo / ".factory/escalations/7.md").read_text())

    def test_unsuccessful_fixup_does_not_loop(self):
        self.configure("Path('unrelated.txt').write_text('not a fix')\n")
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.count.read_text(), "ran\n")
        self.assertEqual(len(self.checks.read_text().splitlines()), 2)
        self.assertEqual([row["code"] for row in self.events("escalate")], ["gate_failed"])

    def test_pr_gate_fixup_rechecks_attempt_gate_and_reviews_new_head(self):
        self.configure("Path('fixed.txt').write_text('pr fix\\n')\n", pr=True)
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.events("escalate"), [])
        [approved] = self.events("approved")
        [review] = self.events("review")
        self.assertEqual(review["head"], approved["head"])
        self.assertEqual(self.count.read_text(), "ran\n")
        self.assertEqual(self.f.git("rev-parse", "origin/agent/7").stdout.strip(), approved["head"])
        self.assertEqual(self.f.git("show", f"{approved['head']}:fixed.txt").stdout, "pr fix\n")

    def test_manager_fix_uses_same_mechanical_recovery(self):
        self.configure("Path('fixed.txt').write_text('manager fix\\n')\n")
        wt = self.f.repo / ".factory/wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.f.git("worktree", "add", str(wt), "-b", "agent/7")
        head = self.f.git("rev-parse", "HEAD").stdout.strip()
        packet = wt.parent / "packet.md"
        packet.write_text("repair the gate")
        issue = {"title": "local change", "body": "", "comments": [], "labels": []}
        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": head,
              "baseRefName": "main", "reviewDecision": ""}
        with mock.patch.dict(os.environ, self.f.env), \
                mock.patch.object(dispatch, "gh_json", side_effect=lambda a: pr if a[:2] == ["pr", "view"] else issue), \
                mock.patch.object(dispatch, "push_agent", return_value=head), \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch.configure(config.load(self.f.repo))
            manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)
        escalate.assert_not_called()
        approve.assert_called_once()
        approved_head = approve.call_args.args[1]
        self.assertEqual(self.f.git("show", f"{approved_head}:fixed.txt").stdout, "manager fix\n")
        self.assertEqual(self.count.read_text(), "ran\n")

    def test_fixup_configuration_rejects_malformed_argv(self):
        self.configure()
        path = self.f.repo / ".factory.toml"
        original = path.read_text()
        for value in ('"cargo xtask manifest"', '[[]]', '[["cargo", 7]]', '[[""]]'):
            with self.subTest(value=value):
                path.write_text(original.replace("[gate]\n", f"[gate]\nfixups = {value}\n"))
                with mock.patch.dict(os.environ, self.f.env), self.assertRaises(config.ConfigError):
                    config.load(self.f.repo)

    def test_failed_fixup_runs_once_after_worker_attempts_are_exhausted(self):
        self.configure("pass\n", attempts=2)
        self.f.cli("dispatch", "--ticket", "7")
        self.assertEqual(self.count.read_text(), "ran\n")
        self.assertEqual([row["gate"] for row in self.events("attempt")], ["FAIL", "FAIL"])
        self.assertEqual([row["code"] for row in self.events("escalate")], ["gate_failed"])

    def test_refresh_fixup_commits_and_regates_before_new_head_review(self):
        self.configure("Path('fixed.txt').write_text('refresh fix\\n')\n")
        wt = self.f.repo / ".factory/wt-7"
        wt.parent.mkdir(exist_ok=True)
        self.f.git("worktree", "add", str(wt), "-b", "agent/7")
        (wt / "change.txt").write_text("branch change\n")
        self.f.git("-C", str(wt), "add", "change.txt")
        self.f.git("-C", str(wt), "commit", "-qm", "branch work")
        self.f.git("-C", str(wt), "push", "origin", "agent/7")
        (self.f.repo / "main-change.txt").write_text("main moved\n")
        self.f.git("add", "main-change.txt")
        self.f.git("commit", "-qm", "main moves")
        self.f.git("push", "origin", "main")
        issue = {"state": "OPEN", "labels": [], "body": "", "comments": []}
        with mock.patch.dict(os.environ, self.f.env), \
                mock.patch.object(dispatch, "gh_json", side_effect=lambda a: {"baseRefName": "main"} if a[:2] == ["pr", "view"] else issue), \
                mock.patch.object(dispatch, "review", return_value=("APPROVE", "approved")) as review, \
                mock.patch.object(dispatch, "pr_comment"), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve:
            dispatch.configure(config.load(self.f.repo))
            self.assertTrue(dispatch.refresh_pr_branch(7, 70, False))
        head = self.f.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(approve.call_args.args, (7, head))
        self.assertEqual(review.call_args.args[3], head)
        self.assertEqual(self.f.git("show", f"{head}:fixed.txt").stdout, "refresh fix\n")
        self.assertEqual(self.count.read_text(), "ran\n")
        self.assertEqual(self.checks.read_text().splitlines()[-1], head)

    def test_pr_fixup_cannot_bypass_new_attempt_gate_failure(self):
        self.configure("Path('fixed.txt').write_text('pr fix\\n')\n", pr=True)
        path = self.f.repo / ".factory.toml"
        guard = [sys.executable, "-c", "from pathlib import Path; raise SystemExit(Path('fixed.txt').exists())"]
        path.write_text(path.read_text().replace(
            json.dumps([sys.executable, "-c", "pass"]), json.dumps(guard),
        ).replace("review_rounds = 1", "review_rounds = 0"))
        self.f.commit_config()
        self.f.cli("dispatch", "--ticket", "7")
        self.assertFalse(self.events("approved"))
        self.assertFalse(self.events("review"))
        self.assertEqual([row["code"] for row in self.events("escalate")], ["pr_checks_failed"])
        self.assertEqual(self.count.read_text(), "ran\n")

    def finished_worker(self):
        self.f.worker.write_text(
            self.f.worker.read_text()
            + "import subprocess\n"
            + "subprocess.run(['git', 'add', 'change.txt'], check=True)\n"
            + "subprocess.run(['git', 'commit', '-qm', 'worker finished'], check=True)\n"
            + "Path('.factory').mkdir(exist_ok=True)\n"
            + "Path('.factory/handoff-7.md').write_text('## Changed\\nDone.\\n"
            + "## Still unverified\\nNone.\\n## Next\\nNothing.\\n')\n"
        )

    def test_pr_repaired_head_retains_handoff_after_source_removal(self):
        self.configure("Path('fixed.txt').write_text('pr fix\\n')\n", pr=True)
        self.finished_worker()
        self.f.cli("dispatch", "--ticket", "7")
        [approved] = self.events("approved")
        head = approved["head"]
        source = self.f.repo / ".factory/wt-7/.factory/handoff-7.md"
        content = source.read_text()
        cfg = config.Config(self.f.repo, "local/smoke")
        retained = results.retain(cfg, 7, head, lifecycle.read_events(self.f.events))
        self.assertTrue(retained["cleanup_safe"], retained)
        source.unlink()
        page = results.read_result(cfg, 7, head)
        self.assertEqual((page["status"], page["text"]), ("complete", content))
        self.assertTrue(results.retain(cfg, 7, head, lifecycle.read_events(self.f.events))["cleanup_safe"])

    def pr_repair_after_deadline(self, *, finished):
        self.configure("Path('fixed.txt').write_text('pr fix\\n')\n", pr=True)
        if finished:
            self.finished_worker()
        self.f.state.write_text("{}")
        clock = [0]
        real_review = dispatch.review

        def review(*args, **kwargs):
            verdict = real_review(*args, **kwargs)
            clock[0] = 10_000
            return verdict

        issue = {"number": 7, "title": "local change", "body": "Acceptance: make the local change",
                 "comments": [], "labels": [{"name": "ready-for-agent"}]}
        with mock.patch.dict(os.environ, self.f.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "review", side_effect=review):
            dispatch.configure(config.load(self.f.repo))
            dispatch.process_ticket(issue, 1, False, forced=True)
        [attempt] = self.events("attempt")
        [repair] = self.events("gate-fixup")
        self.assertEqual(attempt["finished"], finished)
        self.assertEqual(repair["previous_head"], attempt["head"])
        self.assertNotEqual(repair["head"], attempt["head"])
        return repair["head"]

    def test_pr_repaired_finished_worker_defers_actionable_rework_after_deadline(self):
        head = self.pr_repair_after_deadline(finished=True)
        self.assertEqual(self.events("escalate"), [])
        [rework] = self.events("review-rework")
        self.assertEqual((rework["head"], rework["deferred"], rework["round"]), (head, True, 1))
        self.assertIn("First review requires revision.", rework["findings"])

    def test_pr_repair_does_not_mark_unfinished_worker_finished(self):
        self.pr_repair_after_deadline(finished=False)
        self.assertEqual(self.events("review-rework"), [])
        self.assertEqual([row["code"] for row in self.events("escalate")], ["budget"])



if __name__ == "__main__":
    unittest.main()
