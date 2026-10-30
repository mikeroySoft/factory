from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, handoff, learn, lifecycle, manage, stats
from tests import test_frontier

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
AT = "2026-10-27T00:00:00Z"


class EscalateNeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets", manager=["manager"])
        self.cfg.reserved = {"spending": frozenset({"budget"})}
        dispatch.configure(self.cfg)
        self.calls: list[list[str]] = []

        def run(argv, **_kwargs):
            self.calls.append(argv)
            stdout = "https://github.com/acme/widgets/issues/7#issuecomment-100\n" if argv[1:3] == ["issue", "comment"] else ""
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        self.enterContext(mock.patch.object(dispatch, "run", side_effect=run))

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def test_budget_routes_to_manager_and_records_closed_need(self) -> None:
        need = dispatch.classify("budget", {})

        dispatch.escalate(7, "wall-clock budget exceeded", None, need, code="budget")

        edit = next(call for call in self.calls if call[1:3] == ["issue", "edit"])
        self.assertIn("ready-for-agent", edit)
        self.assertNotIn(["--add-label", "ready-for-human"],
                         [edit[index:index + 2] for index in range(len(edit) - 1)])
        [row] = self.rows("escalate")
        self.assertEqual((row["need"], row["category"], row["route"]),
                         ("RULE_COVERABLE", None, "manager"))

        self.assertNotIn("--remove-assignee", edit)
        self.assertIn("--add-assignee", edit)
        self.assertTrue(any(call[1:3] == ["issue", "comment"] for call in self.calls))

    def test_signoff_posts_bounded_question_and_unknown_category_precedes_gh(self) -> None:
        block = "Needs you:\n1. Approve the additional spend\n2. Close as wontfix"
        dispatch.escalate(
            7, "quota increase requested", None, dispatch.SIGNOFF("spending"),
            code="budget", question=block,
        )

        edit = next(call for call in self.calls if call[1:3] == ["issue", "edit"])
        self.assertIn("ready-for-human", edit)
        comment = next(call for call in self.calls if call[1:3] == ["issue", "comment"])
        self.assertTrue(comment[-1].endswith(block), comment[-1])
        [row] = self.rows("escalate")
        self.assertEqual((row["need"], row["category"], row["route"]),
                         ("SIGNOFF", "spending", "human"))

        self.calls.clear()
        with self.assertRaisesRegex(ValueError, "unknown reserved category"):
            dispatch.escalate(
                8, "unknown decision", None, dispatch.SIGNOFF("not_reserved"),
                code="budget", question="Needs you:\n1. Approve it",
            )
        self.assertEqual(self.calls, [])

    def test_missing_need_invalid_need_and_missing_question_are_refused_before_gh(self) -> None:
        with self.assertRaises(TypeError):
            dispatch.escalate(7, "missing", None, code="budget")
        with self.assertRaisesRegex(TypeError, "invalid escalation need"):
            dispatch.escalate(7, "invalid", None, "RULE_COVERABLE", code="budget")
        with self.assertRaisesRegex(ValueError, "Needs you"):
            dispatch.escalate(7, "missing question", None, dispatch.SIGNOFF("spending"), code="budget")
        self.assertEqual(self.calls, [])

    def test_missing_need_fails_without_a_reserved_table(self) -> None:
        cfg = config.Config(self.root, "acme/widgets")
        dispatch.configure(cfg)

        with self.assertRaises(TypeError):
            dispatch.escalate(7, "missing", None, code="budget")

        self.assertEqual(self.calls, [])


