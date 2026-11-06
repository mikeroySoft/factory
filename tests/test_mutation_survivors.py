"""PR-tier survivors remain required work until a worker accounts for each identity."""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_actionable_rework as rework
import test_risk_merge as risk_tests

from factory import config, dispatch, lifecycle

_WORKER_ROUND = dispatch.worker_round
SURVIVORS = [
    "factory/dispatch.py:10: NumberReplacer `1` -> `2`",
    "factory/dispatch.py:10: NumberReplacer `1` -> `0`",
    "factory/risk.py:20: BooleanReplacer `True` -> `False`",
]
REPORT = (
    "- mutation: FAIL\n\n## mutation\n```text\n"
    + "\n".join(SURVIVORS)
    + "\nran 3 of 3 mutants in 1s: 3 survived; 3 survived\n```\n"
)


def accounting(rows):
    return "## Mutation survivor accounting\n```json\n" + json.dumps({"mutation_survivors": rows}) + "\n```\n"


def killed(survivor):
    return {"survivor": survivor, "disposition": "killed", "test": "tests.test_dispatch.Case.test_branch"}


class SurvivorRequiredTest(unittest.TestCase):
    patch = risk_tests.HighPipelineBoundaryTest.patch
    github = risk_tests.HighPipelineBoundaryTest.github
    command_result = risk_tests.HighPipelineBoundaryTest.command_result
    configure_reviews = rework.ActionableReworkTest.configure_reviews

    def setUp(self):
        risk_tests.HighPipelineBoundaryTest.setUp(self)
        self.patch(dispatch.brief, "ensure", return_value="")
        self.prompts = []

    def work(self, *args, **kwargs):
        self.prompts.append(dispatch.build_prompt(7, self.wt, args[4]))
        return rework.ActionableReworkTest.work(self, *args, **kwargs)

    def assert_required(self, prompt):
        required = prompt.split("## Required mutation survivor findings", 1)
        self.assertEqual(len(required), 2, "survivors are context, not required findings")
        for survivor in SURVIVORS:
            self.assertIn(survivor, required[1])
        self.assertIn("mutation_survivors", required[1])

    def test_survivor_required_after_both_revise_bounce(self):
        self.configure_reviews(("REVISE", "REVISE"), mutation=True)
        self.patch(dispatch, "run_gate", return_value=(False, REPORT))
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assert_required(self.prompts[1])

    def test_survivor_required_after_deferred_rework(self):
        self.configure_reviews(("REVISE", "REVISE"), mutation=True)
        self.patch(dispatch, "run_gate", return_value=(False, REPORT))
        self.expire = True
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.clock = 0
        self.expire = False
        self.patch(dispatch, "refresh_requeue_context", side_effect=rework._REFRESH_CONTEXT)
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assert_required(self.prompts[1])

    def test_survivor_required_after_escalation_and_dispatcher_restart(self):
        self.configure_reviews(("REVISE", "REVISE"), mutation=True)
        self.patch(dispatch, "run_gate", return_value=(False, REPORT))
        self.cfg.review_rounds = 0
        self.patch(dispatch, "escalate", side_effect=rework._ESCALATE)
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assertTrue(any(row["event"] == "escalate" for row in lifecycle.read_events(dispatch.EVENTS)))
        # The latest attempt hides ordinary requeue context; only durable survivor state can recover it.
        dispatch.record("attempt", ticket=7, head=risk_tests.HEAD)
        dispatch.configure(self.cfg)
        dispatch.process_ticket(self.issue, 1, False, forced=True)
        self.assert_required(self.prompts[1])
        other = dispatch.build_prompt(8, self.wt)
        self.assertNotIn("## Required mutation survivor findings", other)


class MutationSurvivorHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root, "local/smoke")
        self.cfg.checks = [config.Check("mutation", ["mutation"], when="pr")]
        dispatch.configure(self.cfg)
        self.wt = dispatch.FACTORY / "wt-7"
        (self.wt / ".factory").mkdir(parents=True)
        dispatch.LOGS.mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.patch(dispatch, "gh_json", return_value={"title": "change", "body": "", "comments": []})
        self.patch(dispatch, "admit_plan", return_value=None)
        self.patch(dispatch.brief, "ensure", return_value="")
        self.patch(dispatch, "run", side_effect=lambda cmd, **_kw: subprocess.CompletedProcess(
            cmd, 0, risk_tests.HEAD if cmd[:2] == ["git", "rev-parse"] else "", "",
        ))
        self.patch(dispatch, "risk_for_head", return_value=("Medium", []))
        self.gate = self.patch(dispatch, "run_gate", return_value=(False, REPORT))
        dispatch.pr_gate_and_review(self.wt, 7, "attempt", risk_tests.HEAD, {})
        self.gate.return_value = (True, "- test: PASS\n")
        for name in ("journal_attempt_plan", "restore_unplanned_lockfiles", "commit_leftovers"):
            self.patch(dispatch, name)
        self.patch(dispatch, "sync_event", return_value=None)
        self.handoff = ""
        self.patch(dispatch, "run_worker", side_effect=self.work)

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(mock.patch.object(target, name, **kwargs))

    def work(self, _argv, _wt, logfile, handoff, _deadline, **_kwargs):
        logfile.write_text("")
        handoff.write_text("## Changed\nDone.\n## Still unverified\nNone.\n## Next\nNothing.\n" + self.handoff)
        return 0, None

    def round(self):
        return _WORKER_ROUND(7, self.wt, set(), "change", "", 2, float("inf"))

    def test_mutation_survivor_omission_rejects_otherwise_passing_round(self):
        self.handoff = accounting([killed(survivor) for survivor in SURVIVORS[:2]])
        ok, report, _, _ = self.round()
        self.assertFalse(ok, "worker ignored a required survivor but proceeded")
        self.assertIn(SURVIVORS[2], report)
        row = [row for row in lifecycle.read_events(dispatch.EVENTS) if row["event"] == "attempt"][-1]
        self.assertEqual(row["gate"], "FAIL")
        self.assertFalse(row["finished"])

    def test_equivalent_mutant_requires_reasoning_and_killed_requires_test(self):
        cases = [
            ({"disposition": "equivalent"}, False),
            ({"disposition": "equivalent", "reason": "  "}, False),
            ({"disposition": "equivalent", "reason": "equivalent mutant"}, False),
            ({"disposition": "equivalent", "reason": "The bound is unreachable because callers require a positive count."}, True),
            ({"disposition": "killed"}, False),
            ({"disposition": "killed", "test": "tests.test_dispatch.Case.test_branch"}, True),
        ]
        for disposition, accepted in cases:
            with self.subTest(disposition=disposition):
                self.handoff = accounting([
                    {"survivor": SURVIVORS[0], **disposition},
                    *[killed(survivor) for survivor in SURVIVORS[1:]],
                ])
                self.assertEqual(self.round()[0], accepted)

    def test_equivalent_mutant_restatement_is_not_reasoning(self):
        for reason in (
            "This mutant is equivalent.",
            "It is equivalent.",
            "Equivalent.",
            "This mutation is an equivalent mutant.",
        ):
            with self.subTest(reason=reason):
                self.handoff = accounting([
                    {"survivor": SURVIVORS[0], "disposition": "equivalent", "reason": reason},
                    *[killed(survivor) for survivor in SURVIVORS[1:]],
                ])
                ok, report, _, _ = self.round()
                self.assertFalse(ok, "an equivalence assertion is not an equivalence argument")
                self.assertIn(SURVIVORS[0], report)

    def test_mutation_survivor_same_line_different_mutant_is_not_accounted(self):
        self.handoff = accounting([killed(SURVIVORS[0]), killed(SURVIVORS[2])])
        ok, report, _, _ = self.round()
        self.assertFalse(ok)
        self.assertIn(SURVIVORS[1], report)

    def test_mutation_survivor_missing_or_malformed_accounting_is_rejected(self):
        for handoff in ("", "```json\n{broken\n```", accounting([None]), accounting({"bad": "shape"})):
            with self.subTest(handoff=handoff):
                self.handoff = handoff
                self.assertFalse(self.round()[0])

    def test_mutation_survivor_passing_pr_gate_replaces_old_requirements(self):
        self.handoff = accounting([killed(survivor) for survivor in SURVIVORS])
        self.assertTrue(self.round()[0])
        self.patch(dispatch, "review", return_value=("APPROVE", "ok"))
        self.gate.return_value = (True, "- mutation: PASS\n")
        dispatch.pr_gate_and_review(self.wt, 7, "attempt", risk_tests.NEW_HEAD, {})
        self.assertNotIn("## Required mutation survivor findings", dispatch.build_prompt(7, self.wt))
        self.handoff = ""
        self.assertTrue(self.round()[0])

    def test_mutation_survivor_real_report_excludes_other_check_output(self):
        report = REPORT.replace("## mutation\n", "## mutation failure\n")
        report += "\n## extra failure\n```\nfactory/other.py:1: NotAMutant\n```\n"
        self.gate.return_value = (False, report)
        dispatch.pr_gate_and_review(self.wt, 7, "", risk_tests.NEW_HEAD, {})
        prompt = dispatch.build_prompt(7, self.wt)
        for survivor in SURVIVORS:
            self.assertIn(survivor, prompt)
        self.assertNotIn("NotAMutant", prompt)

    def test_mutation_survivor_tool_failure_cannot_erase_requirements(self):
        self.gate.return_value = (False, "- mutation: FAIL\n\n## mutation failure\n```\nbaseline failed\n```\n")
        dispatch.pr_gate_and_review(self.wt, 7, "", risk_tests.NEW_HEAD, {})
        self.assertFalse(self.round()[0])

    def test_mutation_survivor_invalid_disposition_type_is_rejected(self):
        self.handoff = accounting([{"survivor": SURVIVORS[0], "disposition": {}, "reason": "not valid"}])
        self.assertFalse(self.round()[0])

    def test_mutation_survivor_owner_resume_keeps_required_findings(self):
        dispatch.record("generation", ticket=7, generation=1, intent="resume")
        prompt = dispatch.build_prompt(7, self.wt)
        self.assertIn(SURVIVORS[0], prompt)
        self.assertFalse(self.round()[0])

    def test_mutation_survivor_owner_fresh_discards_old_branch_findings(self):
        dispatch.record("generation", ticket=7, generation=1, intent="fresh")
        self.assertNotIn("## Required mutation survivor findings", dispatch.build_prompt(7, self.wt))
        self.assertTrue(self.round()[0])
        self.gate.return_value = (False, REPORT)
        dispatch.pr_gate_and_review(self.wt, 7, "", risk_tests.NEW_HEAD, {})
        self.gate.return_value = (True, "- test: PASS\n")
        self.assertIn(SURVIVORS[0], dispatch.build_prompt(7, self.wt))
        self.assertFalse(self.round()[0])

    def test_survivor_required_from_full_gate_output_beyond_tail_limit(self):
        from test_gate import gate, repo_with_test

        survivors = [
            f"factory/dispatch.py:{line}: NumberReplacer `1` -> `2`"
            for line in range(1, 101)
        ]
        output = "\n".join([*survivors, *["broken mutant diagnostic"] * 100])
        command = [sys.executable, "-c", f"print({output!r}); raise SystemExit(1)"]
        toml = (
            '[[gate.check]]\nname = "mutation"\nwhen = "pr"\nexclusive = false\nrun = '
            + json.dumps(command) + "\n"
        )
        repo = repo_with_test(self.root, toml)
        code, _, report = gate(repo, "--tier", "pr")
        self.assertEqual(code, 1)
        self.gate.return_value = (False, report)
        dispatch.pr_gate_and_review(self.wt, 7, "", risk_tests.NEW_HEAD, {})
        required = dispatch.build_prompt(7, self.wt).split("## Required mutation survivor findings", 1)[1]
        for survivor in survivors:
            self.assertIn(survivor, required)

    def test_mutation_survivor_manager_fix_cannot_proceed_with_omission(self):
        from factory import manage

        issue = {"title": "change", "body": "", "comments": [], "labels": []}
        pr = {"state": "OPEN", "headRefName": "agent/7", "headRefOid": risk_tests.HEAD,
              "baseRefName": "main", "reviewDecision": ""}
        self.patch(dispatch, "gh_json", side_effect=lambda args: pr if args[0] == "pr" else issue)
        self.patch(dispatch, "run", side_effect=lambda cmd, **_kw: subprocess.CompletedProcess(
            cmd, 0, "agent/7" if cmd[:2] == ["git", "branch"] else
            risk_tests.HEAD if cmd[:2] == ["git", "rev-parse"] else "", "",
        ))
        packet = self.root / "packet.md"
        packet.write_text("Fix reviewer findings.")
        push = self.patch(dispatch, "push_agent")
        approve = self.patch(dispatch, "approve_pr")
        escalate = self.patch(dispatch, "escalate")
        manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "Fix"}, packet)
        push.assert_not_called()
        approve.assert_not_called()
        self.assertEqual(escalate.call_args.kwargs["code"], "gate_failed")


