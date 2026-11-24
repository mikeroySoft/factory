"""Confirmed dashboard claim release uses the dispatcher's expiry policy."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import actions, config, decisions


class ReleaseClaimTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cfg = config.Config(root=root, repo="acme/widgets")
        self.claim = {"id": 31, "nonce": "old-owner", "host": "remote", "at": "2026-01-01T00:00:00+00:00"}
        self.refusal = None
        self.mutations = []
        patch = mock.patch.object(decisions, "_run", side_effect=self.transport)
        patch.start()
        self.addCleanup(patch.stop)

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"]:
            value = "alice\n"
        elif argv[:3] == ["gh", "issue", "view"]:
            value = json.dumps({"number": 7, "title": "Fix", "body": "", "state": "OPEN", "labels": [], "assignees": [], "updatedAt": "fixed", "url": "https://github.com/acme/widgets/issues/7"})
        elif "-c" in argv:
            value = json.dumps({"error": self.refusal} if self.refusal else {"claim": self.claim})
            if "apply" in argv:
                self.mutations.append(argv)
        else:
            self.mutations.append(argv)
            value = "ok"
        return subprocess.CompletedProcess(argv, 0, value, "")

    def test_expired_claim_release_is_confirmed_and_replay_safe(self):
        preview = actions.prepare(self.cfg, "release_claim", {"number": 7, "comment": "recover host"}, caller="dashboard")
        self.assertEqual(preview["requests"], [{"op": "release_claim", "number": 7, "comment": "recover host"}])
        self.assertEqual(self.mutations, [])
        result = decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(self.mutations), 2)
        self.assertIn("old-owner", self.mutations[0])
        self.assertEqual(self.mutations[1][-1], "recover host")
        self.assertEqual(decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"]), result)
        self.assertEqual(len(self.mutations), 2)

    def test_live_claim_refused_with_reason(self):
        self.refusal = "live host claim (remote)"
        with self.assertRaisesRegex(decisions.DecisionError, "live host claim"):
            actions.prepare(self.cfg, "release_claim", {"number": 7}, caller="dashboard")
        self.assertEqual(self.mutations, [])

    def test_changed_claim_refuses_before_mutation(self):
        preview = actions.prepare(self.cfg, "release_claim", {"number": 7}, caller="dashboard")
        self.claim = {**self.claim, "nonce": "replacement"}
        with self.assertRaisesRegex(decisions.DecisionError, "target changed"):
            decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
        self.assertEqual(self.mutations, [])

    def test_claim_renewed_after_preview_refuses_before_mutation(self):
        preview = actions.prepare(self.cfg, "release_claim", {"number": 7}, caller="dashboard")
        self.refusal = "live host claim (remote)"
        with self.assertRaisesRegex(decisions.DecisionError, "live host claim"):
            decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
        self.assertEqual(self.mutations, [])

    def test_release_operation_rejects_unrelated_mutations(self):
        with self.assertRaisesRegex(decisions.DecisionError, "fields are closed"):
            decisions.prepare(self.cfg, [{"op": "release_claim", "number": 7, "add": ["other"]}])
        self.assertEqual(self.mutations, [])

    def test_dispatcher_failure_is_durable_uncertain_receipt_not_retried(self):
        preview = actions.prepare(self.cfg, "release_claim", {"number": 7}, caller="dashboard")
        transport = self.transport

        def failure(argv, **kwargs):
            if "-c" in argv and "apply" in argv:
                self.mutations.append(argv)
                return subprocess.CompletedProcess(argv, 1, "", "dispatcher transport failed")
            return transport(argv, **kwargs)

        with mock.patch.object(decisions, "_run", side_effect=failure):
            result = decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"])
            self.assertEqual(result["status"], "uncertain")
            self.assertIn("dispatcher transport failed", result["steps"][0]["output"])
            self.assertEqual(decisions.apply(self.cfg, preview["proposal_id"], preview["confirmation"]), result)
        self.assertEqual(len(self.mutations), 1)
