"""Unchanged worker revisions do not buy another gate or review."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest import mock

from factory import dispatch, lifecycle
from tests import test_dispatch_lifecycle as cli_tests


class UnchangedRoundsTest(unittest.TestCase):
    setUp = cli_tests.LocalCLI.setUp
    git = cli_tests.LocalCLI.git
    write_config = cli_tests.LocalCLI.write_config
    commit_config = cli_tests.LocalCLI.commit_config
    cli = cli_tests.LocalCLI.cli

    def failed_gate(self, needs=False):
        gh = self.bin / "gh"
        gh.write_text(cli_tests.GH.replace('issue = {"number":', '''
if a[:2] == ["issue", "edit"]:
    s.setdefault("edits", []).append(a)
    labels = set(s.get("labels", ["ready-for-agent"]))
    for index, value in enumerate(a[:-1]):
        if value == "--add-label": labels.add(a[index + 1])
        if value == "--remove-label": labels.discard(a[index + 1])
    s["labels"] = sorted(labels)
    p.write_text(json.dumps(s))
    sys.exit(0)
if a[:2] == ["issue", "comment"]:
    import time
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ident = len(s.setdefault("timeline", [])) + 1
    s["timeline"].append({"event":"commented", "id":ident, "created_at":stamp, "updated_at":stamp})
    p.write_text(json.dumps(s))
    print(f"https://example.invalid/issues/7#issuecomment-{ident}")
    sys.exit(0)
if a[0] == "api" and a[1].endswith("/timeline"):
    print(json.dumps(s.get("timeline", [])))
    sys.exit(0)
issue = {"number":''').replace(
            'if s.get("sync"):',
            'issue["labels"] = [{"name": value} for value in s.get("labels", ["ready-for-agent"])]\nif s.get("sync"):',
        ))
        self.worker.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(self.base / 'workers')!r})\n"
            "p.write_text(p.read_text() + 'run\\n' if p.exists() else 'run\\n')\n"
            "h = Path('.factory/handoff-7.md')\n"
            "h.parent.mkdir(exist_ok=True)\n"
            f"h.write_text({('Needs: add-only-authorization tests/test_contract.py:12\n' if needs else 'No source change.\n')!r})\n"
        )
        check = [sys.executable, "-c",
                 "from pathlib import Path; "
                 f"p=Path({str(self.base / 'gates')!r}); "
                 "p.write_text(p.read_text()+'run\\n' if p.exists() else 'run\\n'); "
                 "print('FAIL: test_contract (test_contract.ContractTest.test_contract)'); "
                 "raise SystemExit(1)"]
        self.write_config(check=check)
        self.commit_config()

    def test_failed_gate_request_goes_to_manager_without_retry_or_strike(self):
        self.failed_gate(needs=True)
        # The fake manager receives the real packet, then uses the ordinary AUTHORIZE parser.
        manager = self.base / "manager.py"
        seen = self.base / "manager-seen"
        manager.write_text(
            "import sys\nfrom pathlib import Path\n"
            f"Path({str(seen)!r}).write_text(Path(sys.argv[1]).read_text())\n"
            "print('DECISION: AUTHORIZE\\n' + "
            "'{\"justification\":\"Changed behavior requires the contract edit\",\"guidance\":\"Update the named contract\"}')\n"
        )
        with (self.repo / ".factory.toml").open("a") as config:
            config.write('\n[manager]\ncommand = ' + json.dumps([sys.executable, str(manager), "{prompt}"]) + '\n')
        self.commit_config()
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        events = lifecycle.read_events(self.events)
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run"])
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run"])
        self.assertIn("tests/test_contract.py:12", seen.read_text())
        self.assertIn("test_contract.ContractTest.test_contract", seen.read_text())
        self.assertEqual([r["decision"] for r in events if r["event"] == "manage"], ["AUTHORIZE"])
        self.assertFalse(any(r["event"] == "escalate" for r in events))
        self.assertFalse(any(
            ("--add-label", "ready-for-human") in zip(row, row[1:])
            for row in json.loads(self.state.read_text())["edits"]
        ))

    def test_pending_authorization_blocks_a_later_worker_without_a_manager(self):
        self.failed_gate(needs=True)
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run"])
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run"])
        events = lifecycle.read_events(self.events)
        self.assertEqual(sum(r["event"] == "manager-request" for r in events), 1)
        self.assertFalse(any(r["event"] == "escalate" for r in events))

    def test_unchanged_failed_round_reuses_gate_and_stops_attempts(self):
        self.failed_gate()
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        events = lifecycle.read_events(self.events)
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run", "run"])
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run"])
        self.assertEqual([r["code"] for r in events if r["event"] == "escalate"], ["worker_no_progress"])

    def test_recorded_gate_reused_across_passes_but_tampered_report_is_not(self):
        self.failed_gate()
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.assertEqual((self.base / "workers").read_text().splitlines(), ["run"] * 3)
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run"])
        (self.repo / ".factory" / "gate-cache" / "7.md").write_text("forged PASS")
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run", "run"])

    def test_changed_worker_revision_does_not_reuse_failed_gate(self):
        self.failed_gate()
        self.worker.write_text(cli_tests.WORKER)
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        self.assertEqual((self.base / "gates").read_text().splitlines(), ["run", "run"])
        events = lifecycle.read_events(self.events)
        self.assertEqual([r["code"] for r in events if r["event"] == "escalate"], ["gate_failed"])

    def test_unchanged_rework_skips_pr_gate_and_review(self):
        # First worker creates the candidate; its rework makes no source change.
        self.worker.write_text(
            "from pathlib import Path\n"
            "p=Path('change.txt')\n"
            "if not p.exists(): p.write_text('change\\n')\n"
        )
        pr_log = self.base / "pr-runs"
        self.write_config(extra=(
            '\n[[gate.check]]\nname = "pr-check"\nwhen = "pr"\nrun = ' +
            json.dumps([sys.executable, "-c", "from pathlib import Path; "
                        f"p=Path({str(pr_log)!r}); "
                        "p.write_text(p.read_text()+'run\\n' if p.exists() else 'run\\n')"]) + '\n'
        ))
        self.commit_config()
        self.cli("dispatch", "--ticket", "7", "--budget-min", "1")
        events = lifecycle.read_events(self.events)
        self.assertEqual(json.loads(self.state.read_text())["reviews"], 1)
        self.assertEqual(pr_log.read_text().splitlines(), ["run"])
        self.assertEqual([r["code"] for r in events if r["event"] == "escalate"], ["review_findings_unresolved"])

    def test_unchanged_mutation_rework_keeps_open_cause(self):
        from tests import test_actionable_rework as rework_tests
        fixture = rework_tests.ActionableReworkTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.configure_reviews(("APPROVE", "APPROVE"), mutation=True)
        original = fixture.work

        def work(*args, **kwargs):
            result = original(*args, **kwargs)
            fixture.pr["headRefOid"] = fixture.heads[0]
            return (*result[:3], fixture.heads[0])

        fixture.worker.side_effect = work
        original_command = fixture.command_result
        fixture.command.side_effect = lambda args, **kwargs: (
            subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["git", "status"] else original_command(args, **kwargs)
        )
        with mock.patch.object(dispatch, "pr_gate_and_review", wraps=dispatch.pr_gate_and_review) as review:
            dispatch.process_ticket(fixture.issue, 1, False, forced=True)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(fixture.escalate.call_args.kwargs["code"], "mutation_survivors_unresolved")
