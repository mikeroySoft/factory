"""Release transitions preserve claim evidence before reopening admission."""
from __future__ import annotations

import copy
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from tests import test_host_claims as fixture


class ClaimReleaseProtocolTest(unittest.TestCase):
    setUp = fixture.HostClaimRaceTest.setUp
    dispatcher = fixture.HostClaimRaceTest.dispatcher
    launch = fixture.HostClaimRaceTest.launch

    @staticmethod
    def nonce(comment):
        return comment["body"].split("nonce=", 1)[1].split()[0]

    @staticmethod
    def posted_body(args):
        if (args[:1] != ["api"] or "--method" not in args
                or args[args.index("--method") + 1] != "POST"):
            return None
        return next((arg[5:] for arg in args if arg.startswith("body=")), None)

    def expired_claim(self, host, *, retained=False):
        claim = self.github.add_claim(host, datetime.now(UTC) - timedelta(hours=3))
        if retained:
            claim["body"] += "\nFactory claim history: retained"
        return copy.deepcopy(claim)

    def install_requeue_interleaving(
        self, releaser, admitter, original, *, before_acquire=None, fail_receipt=False,
    ):
        self.github.open_prs = [{"number": 70}]
        self.assertEqual(admitter.frontier()[0].get("held"), "open PR")
        state = {}
        gh_json = releaser.gh_json

        def intercepted(args):
            body = self.posted_body(args)
            if fail_receipt and body and body.startswith("Factory claim released:"):
                raise subprocess.CalledProcessError(1, args, stderr="receipt refused")
            result = gh_json(args)
            if body and body.startswith("Factory claim released:"):
                state["held_after_receipt"] = admitter.frontier()[0].get("held")
            if not body or not body.startswith("Factory requeue:") or "replacement" in state:
                return result

            claims = admitter.host_claims(7)
            source = next((row for row in claims if row["id"] == original["id"]), None)
            state.update(
                marker=body,
                source_at_admission=source is not None,
                receipt_at_admission=bool(source and source.get("released")),
            )
            if before_acquire is not None:
                before_acquire(state)
            ready = admitter.frontier()[0]
            state["hold_at_admission"] = ready.get("held")
            replacement = admitter.acquire_host_claim(7, ready, claims)
            self.assertIsNotNone(replacement)
            state["replacement"] = replacement
            self.addCleanup(replacement.stop)
            self.addCleanup(admitter._HOST_CLAIMS.pop, 7, None)
            admitter.run([
                "gh", "issue", "edit", "7", "--repo", admitter.REPO,
                "--add-assignee", "@me",
            ])
            admitter.release_stale_claims(
                7, copy.deepcopy(self.github.issue), admitter.host_claims(7),
                replacement=replacement.comment["id"],
            )
            return result

        self.enterContext(mock.patch.object(releaser, "gh_json", side_effect=intercepted))
        return state

    def assert_original_survives(self, original):
        self.assertTrue(any(row["id"] == original["id"] for row in self.github.comments))
        self.assertFalse(any(
            host_command[1][:2] == ["gh", "api"]
            and "DELETE" in host_command[1]
            and host_command[1][2].endswith(f"/{original['id']}")
            for host_command in self.github.calls
        ))

    def assert_preserved_interleaving(self, state, original):
        self.assertEqual(state.get("held_after_receipt"), "open PR")
        self.assertTrue(state["receipt_at_admission"])
        self.assertIsNone(state["hold_at_admission"])
        self.assert_original_survives(original)

    def test_dashboard_foreign_release_publishes_receipt_before_ordinary_requeue(self):
        tank, thor = self.dispatcher("tank"), self.dispatcher("thor")
        original = self.expired_claim("ultron")
        state = self.install_requeue_interleaving(tank, thor, original)

        tank.release_expired_claim(7, reason="dashboard release_claim")

        self.assert_preserved_interleaving(state, original)
        self.assertEqual(
            state["marker"], f"Factory requeue: nonce={self.nonce(original)}\n",
        )
        self.assertNotIn("Factory claim history: retained", state["marker"])

    def test_dashboard_release_of_retained_takeover_has_receipt_but_no_retained_marker(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        tank.cfg.land_host = "tank"
        original = self.expired_claim("tank", retained=True)

        def exercise_resume_guard(state):
            with tank.landing_host_claim(
                7, None, dry_run=False, pr=70, head="a" * 40,
            ) as owned:
                self.assertTrue(owned)
                state["resumed"] = 7 in tank._HOST_CLAIMS

        state = self.install_requeue_interleaving(
            tank, ultron, original, before_acquire=exercise_resume_guard,
        )
        tank.release_expired_claim(7, reason="dashboard release_claim")

        self.assert_preserved_interleaving(state, original)
        self.assertFalse(state["resumed"])
        self.assertEqual(
            state["marker"], f"Factory requeue: nonce={self.nonce(original)}\n",
        )
        self.assertNotIn("Factory claim history: retained", state["marker"])

    def test_land_host_takeover_publishes_receipt_before_retained_requeue(self):
        tank, thor = self.dispatcher("tank"), self.dispatcher("thor")
        original = self.expired_claim("ultron")
        state = self.install_requeue_interleaving(tank, thor, original)

        tank.release_expired_claim(
            7, reason="land-host takeover", expected_nonce=self.nonce(original),
        )

        self.assert_preserved_interleaving(state, original)
        self.assertEqual(
            state["marker"],
            f"Factory requeue: nonce={self.nonce(original)}\n"
            "Factory claim history: retained\n",
        )

    def test_retained_unclaim_publishes_receipt_before_requeue(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        original = self.expired_claim("tank", retained=True)
        state = self.install_requeue_interleaving(tank, ultron, original)

        tank.unclaim(7, "--add-label", "ready-for-agent")

        self.assert_preserved_interleaving(state, original)
        self.assertEqual(
            state["marker"],
            f"Factory requeue: nonce={self.nonce(original)}\n"
            "Factory claim history: retained\n",
        )

    def test_ordinary_unclaim_requeue_remains_ephemeral_during_interleaved_admission(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        original = self.expired_claim("tank")
        state = self.install_requeue_interleaving(tank, ultron, original)

        tank.unclaim(7, "--add-label", "ready-for-agent")

        self.assertFalse(state["receipt_at_admission"])
        self.assertIsNone(state["hold_at_admission"])
        self.assertFalse(any(row["id"] == original["id"] for row in self.github.comments))
        self.assertNotIn("Factory claim history: retained", state["marker"])

    def test_receipt_post_failure_prevents_requeue_and_cross_host_admission(self):
        tank, thor = self.dispatcher("tank"), self.dispatcher("thor")
        original = self.expired_claim("ultron")
        state = self.install_requeue_interleaving(
            tank, thor, original, fail_receipt=True,
        )

        with self.assertRaises(subprocess.CalledProcessError):
            tank.release_expired_claim(7, reason="dashboard release_claim")

        self.assertNotIn("replacement", state)
        self.assertFalse(any(
            row["body"].startswith("Factory requeue:") for row in self.github.comments
        ))
        self.assertEqual(
            [row["id"] for row in self.github.comments if row["body"].startswith("Factory claim:")],
            [original["id"]],
        )
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])


if __name__ == "__main__":
    unittest.main()