class ReservedConfigTest(unittest.TestCase):
    def test_doctor_names_empty_code_at_the_configuration_boundary(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / config.RESERVED_NAME
        path.parent.mkdir()
        path.write_text("| `spending` | Spending or quotas | |\n")
        output = io.StringIO()
        with mock.patch.object(config, "repo_root", return_value=root), \
             mock.patch.object(config, "remote_slug", return_value="acme/widgets"), \
             mock.patch.object(config, "host_config", return_value={}), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["doctor"]), 1)
        self.assertEqual(output.getvalue().splitlines(), [
            f"FAIL configuration: factory: {path}: category 'spending' needs an escalation code",
        ])

    def test_reserved_table_is_parsed_and_names_unknown_categories_and_codes(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / config.RESERVED_NAME
        path.parent.mkdir()
        path.write_text(
            "# Reserved for human\n\n"
            "| Category | Decision | Allowed escalation codes |\n"
            "|---|---|---|\n"
            "| `spending` | Spending or quotas | `budget` |\n"
        )
        self.assertEqual(config.reserved_table(path), {"spending": frozenset({"budget"})})

        path.write_text(
            "| Category | Decision | Allowed escalation codes |\n"
            "|---|---|---|\n"
            "| spending | Spending | budget |\n"
        )
        self.assertEqual(config.reserved_table(path), {"spending": frozenset({"budget"})})

        for row, problem in (
            ("| `unknown` | Unknown | `budget` |\n", "unknown category.*unknown"),
            ("| unknown | Unknown | budget |\n", "unknown category.*unknown"),
            ("| `spending` | Spending | `not_a_code` |\n", "unknown escalation code.*not_a_code"),
        ):
            with self.subTest(problem=problem):
                path.write_text(
                    "| Category | Decision | Allowed escalation codes |\n"
                    "|---|---|---|\n" + row
                )
                with self.assertRaisesRegex(config.ConfigError, problem):
                    config.reserved_table(path)

    def test_doctor_reports_bad_reserved_policy_without_a_traceback(self) -> None:
        output = io.StringIO()
        error = config.ConfigError("docs/reserved-for-human.md: unknown category 'unknown'")

        with mock.patch.object(config, "load", side_effect=error), \
             contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["doctor"]), 1)

        self.assertEqual(
            output.getvalue().splitlines(),
            ["FAIL configuration: factory: docs/reserved-for-human.md: unknown category 'unknown'"],
        )


class LearningAndStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets")
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.events = self.cfg.factory / "events.jsonl"
        rows = [
            {"at": AT, "event": "claimed", "ticket": 1, "title": "Spend"},
            {"at": AT, "event": "escalate", "ticket": 1, "code": "budget", "need": "SIGNOFF", "category": "spending"},
            {"at": AT, "event": "claimed", "ticket": 2, "title": "Gate one"},
            {"at": AT, "event": "escalate", "ticket": 2, "code": "gate_failed", "need": "RULE_COVERABLE", "category": None},
            {"at": AT, "event": "claimed", "ticket": 3, "title": "Gate two"},
            {"at": AT, "event": "escalate", "ticket": 3, "code": "gate_failed", "need": "RULE_COVERABLE", "category": None},
        ]
        self.events.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def test_learning_selects_only_rule_coverable_escalations_and_labels_them(self) -> None:
        with mock.patch.object(dispatch, "EVENTS", self.events), \
             mock.patch.object(dispatch, "FACTORY", self.cfg.factory), \
             mock.patch.object(stats, "escapes", return_value=[]):
            tickets, text = learn.evidence(10)

        self.assertEqual(tickets, [2, 3])
        self.assertNotIn("Ticket #1", text)
        self.assertIn("Ticket #2 [RULE_COVERABLE]", text)
        self.assertIn("Ticket #3 [RULE_COVERABLE]", text)

    def test_week_json_reports_need_counts_and_signoff_share(self) -> None:
        output = io.StringIO()
        with mock.patch.object(stats, "resolve", return_value=self.cfg), \
             mock.patch.object(stats, "_now", return_value=NOW), \
             mock.patch.object(stats, "stuck_over_days", return_value=[]), \
             contextlib.redirect_stdout(output):
            self.assertEqual(stats.main(["--week", "--json"]), 0)

        result = json.loads(output.getvalue())
        self.assertEqual(result["escalations_by_need"], {"RULE_COVERABLE": 2, "SIGNOFF": 1})
        self.assertEqual(result["signoff_share"], 1 / 3)