class MutationSurvivorCLITest(unittest.TestCase):
    from test_dispatch_lifecycle import LocalCLI as _Fixture

    setUp = _Fixture.setUp
    git = _Fixture.git
    write_config = _Fixture.write_config
    cli = _Fixture.cli

    def test_mutation_survivor_cli_rejects_omission_then_accepts_accounting(self):
        lifecycle.append(self.events, {
            "event": "mutation-survivors", "ticket": 7, "head": "previous",
            "survivors": SURVIVORS,
        })
        self.worker.write_text(
            "import json\nfrom pathlib import Path\n"
            "prompt = Path('.factory-prompt.md').read_text()\n"
            "required = prompt.split('## Required mutation survivor findings')[1]\n"
            f"survivors = {SURVIVORS!r}\n"
            "assert all(s in required for s in survivors)\n"
            "counter = Path('.factory/rounds')\n"
            "counter.parent.mkdir(exist_ok=True)\n"
            "count = int(counter.read_text()) if counter.exists() else 0\n"
            "counter.write_text(str(count + 1))\n"
            "Path('change.txt').write_text(str(count))\n"
            "rows = [{'survivor': s, 'disposition': 'killed', 'test': 'test_branch'} for s in survivors]\n"
            "if count == 0: rows.pop()\n"
            "Path('.factory/handoff-7.md').write_text('## Still unverified\\nNone.\\n## Accounting\\n```json\\n'\n"
            "    + json.dumps({'mutation_survivors': rows}) + '\\n```\\n')\n"
        )
        self.cli("dispatch", "--ticket", "7")
        rows = lifecycle.read_events(self.events)
        attempts = [row for row in rows if row.get("event") == "attempt"]
        self.assertEqual([row["gate"] for row in attempts], ["FAIL", "PASS", "PASS"])
        self.assertFalse(attempts[0]["finished"])
        self.assertTrue(any(row.get("event") == "approved" for row in rows))
        self.assertFalse(any(row.get("event") == "escalate" for row in rows))
