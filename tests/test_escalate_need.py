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

from factory import config, dispatch, learn, lifecycle, stats

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
        self.assertFalse(any(call[1:3] == ["issue", "comment"] for call in self.calls))
        [row] = self.rows("escalate")
        self.assertEqual((row["need"], row["category"], row["route"]),
                         ("RULE_COVERABLE", None, "manager"))

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


class ReservedConfigTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
