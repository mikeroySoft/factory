"""Review rounds through the real dispatcher and manager, with local subprocesses."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage, stats
from tests import test_dispatch_lifecycle as smoke
from tests.test_review_convergence import finding, tally


class ReviewRoundMetricsTest(unittest.TestCase):
    def test_resumed_dispatcher_round_numbers_do_not_merge_ticket_rounds(self) -> None:
        events = [
            {
                "event": "review-result", "dispatcher_run_id": run_id, "review_round": 1,
                "role": role, "accepted": True, "verdict": verdict,
                "counts": {"important": int(verdict != "APPROVE")},
            }
            for run_id, role, verdict in (
                ("first", "primary", "APPROVE"), ("first", "second", "REQUEST_CHANGES"),
                ("resumed", "primary", "APPROVE"), ("resumed", "second", "APPROVE"),
            )
        ]
        self.assertEqual(stats.review_convergence(events), {
            "rounds_to_pass": 2, "repeated_findings_dropped": 0,
            "important_findings_per_round": [1, 0],
        })


class ReviewRoundsTest(unittest.TestCase):
    setUp = smoke.LocalCLI.setUp
    git = smoke.LocalCLI.git
    write_config = smoke.LocalCLI.write_config
    commit_config = smoke.LocalCLI.commit_config
    cli = smoke.LocalCLI.cli

    def prepare(self, outputs: list[str], *, high: bool = True, rounds: int = 3) -> None:
        self.state.write_text(json.dumps({"outputs": outputs}))
        self.reviewer.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "p = Path(os.environ['SMOKE_STATE'])\n"
            "s = json.loads(p.read_text())\n"
            "seen = s.setdefault('seen', [])\n"
            "seen.append({'prompt': sys.argv[1], 'diff': Path('review.diff').read_text(),\n"
            "             'full': Path('review-full.diff').read_text()\n"
            "                     if Path('review-full.diff').exists() else None})\n"
            "p.write_text(json.dumps(s))\n"
            "print(s['outputs'][len(seen) - 1])\n"
        )
        self.worker.write_text(
            "import json, os\n"
            "from pathlib import Path\n"
            "p = Path(os.environ['SMOKE_STATE'])\n"
            "s = json.loads(p.read_text())\n"
            "s.setdefault('workers', []).append(Path('.factory-prompt.md').read_text())\n"
            "p.write_text(json.dumps(s))\n"
            "p = Path('change.txt')\n"
            "p.write_text(p.read_text() + 'change\\n' if p.exists() else 'change\\n')\n"
        )
        self.write_config(
            worker=[sys.executable, str(self.worker), "--model=worker/model"],
            reviewer=[sys.executable, str(self.reviewer), "{prompt}", "--model=primary/model"],
            extra=(
                '[[gate.check]]\nname = "mutation"\nwhen = "pr"\nrun = '
                + json.dumps([sys.executable, "-c", "raise SystemExit(0)"]) + "\n"
            ),
        )
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text().replace("review_rounds = 1", f"review_rounds = {rounds}"))
        self.commit_config()
        if high:
            (self.base / "config").mkdir(exist_ok=True)
            (self.base / "config" / "factory").mkdir(exist_ok=True)
            (self.base / "config" / "factory" / "config.toml").write_text(
                "[defaults.review]\nsecond_command = "
                + json.dumps([
                    sys.executable, str(self.reviewer), "{prompt}", "--model=second/model",
                ]) + "\n"
            )
            # Changing dispatcher code selects High without changing real production files.
            self.worker.write_text(
                self.worker.read_text()
                + "Path('factory').mkdir(exist_ok=True)\n"
                + "Path('factory/dispatch.py').write_text('# local high-risk fixture\\n')\n"
            )

    def journal_rows(self) -> list[dict]:
        return lifecycle.read_events(self.repo / ".factory" / "events.jsonl")

    def test_malformed_important_retries_without_worker_bounce(self) -> None:
        row = finding()
        del row["class"]
        malformed = tally("REVISE", [row])
        self.prepare([malformed] * 3, high=False)

        self.cli("dispatch", "--ticket", "7")

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["seen"]), 3)
        self.assertEqual(len(state["workers"]), 1)
        self.assertEqual(
            [row["code"] for row in self.journal_rows() if row.get("event") == "escalate"],
            ["reviewer_unavailable"],
        )
        self.assertFalse((self.repo / ".factory" / "review-ledger-7.json").exists())

    def test_dual_split_uses_one_bounce_and_combined_round_metrics(self) -> None:
        self.prepare([
            tally("APPROVE", []), tally("REVISE", [finding(finding_id=None)]),
            tally("APPROVE", [], [{"id": "F1", "status": "fixed"}]),
            tally("APPROVE", [], [{"id": "F1", "status": "fixed"}]),
        ])

        self.cli("dispatch", "--ticket", "7")

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["workers"]), 2)
        self.assertEqual(state["workers"][1].count("Value is wrong."), 1)
        self.assertNotIn("High-risk disagreement", state["workers"][1])
        self.assertEqual(len(state["seen"]), 4)
        primary, second = state["seen"][2:]
        self.assertEqual(primary["diff"], second["diff"])
        self.assertNotEqual(primary["diff"], primary["full"])
        for review in (primary, second):
            self.assertIn('"id": "F1"', review["prompt"])
        self.assertTrue(any(row.get("event") == "approved" for row in self.journal_rows()))
        self.assertEqual(stats.review_convergence(self.journal_rows()), {
            "rounds_to_pass": 2, "repeated_findings_dropped": 0,
            "important_findings_per_round": [1, 0],
        })

    def test_unchanged_head_rereview_has_an_empty_primary_diff(self) -> None:
        self.prepare([
            tally("REVISE", [finding()]),
            tally("APPROVE", [], [{"id": "F1", "status": "withdrawn"}]),
        ], high=False)
        self.worker.write_text(self.worker.read_text().replace(
            "p.write_text(p.read_text() + 'change\\n' if p.exists() else 'change\\n')",
            "p.exists() or p.write_text('change\\n')",
        ))

        self.cli("dispatch", "--ticket", "7")

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["seen"]), 2)
        self.assertEqual(state["seen"][1]["diff"], "")
        self.assertTrue(state["seen"][1]["full"])
        self.assertNotIn("Full review fallback", state["seen"][1]["prompt"])

    def test_distinct_dual_findings_keep_independent_ids_and_survive_rereview(self) -> None:
        first = finding()
        other = finding()
        other.update({"class": "other-defect", "path": "other.py", "summary": "Other defect."})
        other["instances"] = [{"path": "other.py", "line": 1, "summary": "Other defect."}]
        primary_statuses = [
            {"id": "F1", "status": "still-open"}, {"id": "F2", "status": "fixed"},
        ]
        second_statuses = [
            {"id": "F1", "status": "fixed"}, {"id": "F2", "status": "still-open"},
        ]
        fixed = [{"id": identity, "status": "fixed"} for identity in ("F1", "F2")]
        self.prepare([
            tally("REVISE", [first]), tally("REVISE", [other]),
            tally("REVISE", [first], primary_statuses),
            tally("REVISE", [{**other, "id": "F2"}], second_statuses),
            tally("APPROVE", [], fixed), tally("APPROVE", [], fixed),
        ])

        self.cli("dispatch", "--ticket", "7")

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["workers"]), 3)
        for prompt in state["workers"][1:]:
            self.assertEqual(prompt.count("Value is wrong."), 1)
            self.assertEqual(prompt.count("Other defect."), 1)
            self.assertIn("[F1 ", prompt)
            self.assertIn("[F2 ", prompt)
        self.assertIn("[F1 still-open]", state["workers"][2])
        self.assertIn("[F2 still-open]", state["workers"][2])
        self.assertEqual(stats.review_convergence(self.journal_rows()), {
            "rounds_to_pass": 3, "repeated_findings_dropped": 0,
            "important_findings_per_round": [2, 2, 0],
        })

    def test_repeated_dual_findings_are_dropped_once_and_budget_still_exhausts(self) -> None:
        row = finding(finding_id=None)
        still_open = [{"id": "F1", "status": "still-open"}]
        self.prepare([
            tally("REVISE", [row, row]), tally("APPROVE", []),
            tally("REVISE", [row, row], still_open),
            tally("APPROVE", [], [{"id": "F1", "status": "fixed"}]),
        ], rounds=1)

        self.cli("dispatch", "--ticket", "7")

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["workers"]), 2)
        self.assertEqual(state["workers"][1].count("Value is wrong."), 1)
        self.assertEqual(
            [row["code"] for row in self.journal_rows() if row.get("event") == "escalate"],
            ["review_revise"],
        )
        ledger = json.loads((self.repo / ".factory" / "review-ledger-7.json").read_text())
        self.assertEqual(ledger["repeated_dropped"], 2)
        self.assertEqual(stats.review_convergence(self.journal_rows()), {
            "rounds_to_pass": None, "repeated_findings_dropped": 2,
            "important_findings_per_round": [1, 1],
        })

    def test_manager_fix_regates_and_reviews_both_incremental_snapshots(self) -> None:
        fixed = [{"id": "F1", "status": "fixed"}]
        self.prepare([
            tally("APPROVE", []), tally("REVISE", [finding(finding_id=None)]),
            tally("APPROVE", [], fixed), tally("APPROVE", [], fixed),
        ], rounds=0)
        self.cli("dispatch", "--ticket", "7")
        wt = self.repo / ".factory" / "wt-7"
        previous = self.git("-C", str(wt), "rev-parse", "HEAD").stdout.strip()
        packet = self.repo / ".factory" / "packet.md"
        packet.write_text("Resolve the review finding.")
        issue = {"title": "local change", "body": "", "comments": [], "labels": []}
        pr = {
            "state": "OPEN", "headRefName": "agent/7", "headRefOid": previous,
            "baseRefName": "main", "reviewDecision": "",
        }
        with mock.patch.dict(os.environ, self.env), \
                mock.patch.object(
                    dispatch, "gh_json",
                    side_effect=lambda args: pr if args[:2] == ["pr", "view"] else issue,
                ), \
                mock.patch.object(dispatch, "approve_pr", return_value=True) as approve, \
                mock.patch.object(dispatch, "escalate") as escalate:
            dispatch.configure(config.load(self.repo))
            manage.apply(7, issue, "FIX", "", {"worker": "default", "guidance": "fix"}, packet)

        state = json.loads(self.state.read_text())
        self.assertEqual(len(state["seen"]), 4)
        primary, second = state["seen"][2:]
        self.assertEqual(primary["diff"], second["diff"])
        self.assertNotEqual(primary["diff"], primary["full"])
        self.assertIn(previous, primary["prompt"])
        self.assertIn(previous, second["prompt"])
        self.assertIn('"id": "F1"', primary["prompt"])
        self.assertIn('"id": "F1"', second["prompt"])
        attempts = [row for row in self.journal_rows() if row.get("event") == "attempt"]
        self.assertEqual(attempts[-1]["gate"], "PASS")
        self.assertNotEqual(attempts[-1]["head"], previous)
        approve.assert_called_once_with(7, attempts[-1]["head"])
        escalate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
