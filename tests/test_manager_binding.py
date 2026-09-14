"""Manager rewrites and splits preserve immutable initiative bindings."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle, manage


class ManagerBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".factory").mkdir()
        self.cfg = config.Config(self.root, "acme/widgets")
        dispatch.configure(self.cfg)
        self.packet = self.root / ".factory/escalation.md"
        self.packet.write_text("manager evidence")
        sections = {
            "Outcome": "Ship bounded work",
            "Boundaries": "No authority expansion",
            "Plan": "Change only the requested slice",
            "Success evidence": "Observable result",
        }
        digest = hashlib.sha256(
            json.dumps(
                sections,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.baseline = {
            "schema_version": 1,
            "initiative": 50,
            "sections": sections,
            "sha256": digest,
            "source_url": "https://github.com/acme/widgets/issues/50",
            "observed_at": "2026-09-14T12:00:00Z",
        }
        self.calls: list[list[str]] = []
        self.next_issue = 80

    def bound(self, text: str, baseline: dict | None = None) -> str:
        return text + "\n\n" + binding.render(baseline or self.baseline)

    def accept(self) -> None:
        lifecycle.append(
            dispatch.EVENTS,
            {
                "event": "plan-bound",
                "ticket": 7,
                "schema_version": 1,
                "baseline": self.baseline,
                "issue": {
                    "title": "Bounded slice",
                    "body": self.bound("Human-approved scope"),
                    "comments": [],
                },
            },
        )

    def run_command(self, command: list[str], *args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(command)
        stdout = ""
        if command[1:3] == ["issue", "create"]:
            stdout = f"https://github.com/acme/widgets/issues/{self.next_issue}"
            self.next_issue += 1
        elif command[1:3] == ["issue", "comment"]:
            stdout = "https://github.com/acme/widgets/issues/7#issuecomment-701"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def test_rewrite_rejects_lost_or_replaced_binding_before_mutation(self) -> None:
        self.accept()
        live = {"title": "Bounded slice", "body": self.bound("Latest live scope")}
        replacement = copy.deepcopy(self.baseline)
        replacement["observed_at"] = "2026-09-14T13:00:00Z"

        proposed_bodies = (
            "Manager replacement without a binding",
            self.bound("Manager replacement", replacement),
        )
        for proposed in proposed_bodies:
            with self.subTest(proposed=proposed[:30]), mock.patch.object(
                dispatch, "gh_json", return_value=live
            ), mock.patch.object(dispatch, "run", side_effect=self.run_command):
                with self.assertRaisesRegex(
                    binding.BindingError,
                    "preserve the accepted Initiative and Plan baseline",
                ):
                    manage.apply(
                        7,
                        {"title": "Bounded slice", "body": "stale list body"},
                        "REWRITE",
                        proposed,
                        None,
                        self.packet,
                    )
            self.assertEqual(self.calls, [])

    def test_rewrite_refuses_a_live_downgrade_of_an_accepted_ticket(self) -> None:
        self.accept()
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Bounded slice", "body": "Binding was removed"},
        ), mock.patch.object(dispatch, "run", side_effect=self.run_command):
            with self.assertRaisesRegex(
                binding.BindingError,
                "removed its accepted Initiative and Plan baseline",
            ):
                manage.apply(
                    7,
                    {"title": "Bounded slice", "body": self.bound("Stale list scope")},
                    "REWRITE",
                    "Manager replacement without a binding",
                    None,
                    self.packet,
                )
        self.assertEqual(self.calls, [])

    def test_manager_edit_requires_a_complete_fresh_ticket_body(self) -> None:
        for fresh in ({}, {"body": 17}, []):
            with self.subTest(fresh=fresh), mock.patch.object(
                dispatch, "gh_json", return_value=fresh
            ), mock.patch.object(dispatch, "run", side_effect=self.run_command):
                with self.assertRaisesRegex(
                    binding.BindingError,
                    "ticket body refresh is incomplete",
                ):
                    manage.apply(
                        7,
                        {"title": "Legacy ticket", "body": "stale body"},
                        "REWRITE",
                        "replacement",
                        None,
                        self.packet,
                    )
            self.assertEqual(self.calls, [])

    def test_rewrite_keeps_binding_and_uses_refreshed_body_for_receipt(self) -> None:
        self.accept()
        live_body = self.bound("Latest live scope")
        replacement = self.bound("Manager-refined scope")
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Bounded slice", "body": live_body},
        ) as refresh, mock.patch.object(dispatch, "run", side_effect=self.run_command):
            manage.apply(
                7,
                {"title": "Bounded slice", "body": self.bound("Stale list scope")},
                "REWRITE",
                replacement,
                None,
                self.packet,
            )

        refresh.assert_called_once_with(
            ["issue", "view", "7", "--repo", "acme/widgets", "--json", "title,body"]
        )
        comment, edit, relabel = self.calls
        self.assertEqual(comment[1:3], ["issue", "comment"])
        comment_body = comment[comment.index("--body") + 1]
        self.assertIn(live_body, comment_body)
        self.assertNotIn("Stale list scope", comment_body)
        self.assertEqual(edit[edit.index("--body") + 1], replacement)
        self.assertEqual(
            relabel[-4:],
            ["--remove-label", "ready-for-human", "--add-label", "ready-for-agent"],
        )
        receipt = lifecycle.read_events(dispatch.EVENTS)[-1]
        self.assertEqual(
            (receipt["event"], receipt["kind"], receipt["decision"]),
            ("comment", "manager", "REWRITE"),
        )

    def test_bound_split_inherits_binding_after_live_source_preflight(self) -> None:
        self.accept()
        live_body = self.bound("Latest live scope")
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Bounded slice", "body": live_body},
        ), mock.patch.object(
            binding, "admit", return_value=self.baseline
        ) as admit, mock.patch.object(dispatch, "run", side_effect=self.run_command):
            manage.apply(
                7,
                {"title": "Bounded slice", "body": "stale list scope"},
                "SPLIT",
                "",
                [{"title": "Child", "body": "Child scope", "blocked_by": []}],
                self.packet,
            )

        admit.assert_called_once_with(self.cfg, live_body)
        create = next(command for command in self.calls if command[1:3] == ["issue", "create"])
        child_body = create[create.index("--body") + 1]
        self.assertEqual(child_body.count("Initiative: #50"), 1)
        self.assertIn(binding.render(self.baseline), child_body)
        self.assertIn("needs-triage", create)
        parent_edit = next(command for command in self.calls if command[1:3] == ["issue", "edit"])
        self.assertEqual(
            parent_edit[parent_edit.index("--body") + 1],
            live_body + "\n\nBlocked by: #80",
        )

    def test_bound_split_rejects_a_child_rebaseline_before_mutation(self) -> None:
        self.accept()
        live_body = self.bound("Latest live scope")
        replacement = copy.deepcopy(self.baseline)
        replacement["observed_at"] = "2026-09-14T13:00:00Z"
        child = {
            "title": "Child",
            "body": self.bound("Child scope", replacement),
            "blocked_by": [],
        }
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Bounded slice", "body": live_body},
        ), mock.patch.object(
            binding, "admit", return_value=self.baseline
        ), mock.patch.object(dispatch, "run", side_effect=self.run_command):
            with self.assertRaisesRegex(
                binding.BindingError,
                "child must inherit the parent's accepted Initiative and Plan baseline",
            ):
                manage.apply(
                    7,
                    {"title": "Bounded slice", "body": live_body},
                    "SPLIT",
                    "",
                    [child],
                    self.packet,
                )
        self.assertEqual(self.calls, [])

    def test_bound_split_rejects_unavailable_source_before_mutation(self) -> None:
        self.accept()
        live_body = self.bound("Latest live scope")
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Bounded slice", "body": live_body},
        ), mock.patch.object(
            binding,
            "admit",
            side_effect=binding.BindingError("initiative source is unavailable"),
        ), mock.patch.object(dispatch, "run", side_effect=self.run_command):
            with self.assertRaisesRegex(
                binding.BindingError,
                "SPLIT refused before mutation: initiative source is unavailable",
            ):
                manage.apply(
                    7,
                    {"title": "Bounded slice", "body": live_body},
                    "SPLIT",
                    "",
                    [{"title": "Child", "body": "Child scope", "blocked_by": []}],
                    self.packet,
                )
        self.assertEqual(self.calls, [])

    def test_unlinked_split_preflights_every_declared_child_before_creating_any(self) -> None:
        declared = self.bound("Linked child scope")
        with mock.patch.object(
            dispatch,
            "gh_json",
            return_value={"title": "Legacy parent", "body": "Legacy parent scope"},
        ), mock.patch.object(
            binding,
            "admit",
            side_effect=binding.BindingError("initiative source is incomplete"),
        ) as admit, mock.patch.object(dispatch, "run", side_effect=self.run_command):
            with self.assertRaisesRegex(
                binding.BindingError,
                "SPLIT child 2 is not intake-ready: initiative source is incomplete",
            ):
                manage.apply(
                    7,
                    {"title": "Legacy parent", "body": "stale parent scope"},
                    "SPLIT",
                    "",
                    [
                        {"title": "Legacy child", "body": "Legacy child scope", "blocked_by": []},
                        {"title": "Linked child", "body": declared, "blocked_by": []},
                    ],
                    self.packet,
                )

        admit.assert_called_once_with(self.cfg, declared)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
