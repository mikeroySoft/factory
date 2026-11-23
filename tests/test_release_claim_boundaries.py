"""Consumer-visible boundaries of the confirmed expired-claim release."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import decisions
from tests import test_host_claims as fixture


class ReleaseClaimBoundaryTest(unittest.TestCase):
    dispatcher = fixture.HostClaimRaceTest.dispatcher
    launch = fixture.HostClaimRaceTest.launch

    def setUp(self):
        fixture.HostClaimRaceTest.setUp(self)
        self.module = self.dispatcher("tank")
        self.github.issue.update(
            updatedAt="2026-11-20T00:00:00Z",
            url="https://github.com/local/claims/issues/7",
        )
        self.claim = copy.deepcopy(
            self.github.add_claim("ultron", datetime.now(UTC) - timedelta(hours=3)),
        )
        self.provider_result = None
        self.enterContext(mock.patch.object(decisions, "_run", side_effect=self.transport))

    @property
    def nonce(self):
        return self.claim["body"].split("nonce=", 1)[1].split()[0]

    def transport(self, argv, **_kwargs):
        if argv[:2] == ["gh", "api"]:
            value = "alice\n"
        elif argv[:3] == ["gh", "issue", "view"]:
            value = json.dumps(self.github.issue)
        elif "-c" in argv:
            mode, nonce = argv[-3:-1]
            if mode == "preview" and self.provider_result is not None:
                value = json.dumps(self.provider_result)
            else:
                try:
                    claim = self.module.release_expired_claim(
                        7, dry_run=mode == "preview", expected_nonce=nonce or None,
                        reason="dashboard release_claim",
                    )
                except ValueError as exc:
                    return subprocess.CompletedProcess(
                        argv, int(mode == "apply"), json.dumps({"error": str(exc)}), "",
                    )
                value = json.dumps({
                    "claim": {key: claim[key] for key in ("id", "host", "nonce", "at")},
                })
        else:
            return self.github.run("tank", argv)
        return subprocess.CompletedProcess(argv, 0, value, "")

    def json_request(self, op="release_claim"):
        return json.loads(f'{{"op":"{op}","number":7}}')

    def mutation_calls(self):
        return [
            command for _, command in self.github.calls
            if command[:3] == ["gh", "issue", "edit"]
            or ("--method" in command and command[command.index("--method") + 1] == "POST")
        ]

    def test_json_release_preview_and_apply_have_exact_effects(self):
        before = copy.deepcopy(self.github.issue)
        preview = decisions.prepare(
            self.module.cfg, [self.json_request()], caller="dashboard",
        )

        self.assertEqual(preview["requests"], [{"op": "release_claim", "number": 7}])
        effect = preview["effects"][0]
        self.assertEqual(effect["target"], {"kind": "release_claim", "number": 7})
        self.assertIn("remove label host:ultron", effect["changes"])
        self.assertEqual(self.github.issue, before)
        self.assertEqual(self.github.comments, [self.claim])

        result = decisions.apply(
            self.module.cfg, preview["proposal_id"], preview["confirmation"],
        )

        self.assertEqual(result["status"], "success")
        expected = copy.deepcopy(before)
        expected["labels"] = [
            row for row in expected["labels"] if row["name"] != "host:ultron"
        ]
        self.assertEqual(self.github.issue, expected)
        self.assertEqual(self.github.comments[0], self.claim)
        self.assertEqual(
            self.github.comments[1]["body"],
            f"Factory requeue: nonce={self.nonce}\nFactory claim history: retained\n",
        )
        self.assertFalse(any("DELETE" in command for _, command in self.github.calls))

    def test_closed_and_invalid_provider_states_fail_closed(self):
        self.github.issue["state"] = "CLOSED"
        with self.assertRaises(decisions.DecisionError) as closed:
            decisions.prepare(self.module.cfg, [self.json_request()], caller="dashboard")
        self.assertEqual(closed.exception.code, "precondition_failed")

        self.github.issue["state"] = None
        with self.assertRaises(decisions.DecisionError) as invalid:
            decisions.prepare(self.module.cfg, [self.json_request()], caller="dashboard")
        self.assertEqual(invalid.exception.code, "provider_unavailable")
        with self.assertRaisesRegex(ValueError, "ticket is not open"):
            self.module.release_expired_claim(7, dry_run=True)

        self.github.issue["state"] = json.loads('"OPEN"')
        claim = self.module.release_expired_claim(7, dry_run=True)
        self.assertEqual(claim["nonce"], self.nonce)
        self.assertEqual(self.mutation_calls(), [])

    def test_malformed_claim_provider_responses_fail_closed(self):
        malformed = [
            None,
            {"id": 1, "host": "ultron", "nonce": self.nonce,
             "at": "2026-01-01T00:00:00+00:00", "unexpected": True},
        ]
        for claim in malformed:
            with self.subTest(claim=claim):
                self.provider_result = {"claim": claim}
                with self.assertRaises(decisions.DecisionError) as refused:
                    decisions.prepare(
                        self.module.cfg, [self.json_request()], caller="dashboard",
                    )
                self.assertEqual(refused.exception.code, "provider_unavailable")
        self.assertEqual(self.mutation_calls(), [])

    def test_second_authenticated_read_failure_is_a_closed_refusal(self):
        def change_provider_identity(_number):
            self.github.comments[0]["user"] = {"login": "other-bot"}
            self.github.comments[0]["author_association"] = "MEMBER"
            return ""

        with mock.patch.object(
            self.module, "refresh_requeue_context", side_effect=change_provider_identity,
        ), self.assertRaisesRegex(ValueError, "does not match dispatcher login"):
            self.module.release_expired_claim(7)

        self.assertEqual(self.mutation_calls(), [])
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])

    def test_ticket_state_or_owner_change_during_release_is_refused(self):
        issue = copy.deepcopy(self.github.issue)
        comments = copy.deepcopy(self.github.comments)
        next_id = self.github.next_id
        cases = [
            ("state", "CLOSED", "ticket is not open"),
            ("assignees", [{"login": "human"}], "ticket has a human assignee"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field):
                self.github.issue = copy.deepcopy(issue)
                self.github.comments = copy.deepcopy(comments)
                self.github.next_id = next_id
                self.github.calls.clear()

                def change_ticket(_number, field=field, value=value):
                    self.github.issue[field] = value
                    return ""

                with mock.patch.object(
                    self.module, "refresh_requeue_context", side_effect=change_ticket,
                ), self.assertRaisesRegex(ValueError, message):
                    self.module.release_expired_claim(7)

                self.assertEqual(self.mutation_calls(), [])
                self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])

    def test_changed_election_during_release_is_refused_atomically(self):
        replacement = "00000000-0000-4000-8000-000000000000"

        def change_election(_number):
            self.github.comments[0]["body"] = self.github.comments[0]["body"].replace(
                self.nonce, replacement,
            )
            return ""

        with mock.patch.object(
            self.module, "refresh_requeue_context", side_effect=change_election,
        ), self.assertRaisesRegex(ValueError, "elected host claim changed"):
            self.module.release_expired_claim(7, expected_nonce=self.nonce)

        self.assertEqual(self.mutation_calls(), [])
        self.assertIn({"name": "host:ultron"}, self.github.issue["labels"])

    def test_existing_dispatcher_context_is_retained_in_replacement_marker(self):
        context = "## Recovery context from the foreign dispatcher\n\nkeep this evidence\n"
        old_marker = {
            "id": self.github.next_id,
            "body": f"Factory requeue: nonce={self.nonce}\n{context}",
            "created_at": datetime.now(UTC).isoformat(),
            "user": {"login": "@me"},
        }
        self.github.next_id += 1
        self.github.comments.append(old_marker)

        released = self.module.release_expired_claim(7)

        self.assertEqual(released["nonce"], self.nonce)
        self.assertIn(self.claim, self.github.comments)
        self.assertIn(old_marker, self.github.comments)
        self.assertEqual(
            self.github.comments[-1]["body"],
            f"Factory requeue: nonce={self.nonce}\n"
            f"Factory claim history: retained\n{context}",
        )

    def test_triage_observation_is_not_reported_as_claim_release(self):
        preview = decisions.prepare(
            self.module.cfg, [self.json_request("triage")], caller="dashboard",
        )

        receipt = decisions.receipt(self.module.cfg, preview["proposal_id"], observe=True)

        self.assertEqual(receipt["status"], "prepared")
        [postcondition] = receipt["observation"]["postconditions"]
        self.assertEqual(postcondition["status"], "unknown")
        evidence = " ".join(postcondition["evidence"])
        self.assertIn("triage", evidence)
        self.assertNotIn("claim", evidence)
