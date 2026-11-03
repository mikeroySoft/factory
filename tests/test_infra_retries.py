"""Network outages retry the same evidence, not a worker's code budget."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_dispatch_lifecycle as cli_fixture


class GateInfraRetriesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = cli_fixture.LocalCLI("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.repo
        self.counter = self.fixture.base / "gate-count"

    def scenario(self, failures, message, settings=""):
        script = self.fixture.base / "network-check.py"
        script.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(self.counter)!r})\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\n"
            f"print({message!r})\n"
            f"raise SystemExit(1 if n < {failures} else 0)\n"
        )
        self.fixture.write_config(
            check=[sys.executable, str(script)],
            reviewer=[sys.executable, "-c", 'print(\'```json\\n{"verdict":"APPROVE","findings":[]}\\n```\\nVERDICT: APPROVE\')'],
        )
        path = self.root / ".factory.toml"
        path.write_text(path.read_text().replace("[gate]\n", "[gate]\n" + settings))
        self.fixture.commit_config()
        self.fixture.cli("dispatch", "--ticket", "7")
        return lifecycle.read_events(self.fixture.events)

    def test_network_retries_do_not_spend_worker_attempts(self):
        rows = self.scenario(3, "error: [92] Stream error in the HTTP/2 framing layer")
        attempts = [row for row in rows if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["PASS"])
        self.assertEqual(self.counter.read_text(), "4")
        self.assertTrue(any(row.get("event") == "approved" for row in rows))

    def test_unmatched_failures_keep_the_existing_cap(self):
        rows = self.scenario(20, "assertion failed: expected 1, got 2")
        self.assertEqual(self.counter.read_text(), "2")
        self.assertEqual([row["gate"] for row in rows if row.get("event") == "attempt"], ["FAIL", "FAIL"])
        self.assertEqual([row["code"] for row in rows if row.get("event") == "escalate"], ["gate_failed"])

    def test_repo_pattern_retries_without_replacing_defaults(self):
        rows = self.scenario(2, "mirror unavailable: INTERNAL_FETCH_518", "infra_patterns = ['INTERNAL_FETCH_518']\n")
        self.assertEqual([row["gate"] for row in rows if row.get("event") == "attempt"], ["PASS"])
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.fixture.base / "config")}):
            cfg = config.load(self.root)
        self.assertTrue(cfg.infra_error("Connection reset by peer"))
        self.assertTrue(cfg.infra_error("INTERNAL_FETCH_518"))

    def test_infra_limit_escalates_as_a_host_problem(self):
        rows = self.scenario(20, "Could not resolve host: index.crates.io", "infra_retries = 1\n")
        self.assertEqual(self.counter.read_text(), "2")
        escalations = [row for row in rows if row.get("event") == "escalate"]
        self.assertEqual([row["code"] for row in escalations], ["infra_failed"])
        self.assertIn("host", escalations[0]["reason"].lower())
        self.assertEqual(len([row for row in rows if row.get("event") == "llm-usage" and row.get("stage") == "worker"]), 1)

    def test_escalation_comment_lists_infra_and_code_retry_classes(self):
        # One recovered network failure followed by two ordinary code failures.
        script = self.fixture.base / "network-check.py"
        script.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(self.counter)!r})\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\n"
            "print('Connection reset by peer' if n == 0 else 'assertion failed')\n"
            "raise SystemExit(1)\n"
        )
        self.fixture.write_config(check=[sys.executable, str(script)])
        self.fixture.commit_config()
        # The fixture GitHub stand-in records the exact issue comment body.
        gh = self.fixture.bin / "gh"
        gh.write_text(gh.read_text().replace(
            'elif a[:2] not in',
            'elif a[:2] == ["issue", "comment"]:\n'
            '    s["escalation_comment"] = a[a.index("--body") + 1]\n'
            '    p.write_text(json.dumps(s))\n'
            'elif a[:2] not in',
        ))
        self.fixture.cli("dispatch", "--ticket", "7")
        body = json.loads(self.fixture.state.read_text())["escalation_comment"]
        self.assertIn("infra", body.lower())
        self.assertIn("code", body.lower())
        self.assertEqual(self.counter.read_text(), "3")

    def test_infra_signal_survives_report_truncation(self):
        rows = self.scenario(1, "Connection reset by peer\n" + "trailing diagnostics\n" * 300)
        self.assertEqual(self.counter.read_text(), "2")
        self.assertEqual([r["gate"] for r in rows if r.get("event") == "attempt"], ["PASS"])

    def test_zero_infra_retries_escalates_the_first_failure(self):
        rows = self.scenario(20, "Connection reset by peer", "infra_retries = 0\n")
        self.assertEqual(self.counter.read_text(), "1")
        self.assertEqual([r["code"] for r in rows if r.get("event") == "escalate"], ["infra_failed"])

    def test_mixed_gate_failure_keeps_the_code_cap(self):
        self.fixture.write_config(
            check=[sys.executable, "-c", "print('Connection reset by peer'); raise SystemExit(1)"],
            extra='[[gate.check]]\nname = "logic"\nrun = '
            + json.dumps([sys.executable, "-c", "print('assertion failed'); raise SystemExit(1)"]) + "\n",
        )
        self.fixture.commit_config()
        self.fixture.cli("dispatch", "--ticket", "7")
        rows = lifecycle.read_events(self.fixture.events)
        self.assertEqual([r["gate"] for r in rows if r.get("event") == "attempt"], ["FAIL", "FAIL"])
        self.assertEqual([r["failure_class"] for r in rows if r.get("event") == "failure"], ["code", "code"])

    def test_colon_named_code_check_keeps_mixed_failure_on_code_cap(self):
        self.fixture.write_config(
            check=[sys.executable, "-c", "print('Connection reset by peer'); raise SystemExit(1)"],
            extra='[[gate.check]]\nname = "lint:python"\nrun = '
            + json.dumps([sys.executable, "-c", "print('assertion failed'); raise SystemExit(1)"]) + "\n",
        )
        self.fixture.commit_config()
        self.fixture.cli("dispatch", "--ticket", "7")
        rows = lifecycle.read_events(self.fixture.events)
        self.assertEqual([r["code"] for r in rows if r.get("event") == "escalate"], ["gate_failed"])
        self.assertEqual([r["failure_class"] for r in rows if r.get("event") == "failure"], ["code", "code"])
        self.assertEqual([r["gate"] for r in rows if r.get("event") == "attempt"], ["FAIL", "FAIL"])

    def test_pr_tier_network_recovery_does_not_bounce_worker(self):
        self.fixture.pr_config(fails=0)
        check = self.fixture.base / "pr_check.py"
        check.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(self.counter)!r})\n"
            "n = int(p.read_text()) if p.exists() else 0\n"
            "p.write_text(str(n + 1))\n"
            "print('Connection reset by peer')\n"
            "raise SystemExit(n == 0)\n"
        )
        reviewer = self.fixture.base / "approve.py"
        reviewer.write_text("print(" + repr('```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE') + ")\n")
        path = self.root / ".factory.toml"
        text = path.read_text()
        # Keep the fixture's changed Python path (Medium risk), but approve its first review.
        text = text.replace(str(self.fixture.reviewer), str(reviewer))
        path.write_text(text)
        self.fixture.commit_config()
        self.fixture.cli("dispatch", "--ticket", "7")
        rows = lifecycle.read_events(self.fixture.events)
        self.assertEqual(self.counter.read_text(), "2")
        self.assertEqual([r["gate"] for r in rows if r.get("event") == "attempt"], ["PASS"])
        self.assertTrue(any(r.get("event") == "approved" for r in rows))

    def test_high_risk_mutation_infra_exhaustion_stops_before_review(self):
        self.worker = self.fixture.worker
        self.worker.write_text(
            "from pathlib import Path\n"
            "p = Path('.github/workflows/local.yml')\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\n"
            "p.write_text('name: local\\n')\n"
        )
        check = self.fixture.base / "mutation-check.py"
        check.write_text(
            "from pathlib import Path\n"
            f"p = Path({str(self.counter)!r})\n"
            "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
            "print('Connection reset by peer')\n"
            "raise SystemExit(1)\n"
        )
        self.fixture.write_config(
            extra='[[gate.check]]\nname = "mutation"\nwhen = "pr"\nrun = '
            + json.dumps([sys.executable, str(check)]) + "\n",
        )
        path = self.root / ".factory.toml"
        path.write_text(path.read_text().replace("[gate]\n", "[gate]\ninfra_retries = 1\n"))
        self.fixture.commit_config()
        self.fixture.cli("dispatch", "--ticket", "7")
        rows = lifecycle.read_events(self.fixture.events)
        self.assertEqual([r["code"] for r in rows if r.get("event") == "escalate"], ["infra_failed"])
        self.assertEqual(self.counter.read_text(), "2")
        self.assertEqual(len([r for r in rows if r.get("event") == "attempt"]), 1)
        self.assertFalse(any(r.get("event") in {"review", "approved"} for r in rows))


class CIInfraRetriesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        globals_patch = mock.patch.dict(dispatch.__dict__)
        globals_patch.start()
        self.addCleanup(globals_patch.stop)
        dispatch.configure(config.Config(root=self.root, repo="local/infra"))
        self.attempt = 1
        self.message = "error: failed to download from https://static.crates.io/crates/foo: Connection reset by peer"
        self.reruns = []
        self.comments = []
        self.check = {"name": "signatures", "bucket": "fail", "link": "https://github.com/local/infra/actions/runs/42/job/99"}
        patcher = mock.patch.object(dispatch, "run", side_effect=self.command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"]:
            if "/actions/jobs/" in argv[2]:
                output = json.dumps({"run_attempt": self.attempt, "runner_name": "host", "steps": [{"conclusion": "failure"}]})
            elif "/actions/runs/" in argv[2]:
                output = json.dumps({"status": "completed", "run_attempt": self.attempt})
            else:
                raise AssertionError(argv)
        elif argv[:3] == ["gh", "run", "view"]:
            self.assertIn("--job", argv)
            self.assertIn("--log", argv)
            output = self.message
        elif argv[:3] == ["gh", "run", "rerun"]:
            self.reruns.append(argv)
            output = ""
        elif argv[:3] == ["gh", "issue", "comment"]:
            self.comments.append(argv[argv.index("--body") + 1])
            output = ""
        elif argv[:3] == ["gh", "issue", "edit"]:
            output = ""
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, output, "")

    def triage(self, checks=None, dry_run=False):
        return dispatch.triage_cancelled(70, checks or [self.check], dry_run, 7, "head")

    def test_network_ci_reruns_once_per_observed_attempt(self):
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(len(self.reruns), 1)
        self.attempt = 2
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(len(self.reruns), 2)

    def test_ci_infra_limit_escalates_with_classed_comment(self):
        dispatch.cfg.infra_retries = 1
        self.triage()
        self.attempt = 2
        checks = self.triage()
        self.assertEqual(checks[0]["bucket"], "infra")
        dispatch.runner_unavailable(7, 70, checks, False)
        self.assertEqual(len(self.reruns), 1)
        self.assertIn("infra", self.comments[0].lower())
        self.assertIn("host", self.comments[0].lower())
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual([r["code"] for r in rows if r.get("event") == "escalate"], ["infra_failed"])

    def test_unmatched_ci_failure_is_not_retried(self):
        self.message = "error[E0308]: mismatched types"
        self.assertEqual(self.triage()[0]["bucket"], "fail")
        self.assertEqual(self.reruns, [])

    def test_ci_dry_run_does_not_spend_retries(self):
        self.assertEqual(self.triage(dry_run=True)[0]["bucket"], "pending")
        self.assertEqual(self.reruns, [])
        self.assertEqual(lifecycle.read_events(dispatch.EVENTS), [])

    def test_pollers_with_and_without_head_share_retry_budget(self):
        self.assertEqual(dispatch.triage_cancelled(70, [self.check])[0]["bucket"], "pending")
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(len(self.reruns), 1)

    def test_mixed_code_and_network_jobs_do_not_rerun_code(self):
        other = {**self.check, "name": "logic", "link": self.check["link"].replace("99", "100")}
        original = self.command

        def command(argv, **kwargs):
            if argv[:3] == ["gh", "run", "view"] and "100" in argv:
                return subprocess.CompletedProcess(argv, 0, "assertion failed", "")
            return original(argv, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=command):
            self.assertEqual({c["bucket"] for c in self.triage([self.check, other])}, {"fail"})
        self.assertEqual(self.reruns, [])

    def test_network_jobs_in_one_run_share_a_retry(self):
        other = {**self.check, "name": "download", "link": self.check["link"].replace("99", "100")}
        self.assertEqual([c["bucket"] for c in self.triage([self.check, other])], ["pending", "pending"])
        self.assertEqual(len(self.reruns), 1)

    def test_unreadable_ci_logs_fail_closed(self):
        original = self.command

        def command(argv, **kwargs):
            if argv[:3] == ["gh", "run", "view"]:
                return subprocess.CompletedProcess(argv, 1, "", "log unavailable")
            return original(argv, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=command):
            self.assertEqual(self.triage()[0]["bucket"], "fail")
        self.assertEqual(self.reruns, [])

    def test_pending_sibling_prevents_workflow_rerun(self):
        sibling = {**self.check, "name": "slow", "bucket": "pending"}
        self.assertEqual([c["bucket"] for c in self.triage([self.check, sibling])], ["pending", "pending"])
        self.assertEqual(self.reruns, [])

    def test_active_workflow_defers_failed_job_until_logs_are_available(self):
        active = True
        original = self.command

        def command(argv, **kwargs):
            if argv[:2] == ["gh", "api"] and "/actions/runs/" in argv[2]:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps({"status": "in_progress" if active else "completed"}), "",
                )
            if active and argv[:3] == ["gh", "run", "view"]:
                return subprocess.CompletedProcess(
                    argv, 1, "", "run 42 is still in progress; logs will be available when it is complete",
                )
            return original(argv, **kwargs)

        # Required-only polls can omit the still-running sibling entirely.
        with mock.patch.object(dispatch, "run", side_effect=command):
            for checks in ([self.check], [self.check, {**self.check, "name": "slow", "bucket": "pending"}]):
                with self.subTest(checks=checks):
                    self.assertEqual({c["bucket"] for c in self.triage(checks)}, {"pending"})
                    self.assertEqual(self.reruns, [])
                    self.assertEqual(lifecycle.read_events(dispatch.EVENTS), [])
            active = False
            self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(len(self.reruns), 1)
        failures = [r for r in lifecycle.read_events(dispatch.EVENTS) if r.get("event") == "failure"]
        self.assertEqual([r["failure_class"] for r in failures], ["infra"])

    def test_ticketless_rerun_is_included_once_in_escalation_evidence(self):
        dispatch.cfg.infra_retries = 1
        self.assertEqual(dispatch.triage_cancelled(70, [self.check])[0]["bucket"], "pending")
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.assertEqual(self.triage()[0]["bucket"], "pending")
        self.attempt = 2
        checks = self.triage()
        dispatch.runner_unavailable(7, 70, checks, False)
        self.assertEqual(len(self.reruns), 1)
        self.assertIn("| 1. ci run 42 attempt 1 | infra | infra retry 1/1; normal cap unchanged |", self.comments[0])
        failures = [r for r in lifecycle.read_events(dispatch.EVENTS) if r.get("event") == "failure"]
        self.assertEqual([(r["ticket"], r["run"], r["attempt"]) for r in failures], [(7, 42, 1)])

    def test_ticket_association_after_ci_changes_preserves_prior_retry(self):
        dispatch.cfg.infra_retries = 1
        dispatch.triage_cancelled(70, [self.check])
        self.attempt = 2
        self.message = "assertion failed"
        self.assertEqual(self.triage()[0]["bucket"], "fail")
        self.assertIn("| 1. ci run 42 attempt 1 | infra |", dispatch.retry_summary(7))
        self.assertIn("infra retry 1/1; normal cap unchanged", dispatch.retry_summary(7))
        self.assertEqual(len(self.reruns), 1)

    def test_ci_retry_evidence_is_not_suppressed_by_another_ticket(self):
        dispatch.cfg.infra_retries = 1
        dispatch.triage_cancelled(70, [self.check], ticket=8, head="head")
        self.triage()
        self.triage()
        self.attempt = 2
        dispatch.runner_unavailable(7, 70, self.triage(), False)
        failures = [r for r in lifecycle.read_events(dispatch.EVENTS) if r.get("event") == "failure"]
        self.assertEqual([(r["ticket"], r["run"], r["attempt"]) for r in failures], [(8, 42, 1), (7, 42, 1)])
        self.assertIn("| 1. ci run 42 attempt 1 | infra | infra retry 1/1; normal cap unchanged |", self.comments[0])
        self.assertEqual(len(self.reruns), 1)

    def test_pending_ci_retry_does_not_report_terminal_host_exhaustion(self):
        dispatch.cfg.infra_retries = 1
        for _ in range(2):
            pending = self.triage()[0]
            self.assertEqual(pending["bucket"], "pending")
            self.assertNotIn("infra_reason", pending)
            self.assertNotIn("infra_limit", pending)
        self.attempt = 2
        exhausted = self.triage()[0]
        self.assertEqual(exhausted["bucket"], "infra")
        self.assertEqual(exhausted["infra_reason"], "network/download host problem")
        self.assertEqual((exhausted["infra_retry"], exhausted["infra_limit"]), (1, 1))
        self.assertEqual(len(self.reruns), 1)


class InfraConfigTest(unittest.TestCase):
    def test_invalid_retry_policy_is_rejected_at_load(self):
        for value in ("infra_retries = -1", "infra_retries = true", "infra_patterns = 'reset'", "infra_patterns = ['[']", "infra_patterns = ['']"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".factory.toml").write_text('[repo]\nslug = "local/infra"\n[gate]\n' + value)
                with mock.patch.object(config, "repo_root", return_value=root), mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}), self.assertRaises(config.ConfigError):
                    config.load(root)


class InfraOutcomeTest(unittest.TestCase):
    def test_network_outages_are_not_repeated_engine_defects(self):
        from datetime import UTC, datetime

        from factory import watch

        at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rows = [
            {"event": "escalate", "code": code, "ticket": ticket, "at": at}
            for code in ("infra_failed", "reviewer_unavailable")
            for ticket in (1, 2, 3)
        ]
        self.assertEqual(set(watch.repeated_escalations(rows)), {"reviewer_unavailable"})


if __name__ == "__main__":
    unittest.main()
