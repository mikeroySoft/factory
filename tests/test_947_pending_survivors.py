"""Consumer regression for #947's pending authorization scan."""
from __future__ import annotations

import unittest
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_authorization_request as authorization_fixture


class PendingAuthorizationScanTest(unittest.TestCase):
    setUp = authorization_fixture.ManagerAuthorizationRequestTest.setUp
    command_result = authorization_fixture.ManagerAuthorizationRequestTest.command_result


    def test_persisted_completed_latest_request_is_not_pending_after_prior_round(self) -> None:
        for round_number in (1, 2):
            packet = self.cfg.factory / f"request-{round_number}.md"
            packet.write_text("authorization evidence")
            dispatch.record(
                "manager-request", ticket=7, code="add_only_authorization",
                paths=self.paths, packet=str(packet), log=None,
                round=round_number, exhausted=False,
            )
            dispatch.record(
                "manager-request-complete", ticket=7,
                packet=str(packet), round=round_number,
            )

        self.assertFalse(manage.pending_add_only_request(7))


    def test_human_override_completes_keyword_ticket_request_and_releases_worker(self) -> None:
        packet = self.cfg.factory / "override-request.md"
        packet.write_text("authorization evidence")
        dispatch.record(
            "manager-request", ticket=7, code="add_only_authorization",
            paths=self.paths, packet=str(packet), log=None, round=1, exhausted=False,
        )

        with mock.patch.object(dispatch, "run", side_effect=self.command_result):
            pending = manage.pending_add_only_request(
                n=7, labels={config.LABEL_ADD_ONLY_OVERRIDE},
            )

        self.assertFalse(pending)
        completed = [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == "manager-request-complete"
        ]
        self.assertEqual(
            [(row["packet"], row["round"], row["reason"]) for row in completed],
            [(str(packet), 1, "human_authorized")],
        )
        self.assertTrue(any(
            command[:4] == ["gh", "issue", "edit", "7"]
            and config.LABEL_AGENT in command
            for command in self.commands
        ))

if __name__ == "__main__":
    unittest.main()
