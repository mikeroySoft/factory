"""Owning-host recovery of escalations whose public marker was lost."""
import subprocess
from datetime import UTC, datetime, timedelta
from unittest import TestCase, mock

from tests.test_host_claims import HostClaimRaceTest


class DeadClaimRequeueTest(TestCase):
    setUp = HostClaimRaceTest.setUp
    dispatcher = HostClaimRaceTest.dispatcher
    launch = HostClaimRaceTest.launch

    def prepare(self):
        module = self.dispatcher("tank")
        raw = self.github.add_claim("tank", datetime.now(UTC) - timedelta(hours=3))
        nonce = raw["body"].split("nonce=", 1)[1].split()[0]
        module.record("claimed", ticket=7, claim_nonce=nonce, execution_id="dead")
        self.github.open_prs = [{"number": 954}]
        record = module.record
        self.enterContext(mock.patch.object(
            module, "record", side_effect=lambda event, **fields:
            record(event, **({"execution_id": "dead"} | fields))
        ))
        # Model failed cleanup too: the dead elected claim remains on GitHub.
        with mock.patch.object(module, "unclaim"), mock.patch.object(
            module, "escalation_packet", return_value=(module.FACTORY / "packet.md", 1)
        ), mock.patch.object(module, "run", return_value=subprocess.CompletedProcess(
            [], 1, "", "GraphQL: API rate limit already exceeded"
        )):
            module.escalate(7, "review failed", None, code="gh_failed")
        module._ESCALATED.clear()
        return module, nonce

    def test_failed_escalation_marker_retries_until_claimable(self):
        module, nonce = self.prepare()
        original = module.gh_json
        attempts = []

        def post(args):
            if "POST" in args:
                attempts.append(args)
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(1, args, stderr="rate limit")
            return original(args)

        with mock.patch.object(module, "gh_json", side_effect=post):
            self.assertEqual(module.frontier()[0].get("held"), "open PR")
            self.assertNotIn("held", module.frontier()[0])
            self.assertNotIn("held", module.frontier()[0])
        self.assertEqual(len(attempts), 2)
        self.assertTrue(any(row["body"].startswith(f"Factory requeue: nonce={nonce}")
                            for row in self.github.comments))

    def test_owner_generation_supersedes_only_local_dead_claim(self):
        module, _ = self.prepare()
        module.record("generation", ticket=7, generation=1, intent="resume",
                      cause="owner_requeue", source="requeue_agent")
        claims = module.host_claims(7)
        with mock.patch.object(module, "gh_json", side_effect=AssertionError("no PR read needed")):
            self.assertIsNone(module.host_claim_hold(7, self.github.issue, claims))

    def test_foreign_live_claim_still_holds(self):
        module, _ = self.prepare()
        module.record("generation", ticket=7, generation=1, intent="resume",
                      cause="owner_requeue", source="requeue_agent")
        self.github.add_claim("ultron", datetime.now(UTC))
        self.assertEqual(module.host_claim_hold(7, self.github.issue, module.host_claims(7)),
                         "host claim (ultron)")
