from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle


class ManagerFixWorkerSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}).start()
        self.root = Path(self.temp.name)
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            workers={
                "prime": ["prime-worker", "{prompt}"],
                "chore": ["chore-worker", "{prompt}"],
                "ci-fix": ["fix-worker", "{prompt}"],
                "default": ["default-worker", "{prompt}"],
            },
            worker_select_label={"prime": "harness-pilot"},
        )
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        dispatch.LOGS.mkdir()

    def fix_round(self, ticket: int, labels: set[str], selected_worker: str) -> tuple[str, dict]:
        commands = []

        def run_worker(argv: list[str], *_args: object) -> tuple[int, None]:
            commands.append(argv[0])
            return 0, None

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_worker", side_effect=run_worker), \
                mock.patch.object(dispatch, "worker_usage", return_value={
                    "model": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "cached_tokens": None,
                    "cost": None,
                }), \
                mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(
                    dispatch,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, "head\n", ""),
                ), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")), \
                mock.patch("factory.results.source_metadata", return_value={}):
            dispatch.worker_round(
                ticket,
                self.root,
                labels,
                "ticket",
                "manager guidance",
                2,
                float("inf"),
                selected_worker=selected_worker,
            )

        decision = next(
            row
            for row in reversed(lifecycle.read_events(dispatch.EVENTS))
            if row.get("event") == "decision"
            and row.get("stage") == "worker_select"
            and row.get("ticket") == ticket
        )
        return commands[0], decision

    def test_label_gated_ticket_stays_on_first_worker_during_fix(self) -> None:
        dispatch.record_worker_selection(7, {"bug", "harness-pilot"})

        command, follow_up = self.fix_round(7, {"bug", "harness-pilot"}, "chore")
        selections = [
            row["chosen"]
            for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("stage") == "worker_select" and row.get("ticket") == 7
        ]

        self.assertEqual(command, "prime-worker")
        self.assertEqual(selections, ["prime", "prime"])
        self.assertIn("sticky", follow_up["rationale"])

    def test_label_gated_override_resolves_with_ticket_labels(self) -> None:
        command, follow_up = self.fix_round(8, {"harness-pilot"}, "prime")

        self.assertEqual(command, "prime-worker")
        self.assertEqual(follow_up["chosen"], "prime")
        self.assertNotEqual(follow_up["chosen"], "default")

    def test_non_gated_ticket_uses_manager_worker(self) -> None:
        command, follow_up = self.fix_round(9, {"chore"}, "ci-fix")

        self.assertEqual(command, "fix-worker")
        self.assertEqual(follow_up["chosen"], "ci-fix")
        self.assertIn("override", follow_up["rationale"])

    def test_label_gated_sticky_worker_ignores_worker_order(self) -> None:
        self.cfg.workers = {
            "chore": ["chore-worker", "{prompt}"],
            "prime": ["prime-worker", "{prompt}"],
            "ci-fix": ["fix-worker", "{prompt}"],
            "default": ["default-worker", "{prompt}"],
        }
        dispatch.record_worker_selection(10, {"bug", "harness-pilot"})

        command, follow_up = self.fix_round(10, {"bug", "harness-pilot"}, "chore")

        self.assertEqual(command, "prime-worker")
        self.assertEqual(follow_up["chosen"], "prime")



if __name__ == "__main__":
    unittest.main()