class NeedCorrectionTest(unittest.TestCase):
    def test_edited_older_recorded_comment_is_not_a_label_only_correction(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.configure(config.Config(root, "acme/widgets"))
        lifecycle.append(dispatch.EVENTS, {
            "at": AT, "event": "escalate", "ticket": 7, "round": 1,
            "code": "budget", "need": "SIGNOFF", "category": "spending",
        })
        for comment, kind in ((99, "manager"), (100, "escalation")):
            lifecycle.append(dispatch.EVENTS, {
                "at": "2026-10-27T00:00:01Z", "event": "comment", "ticket": 7,
                "kind": kind, "round": 1, "comment": comment,
            })
        human = {"login": "mikeroySoft", "type": "User"}
        timeline = [[
            {"event": "commented", "id": 99, "created_at": "2026-10-26T00:00:00Z",
             "updated_at": "2026-10-27T00:01:00Z"},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z",
             "updated_at": "2026-10-27T00:00:01Z"},
            {"event": "unlabeled", "label": {"name": "ready-for-human"}, "actor": human},
            {"event": "labeled", "label": {"name": "ready-for-agent"}, "actor": human},
        ]]
        with mock.patch.object(dispatch, "gh_json", return_value=timeline):
            self.assertFalse(dispatch.correct_need(7))
        self.assertFalse(any(row.get("need_corrected")
                             for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_label_only_human_return_retags_the_escalation(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cfg = config.Config(root, "acme/widgets")
        dispatch.configure(cfg)
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T00:00:00Z", "event": "escalate", "ticket": 7,
            "round": 1, "code": "budget", "need": "SIGNOFF", "category": "spending",
        })
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T00:00:01Z", "event": "comment", "ticket": 7,
            "kind": "escalation", "round": 1, "comment": 100,
        })
        bot = {"login": "factory-bot", "type": "Bot"}
        michael = {"login": "mikeroySoft", "type": "User"}
        timeline = [[
            {"event": "unlabeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-10-27T00:00:00Z", "actor": bot},
            {"event": "labeled", "label": {"name": "ready-for-human"}, "created_at": "2026-10-27T00:00:00Z", "actor": bot},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z", "updated_at": "2026-10-27T00:00:01Z", "user": bot},
            {"event": "unlabeled", "label": {"name": "ready-for-human"}, "created_at": "2026-10-27T00:01:00Z", "actor": michael},
            {"event": "labeled", "label": {"name": "ready-for-agent"}, "created_at": "2026-10-27T00:01:00Z", "actor": michael},
        ]]
        with mock.patch.object(dispatch, "gh_json", return_value=timeline), \
             mock.patch.object(stats, "dispatcher_login", return_value="factory-bot"):
            self.assertTrue(dispatch.correct_need(7))
            self.assertFalse(dispatch.correct_need(7))

        [row] = [row for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("event") == "escalation-correction"]
        self.assertEqual((row["need"], row["need_corrected"], row["category"]),
                         ("RULE_COVERABLE", True, None))

    def test_dispatcher_user_account_is_not_a_human_correction(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.configure(config.Config(root, "acme/widgets"))
        lifecycle.append(dispatch.EVENTS, {
            "at": AT, "event": "escalate", "ticket": 7, "round": 1,
            "code": "budget", "need": "SIGNOFF", "category": "spending",
        })
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T00:00:01Z", "event": "comment", "ticket": 7,
            "kind": "escalation", "round": 1, "comment": 100,
        })
        service = {"login": "factory-service", "type": "User"}
        timeline = [[
            {"event": "labeled", "label": {"name": "ready-for-human"},
             "created_at": AT, "actor": service},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z",
             "updated_at": "2026-10-27T00:00:01Z", "user": service},
            {"event": "unlabeled", "label": {"name": "ready-for-human"},
             "created_at": "2026-10-27T00:01:00Z", "actor": service},
            {"event": "labeled", "label": {"name": "ready-for-agent"},
             "created_at": "2026-10-27T00:01:00Z", "actor": service},
        ]]

        with mock.patch.object(dispatch, "gh_json", return_value=timeline), \
             mock.patch.object(stats, "dispatcher_login", return_value="factory-service"):
            self.assertFalse(dispatch.correct_need(7))
        self.assertFalse(any(row.get("need_corrected")
                             for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_edited_escalation_comment_is_not_a_label_only_correction(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        dispatch.configure(config.Config(root, "acme/widgets"))
        lifecycle.append(dispatch.EVENTS, {
            "at": AT, "event": "escalate", "ticket": 7, "round": 1,
            "code": "budget", "need": "SIGNOFF", "category": "spending",
        })
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T00:00:01Z", "event": "comment", "ticket": 7,
            "kind": "escalation", "round": 1, "comment": 100,
        })
        timeline = [[
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z",
             "updated_at": "2026-10-27T00:01:00Z", "user": {"login": "factory-bot", "type": "Bot"}},
            {"event": "unlabeled", "label": {"name": "ready-for-human"},
             "created_at": "2026-10-27T00:01:00Z", "actor": {"login": "mikeroySoft", "type": "User"}},
            {"event": "labeled", "label": {"name": "ready-for-agent"},
             "created_at": "2026-10-27T00:01:00Z", "actor": {"login": "mikeroySoft", "type": "User"}},
        ]]

        with mock.patch.object(dispatch, "gh_json", return_value=timeline):
            self.assertFalse(dispatch.correct_need(7))


class ManagerRoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, "acme/widgets", manager=["manager"], manager_rounds=2)
        self.cfg.reserved = {"spending": frozenset({"budget", "manager_human"})}
        dispatch.configure(self.cfg)
        self.packet = self.cfg.factory / "escalations" / "7.md"
        self.packet.parent.mkdir(parents=True)
        self.packet.write_text("# Escalation\n")

    def record_escalation(self, **fields: object) -> dict:
        row = {
            "at": AT, "event": "escalate", "ticket": 7, "round": 1,
            "packet": str(self.packet), "code": "budget", "need": "RULE_COVERABLE",
            "category": None, "route": "manager", **fields,
        }
        lifecycle.append(dispatch.EVENTS, row)
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T00:00:01Z", "event": "comment", "ticket": 7,
            "kind": "escalation", "round": 1, "comment": 100,
        })
        return row

    def issue(self) -> dict:
        return {
            "number": 7, "title": "Escalated", "body": "Manager decision needed",
            "labels": [{"name": "ready-for-agent"}],
        }

    def test_human_takeover_stops_classified_manager_escalation(self) -> None:
        self.record_escalation()
        logs: list[str] = []
        with mock.patch.object(dispatch, "gh_json", return_value=[self.issue()]), \
             mock.patch.object(manage, "human_activity", return_value=True), \
             mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass(dry_run=True)

        self.assertFalse(any("would manage escalation" in line for line in logs))

    def test_review_intake_row_is_consumed_by_manager(self) -> None:
        self.record_escalation(pr=42, code="review_intake", need="EXPLAIN")
        logs: list[str] = []
        with mock.patch.object(dispatch, "gh_json", return_value=[self.issue()]), \
             mock.patch.object(manage, "human_activity", return_value=False), \
             mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass(dry_run=True)

        self.assertTrue(any("would manage escalation round 1" in line for line in logs), logs)

    def test_external_review_close_targets_the_reviewed_pr(self) -> None:
        self.record_escalation(pr=42, code="review_intake", need="EXPLAIN")
        timeline = [{"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"}]
        reads = []
        calls = []

        def read(argv):
            reads.append(argv)
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            if argv[:2] == ["pr", "view"]:
                self.assertEqual(argv[2], "42")
                return {"number": 42, "state": "OPEN", "baseRefName": "main"}
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            output = (
                "DECISION: CLOSE\nThe external review cannot be completed"
                if argv[0] == "manager" else
                "https://github.com/acme/widgets/pull/42#issuecomment-101\n"
                if argv[1:3] == ["pr", "comment"] else
                "https://github.com/acme/widgets/issues/7#issuecomment-102\n"
                if argv[1:3] == ["issue", "comment"] else ""
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()

        self.assertTrue(any(call[1:4] == ["pr", "close", "42"] for call in calls), calls)
        self.assertTrue(any(call[1:4] == ["issue", "close", "7"] for call in calls), calls)
        self.assertFalse(any("agent/7" in part for call in reads for part in call), reads)

    def test_invalid_external_human_keeps_the_reviewed_pr_binding(self) -> None:
        self.record_escalation(pr=42, code="review_intake", need="EXPLAIN")
        timeline = [{"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"}]
        manager_outputs = iter([
            "DECISION: HUMAN\nnot a reserved decision",
            "DECISION: CLOSE\nThe external review cannot be completed",
        ])
        calls = []
        issue_comments = iter((101, 102))

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            if argv[:2] == ["pr", "view"]:
                self.assertEqual(argv[2], "42")
                return {"number": 42, "state": "OPEN", "baseRefName": "main"}
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            if argv[0] == "manager":
                return subprocess.CompletedProcess(argv, 0, next(manager_outputs), "")
            if argv[1:3] == ["issue", "comment"]:
                comment = next(issue_comments)
                return subprocess.CompletedProcess(
                    argv, 0, f"https://github.com/acme/widgets/issues/7#issuecomment-{comment}\n", "",
                )
            if argv[1:3] == ["pr", "comment"]:
                return subprocess.CompletedProcess(
                    argv, 0, "https://github.com/acme/widgets/pull/42#issuecomment-201\n", "",
                )
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()
            routed = next(
                row for row in reversed(lifecycle.read_events(dispatch.EVENTS))
                if row.get("event") == "escalate"
            )
            timeline.extend([
                {"event": "unlabeled", "created_at": routed["at"],
                 "label": {"name": "ready-for-human"}},
                {"event": "labeled", "created_at": routed["at"],
                 "label": {"name": "ready-for-agent"}},
                {"event": "assigned", "created_at": routed["at"]},
                {"event": "commented", "id": 101, "created_at": routed["at"]},
            ])
            manage.escalation_pass()

        self.assertEqual(routed.get("pr"), 42)
        self.assertTrue(any(call[1:4] == ["pr", "close", "42"] for call in calls), calls)

    def test_external_review_fix_is_rejected_once_without_consuming_the_round(self) -> None:
        self.record_escalation(pr=42, code="review_intake", need="EXPLAIN")
        timeline = [{"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"}]
        manager_runs = 0

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            self.fail(argv)

        def run(argv, **_kwargs):
            nonlocal manager_runs
            self.assertEqual(argv[0], "manager")
            manager_runs += 1
            return subprocess.CompletedProcess(
                argv, 0, 'DECISION: FIX\n{"worker":"chore","guidance":"Change the PR"}', "",
            )

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()
            manage.escalation_pass()

        events = lifecycle.read_events(dispatch.EVENTS)
        self.assertEqual(manager_runs, 1)
        self.assertFalse(any(row.get("event") == "manage" for row in events))
        self.assertTrue(any(row.get("event") == "manage-rejected"
                            and row.get("reason") == "external_review_action"
                            for row in events))

    def test_signoff_escalation_is_immediately_terminal_for_handoff(self) -> None:
        escalation = self.record_escalation(need="SIGNOFF", category="spending")

        self.assertIn("human sign-off", handoff.terminal(
            self.cfg, lifecycle.read_events(dispatch.EVENTS), escalation,
        ))

    def test_manager_route_label_churn_is_not_human_takeover(self) -> None:
        self.record_escalation()
        timeline = [
            {"event": "unlabeled", "created_at": AT, "label": {"name": "ready-for-human"}},
            {"event": "labeled", "created_at": AT, "label": {"name": "ready-for-agent"}},
            {"event": "assigned", "created_at": AT},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"},
        ]
        calls = []

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            output = "DECISION: RETRY\nApply the documented budget rule" if argv[0] == "manager" else ""
            return subprocess.CompletedProcess(argv, 0, output, "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()

        self.assertTrue(any(call[0] == "manager" for call in calls), calls)
        self.assertTrue(any(call[1:3] == ["issue", "comment"] for call in calls), calls)
        self.assertTrue(any(call[1:3] == ["issue", "edit"] and "--remove-assignee" in call
                            and "ready-for-agent" in call for call in calls), calls)

    def test_human_comment_during_inference_discards_manager_rewrite(self) -> None:
        self.record_escalation()
        timeline = [
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"},
        ]
        calls = []

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [list(timeline)]
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            self.assertEqual(argv[0], "manager")
            timeline.append({
                "event": "commented", "id": 101, "created_at": "2026-10-27T00:01:00Z",
                "user": {"login": "mikeroySoft", "type": "User"},
            })
            return subprocess.CompletedProcess(argv, 0, "DECISION: REWRITE\nReplacement body", "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()

        self.assertEqual(len(calls), 1)
        self.assertFalse(any(row.get("event") == "manage"
                             for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_editing_an_older_recorded_comment_during_inference_stops_manager(self) -> None:
        self.record_escalation()
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-26T00:00:00Z", "event": "comment", "ticket": 7,
            "kind": "manager", "comment": 99,
        })
        older = {"event": "commented", "id": 99, "created_at": "2026-10-26T00:00:00Z"}
        timeline = [
            older,
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"},
        ]
        calls = []

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            older["updated_at"] = "2026-10-27T00:01:00Z"
            output = "DECISION: REWRITE\nReplacement body" if argv[0] == "manager" else ""
            return subprocess.CompletedProcess(argv, 0, output, "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()

        self.assertEqual([call[0] for call in calls], ["manager"])
        self.assertFalse(any(row.get("event") == "manage"
                             for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_repeat_escalation_is_held_after_one_manager_retry(self) -> None:
        self.cfg.manager_rounds = 1
        issue = self.issue()
        issue["assignees"] = []
        comments = 0

        def run(argv, **_kwargs):
            nonlocal comments
            if argv[1:3] == ["issue", "edit"]:
                if "--add-assignee" in argv:
                    issue["assignees"] = [{"login": "factory"}]
                if "--remove-assignee" in argv:
                    issue["assignees"] = []
                labels = {label["name"] for label in issue["labels"]}
                for flag, operation in (("--add-label", labels.add), ("--remove-label", labels.discard)):
                    if flag in argv:
                        operation(argv[argv.index(flag) + 1])
                issue["labels"] = [{"name": label} for label in sorted(labels)]
            if argv[1:3] == ["issue", "comment"]:
                comments += 1
                stdout = f"https://github.com/acme/widgets/issues/7#issuecomment-{comments}\n"
            else:
                stdout = ""
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        def read(argv, **_kwargs):
            if argv[:2] == ["issue", "list"]:
                label = argv[argv.index("--label") + 1]
                return [issue] if label in {item["name"] for item in issue["labels"]} else []
            self.fail(argv)

        logs: list[str] = []
        with mock.patch.object(dispatch, "run", side_effect=run), \
             mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "log", side_effect=logs.append):
            dispatch.escalate(
                7, "budget exhausted", None,
                dispatch.classify("budget", {}), code="budget",
            )
            manage.apply(7, issue, "RETRY", "Apply the documented budget rule", None, self.packet)
            dispatch.escalate(
                7, "budget exhausted again", None,
                dispatch.classify("budget", {}), code="budget",
            )
            [candidate] = dispatch.frontier()
            manage.escalation_pass(dry_run=True)

        self.assertEqual(candidate["held"], "assigned")
        self.assertFalse(any("would manage escalation" in line for line in logs), logs)
        self.assertEqual(
            [row["round"] for row in lifecycle.read_events(dispatch.EVENTS)
             if row.get("event") == "escalate"],
            [1, 2],
        )

    def test_manager_cannot_consume_a_category_missing_from_the_checked_table(self) -> None:
        self.record_escalation()
        timeline = [
            {"event": "unlabeled", "created_at": AT, "label": {"name": "ready-for-human"}},
            {"event": "labeled", "created_at": AT, "label": {"name": "ready-for-agent"}},
            {"event": "assigned", "created_at": AT},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"},
        ]
        calls = []

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            self.fail(argv)

        def run(argv, **_kwargs):
            calls.append(argv)
            output = (
                'DECISION: HUMAN\n{"category":"product_direction","reason":"Choose direction",'
                '"options":["Approve it"]}'
                if argv[0] == "manager" else
                "https://github.com/acme/widgets/issues/7#issuecomment-101\n"
                if argv[1:3] == ["issue", "comment"] else ""
            )
            return subprocess.CompletedProcess(argv, 0, output, "")

        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run):
            manage.escalation_pass()

        escalations = [row for row in lifecycle.read_events(dispatch.EVENTS)
                       if row.get("event") == "escalate"]
        self.assertEqual(
            (escalations[-1]["code"], escalations[-1]["need"], escalations[-1]["route"]),
            ("manager_failed", "CHASE_STALL", "manager"),
        )
        self.assertTrue(any("--add-assignee" in call and "ready-for-agent" in call
                            for call in calls), calls)
        self.assertFalse(any("--add-label ready-for-human" in " ".join(call)
                             for call in calls), calls)

    def test_failed_manager_signoff_publication_does_not_consume_the_round(self) -> None:
        self.record_escalation()
        timeline = [
            {"event": "unlabeled", "created_at": AT, "label": {"name": "ready-for-human"}},
            {"event": "labeled", "created_at": AT, "label": {"name": "ready-for-agent"}},
            {"event": "assigned", "created_at": AT},
            {"event": "commented", "id": 100, "created_at": "2026-10-27T00:00:01Z"},
        ]
        failed_route = True
        manager_runs = 0

        def read(argv):
            if argv[:2] == ["issue", "list"]:
                return [self.issue()]
            if argv[:2] == ["issue", "view"]:
                return self.issue()
            if argv[0] == "api":
                return [timeline]
            self.fail(argv)

        def run(argv, **_kwargs):
            nonlocal manager_runs
            if argv[0] == "manager":
                manager_runs += 1
                output = (
                    'DECISION: HUMAN\n{"category":"spending","reason":"Approve quota",'
                    '"options":["Approve it"]}'
                )
                return subprocess.CompletedProcess(argv, 0, output, "")
            if argv[1:3] == ["issue", "edit"] and failed_route:
                raise subprocess.CalledProcessError(1, argv, stderr="unavailable")
            stdout = (
                "https://github.com/acme/widgets/issues/7#issuecomment-101\n"
                if argv[1:3] == ["issue", "comment"] else ""
            )
            return subprocess.CompletedProcess(argv, 0, stdout, "")

        logs = []
        with mock.patch.object(dispatch, "gh_json", side_effect=read), \
             mock.patch.object(dispatch, "run", side_effect=run), \
             mock.patch.object(dispatch, "log", side_effect=logs.append):
            manage.escalation_pass()
            self.assertFalse(any(row.get("event") == "manage"
                                 for row in lifecycle.read_events(dispatch.EVENTS)))
            self.assertFalse(any(row.get("need") == "SIGNOFF"
                                 for row in lifecycle.read_events(dispatch.EVENTS)))
            self.assertTrue(any("publication failed" in line for line in logs), logs)

            failed_route = False
            manage.escalation_pass()

        self.assertEqual(manager_runs, 2)
        self.assertTrue(any(row.get("event") == "manage"
                            for row in lifecycle.read_events(dispatch.EVENTS)))


class FinalReviewNeedTest(unittest.TestCase):
    def frontier(self):
        case = test_frontier.FrontierTest(
            "test_review_all_waits_for_a_manager_approve_bound_to_the_exact_head"
        )
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.cfg.manager_review = "all"
        case.quiet_provider()
        case.approved_head(test_frontier.H)
        return case

    def test_manager_exhaustion_escalates_once_while_assigned_to_manager(self) -> None:
        case = self.frontier()
        case.cfg.manager_rounds = 1
        dispatch.record(
            "manage", ticket=79, pr=80, head=test_frontier.H2,
            decision="RETRY", round=1,
        )

        def read(args):
            value = case.gh_json(args)
            if args[:2] == ["issue", "view"]:
                assigned = any("--add-assignee" in command for command in case.mutations)
                value["assignees"] = [{"login": "factory"}] if assigned else []
            return value

        with mock.patch.object(dispatch, "gh_json", side_effect=read):
            manage.frontier_pass()
            manage.frontier_pass()

        self.assertEqual(
            [(row["code"], row["need"]) for row in case.escalations()],
            [("manager_exhausted", "CHASE_STALL")],
        )
        self.assertEqual(
            sum(command[1:3] == ["issue", "comment"] for command in case.mutations),
            1,
        )

    def test_released_or_successfully_fixed_escalation_does_not_hold_a_reclaimed_pr(self) -> None:
        for decision in ("RETRY", "FIX"):
            with self.subTest(decision=decision):
                case = self.frontier()
                dispatch.record(
                    "escalate", ticket=79, round=1, route="manager",
                    reason="gate failed", need="RULE_COVERABLE",
                )
                dispatch.record("manage", ticket=79, round=1, decision=decision)
                case.manager_output = "DECISION: APPROVE\nLooks right"

                def read(args, case=case):
                    value = case.gh_json(args)
                    if args[:2] == ["issue", "view"]:
                        value["assignees"] = [{"login": "factory"}]
                    return value

                with mock.patch.object(dispatch, "gh_json", side_effect=read):
                    manage.frontier_pass()

                self.assertTrue(
                    any("--add-label" in command and "factory-approved" in command
                        for command in case.mutations),
                    case.mutations,
                )

    def test_human_names_a_reserved_signoff_and_concrete_options(self) -> None:
        case = self.frontier()
        case.cfg.reserved = {"product_direction": frozenset({"manager_human"})}
        case.manager_output = (
            'DECISION: HUMAN\n{"category":"product_direction","reason":"Choose the product direction",'
            '"options":["Approve the direction","Close as wontfix"]}'
        )

        manage.frontier_pass()

        [escalation] = case.escalations()
        self.assertEqual(
            (escalation["need"], escalation["category"], escalation["code"]),
            ("SIGNOFF", "product_direction", "manager_human"),
        )
        comment = next(cmd[-1] for cmd in case.mutations if cmd[1:3] == ["issue", "comment"])
        self.assertTrue(comment.endswith(
            "Needs you:\n1. Approve the direction\n2. Close as wontfix"
        ))
        prompt = (case.repo / ".factory/manager-prompt-79.md").read_text()
        self.assertIn("product_direction: manager_human", prompt)

    def test_failed_invalid_human_route_does_not_consume_the_head(self) -> None:
        case = self.frontier()
        case.manager_output = "DECISION: HUMAN\nnot a reserved decision"

        def fail_route(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "issue", "edit"]:
                raise subprocess.CalledProcessError(1, cmd, stderr="routing failed")
            return case.fake_run(cmd, *args, **kwargs)

        with mock.patch.object(dispatch, "run", side_effect=fail_route):
            manage.frontier_pass()

        self.assertFalse(any(e.get("event") == "manage" for e in case.events()))
        case.manager_output = "DECISION: APPROVE\nLooks right"
        case.mutations.clear()
        manage.frontier_pass()
        self.assertTrue(any("--add-label" in cmd and "factory-approved" in cmd
                            for cmd in case.mutations), case.mutations)


class EscalationPublicationTest(unittest.TestCase):
    def test_failed_routing_comment_recovers_only_without_human_activity(self) -> None:
        for takeover in (False, True):
            with self.subTest(takeover=takeover):
                root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                cfg = config.Config(
                    root, "acme/widgets", manager=["manager"], review_rounds=0,
                )
                dispatch.configure(cfg)
                dispatch.record("review-intake", pr=17, head="head-0")
                pr = {
                    "number": 17, "state": "OPEN", "isDraft": False,
                    "isCrossRepository": False, "headRefName": "feature",
                    "headRefOid": "head-0", "baseRefOid": "base",
                    "labels": [{"name": "needs-review"}], "reviewRequests": [],
                }
                timeline = (
                    [{"event": "commented", "id": 900,
                      "created_at": "2026-10-29T12:00:00Z",
                      "user": {"login": "maintainer", "type": "User"}}]
                    if takeover else []
                )
                created = comments = 0

                def read(argv, pr=pr, timeline=timeline):
                    if argv[:2] == ["pr", "list"]:
                        return [pr]
                    if argv[0] == "api":
                        return [timeline]
                    self.fail(argv)

                def run(argv, **_kwargs):
                    nonlocal created, comments
                    if argv[1:3] == ["issue", "create"]:
                        created += 1
                        return subprocess.CompletedProcess(
                            argv, 0, "https://github.com/acme/widgets/issues/99\n", "",
                        )
                    if argv[1:3] == ["issue", "comment"]:
                        comments += 1
                        if comments == 1:
                            return subprocess.CompletedProcess(argv, 1, "", "unavailable")
                        return subprocess.CompletedProcess(
                            argv, 0,
                            "https://github.com/acme/widgets/issues/99#issuecomment-100\n", "",
                        )
                    return subprocess.CompletedProcess(argv, 0, "", "")

                with mock.patch.object(dispatch, "gh_json", side_effect=read), \
                     mock.patch.object(dispatch, "run", side_effect=run), \
                     mock.patch.object(dispatch, "review_readiness"):
                    dispatch.review_intake_pass(False)
                    dispatch.review_intake_pass(False)

                receipts = [
                    row for row in lifecycle.read_events(dispatch.EVENTS)
                    if row.get("event") == "comment" and row.get("kind") == "escalation"
                ]
                self.assertEqual(created, 1)
                self.assertEqual(comments, 1 if takeover else 2)
                self.assertEqual(bool(receipts), not takeover)
    def test_failed_label_or_comment_publication_is_not_reported_as_success(self) -> None:
        for need in (dispatch.RULE_COVERABLE, dispatch.SIGNOFF("spending")):
            for action in ("edit", "comment"):
                with self.subTest(need=need, failed_action=action):
                    root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                    cfg = config.Config(root, "acme/widgets")
                    cfg.reserved = {"spending": frozenset({"budget"})}
                    dispatch.configure(cfg)

                    def run(argv, _cwd, check, *_args, action=action):
                        if argv[1:3] == ["issue", action]:
                            if check:
                                raise subprocess.CalledProcessError(1, argv, stderr="unavailable")
                            return subprocess.CompletedProcess(argv, 1, "", "unavailable")
                        return subprocess.CompletedProcess(argv, 0, "", "")

                    with mock.patch.object(dispatch, "_run", side_effect=run), \
                         self.assertRaises(subprocess.CalledProcessError):
                        dispatch.escalate(
                            7, "budget exhausted", None, need, code="budget",
                            question="Needs you:\n1. Approve more quota"
                            if isinstance(need, dispatch.Signoff) else None,
                        )


if __name__ == "__main__":
    unittest.main()
