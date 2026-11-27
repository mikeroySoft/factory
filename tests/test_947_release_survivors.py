"""Behavioral regressions for #947 release-boundary mutation survivors."""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_947_manager_liveness as liveness_fixture


class ReleaseBoundarySurvivorTest(unittest.TestCase):
    setUp = liveness_fixture.QueuedAuthorizationLivenessTest.setUp
    github = liveness_fixture.QueuedAuthorizationLivenessTest.github

    def request(self, round_number: int = 1) -> dict:
        return next(
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "manager-request" and row.get("round") == round_number
        )

    def receipt(self, comment: int, round_number: int) -> None:
        self.assertTrue(dispatch.comment_receipt(
            7,
            "escalation",
            f"https://github.com/acme/widgets/issues/7#issuecomment-{comment}",
            round=round_number,
        ))

    def timeline_between_markers(self, request: dict, old: int, new: int) -> None:
        at = request["at"]
        actor = {"login": "factory-bot"}
        self.timeline[:] = [
            {"event": "commented", "id": old, "created_at": at, "updated_at": at, "actor": actor},
            {"event": "unassigned", "created_at": at, "actor": actor},
            {"event": "labeled", "label": {"name": config.LABEL_AGENT},
             "created_at": at, "actor": actor},
            {"event": "commented", "id": new, "created_at": at, "updated_at": at, "actor": actor},
        ]


    def test_release_scan_finds_an_exact_marker_between_comment_receipts(self) -> None:
        request = self.request()
        self.receipt(101, request["round"])
        dispatch.record(
            "manager-request-release", ticket=7,
            packet=request["packet"], round=request["round"],
        )
        self.receipt(102, request["round"])
        self.timeline_between_markers(request, 101, 102)

        with mock.patch.object(dispatch, "gh_json", side_effect=self.github):
            self.assertFalse(manage.human_activity(
                7, request, lifecycle.read_events(dispatch.EVENTS),
            ))


    def test_failed_pending_release_is_not_journaled_as_successful(self) -> None:
        request = self.request()

        def fail_edit(command: list[str], *args, check: bool = False, **kwargs):
            result = subprocess.CompletedProcess(command, 1, "", "label update failed")
            if check:
                raise subprocess.CalledProcessError(
                    result.returncode, command, stderr=result.stderr,
                )
            return result

        with mock.patch.object(manage, "_ensure_request_comment", return_value=True), \
                mock.patch.object(dispatch, "run", side_effect=fail_edit), \
                mock.patch.object(dispatch, "log"):
            manage._handle_add_only_request(7, self.issue, request)

        self.assertFalse(any(
            row.get("event") == "manager-request-release"
            for row in lifecycle.read_events(dispatch.EVENTS)
        ))


if __name__ == "__main__":
    unittest.main()
