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

        for row, problem in (
            ("| `unknown` | Unknown | `budget` |\n", "unknown category.*unknown"),
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
        with mock.patch.object(dispatch, "gh_json", return_value=timeline):
            self.assertTrue(dispatch.correct_need(7))
            self.assertFalse(dispatch.correct_need(7))

        [row] = [row for row in lifecycle.read_events(dispatch.EVENTS)
                 if row.get("event") == "escalation-correction"]
        self.assertEqual((row["need"], row["need_corrected"], row["category"]),
                         ("RULE_COVERABLE", True, None))

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


class EscalationPublicationTest(unittest.TestCase):
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
