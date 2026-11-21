"""#875: confirmed claim release is not an implicit routing or generation decision."""
from __future__ import annotations

import copy
import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from factory import actions, config, decisions, dispatch, generation, lifecycle
from tests import test_host_claims as fixture


class ReleaseClaimSafeguardsTest(unittest.TestCase):
    setUp = fixture.HostClaimRaceTest.setUp
    dispatcher = fixture.HostClaimRaceTest.dispatcher
    launch = fixture.HostClaimRaceTest.launch

    def prepare_release(self, host="tank", owner="ultron", *, title="Shared ticket", labels=()):
        module = self.dispatcher(host)
        self.module = module
        self.github.issue.update(
            title=title, labels=[{"name": label} for label in labels],
            updatedAt="2026-11-20T00:00:00Z", url="https://github.com/local/claims/issues/7",
        )
        self.claim = copy.deepcopy(self.github.add_claim(owner, datetime.now(UTC) - timedelta(hours=3)))
        self.before = copy.deepcopy(self.github.issue)
        self.enterContext(mock.patch.object(decisions, "_run", side_effect=self.transport))
        return actions.prepare(module.cfg, "release_claim", {"number": 7}, caller="dashboard")

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"]:
            value = "alice\n"
        elif argv[:3] == ["gh", "issue", "view"]:
            value = json.dumps(self.github.issue)
        elif "-c" in argv:
            mode, nonce = argv[-3:-1]
            try:
                claim = self.module.release_expired_claim(
                    7, dry_run=mode == "preview", expected_nonce=nonce or None,
                    reason="dashboard release_claim",
                )
            except ValueError as exc:
                return subprocess.CompletedProcess(argv, int(mode == "apply"), json.dumps({"error": str(exc)}), "")
            value = json.dumps({"claim": {key: claim[key] for key in ("id", "host", "nonce", "at")}})
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, value, "")

    def apply_release(self, preview):
        result = decisions.apply(self.module.cfg, preview["proposal_id"], preview["confirmation"])
        self.assertEqual(result["status"], "success")
        return result

    def assert_release_only(self):
        self.assertIn(self.claim, self.github.comments)
        expected = copy.deepcopy(self.before)
        expected["labels"] = [row for row in expected["labels"] if row["name"] != f"host:{self.claim['body'].split('host=', 1)[1].split()[0]}"]
        self.assertEqual(self.github.issue, expected)
        edits = [cmd for _, cmd in self.github.calls if cmd[:3] == ["gh", "issue", "edit"]]
        self.assertEqual(len(edits), 1)
        self.assertNotIn("--add-label", edits[0])
        self.assertNotIn("--remove-assignee", edits[0])
        nonce = self.claim["body"].split("nonce=", 1)[1].split()[0]
        releases = [row for row in self.github.comments if row["body"].startswith(f"Factory requeue: nonce={nonce}\n")]
        self.assertEqual(len(releases), 1)

    def test_preview_names_exact_release_effects_and_no_routing_or_budget_changes(self):
        preview = self.prepare_release(labels=(config.LABEL_AGENT,))
        changes = "\n".join(preview["effects"][0]["changes"])
        self.assertIn("remove label host:ultron", changes)
        self.assertIn("nonce-bound", changes)
        self.assertIn("retain claim comments", changes)
        self.assertIn("preserve pipeline labels, assignees and generation budgets", changes)
        self.assertEqual(self.github.issue, self.before)
        self.assertEqual(self.github.comments, [self.claim])

    def test_upstream_sync_release_never_adds_ordinary_agent_routing(self):
        preview = self.prepare_release(title=dispatch.SYNC_TITLE + "main drifted")
        self.apply_release(preview)
        self.assert_release_only()
        self.assertNotIn({"name": config.LABEL_AGENT}, self.github.issue["labels"])

    def test_escalated_release_preserves_human_hold_and_does_not_open_generation(self):
        preview = self.prepare_release(labels=(config.LABEL_HUMAN,))
        self.module.record("escalate", ticket=7, reason="owner ruling required", code="budget")
        self.module.record("generation", ticket=7, generation=2, intent="fresh", cause="owner_requeue")
        self.module.record("escalate", ticket=7, reason="fresh generation exhausted", code="budget")
        before = generation.current(lifecycle.read_events(self.module.EVENTS), 7)
        self.apply_release(preview)
        self.assert_release_only()
        self.module.observe_owner_requeue(7, self.github.issue)
        after = generation.current(lifecycle.read_events(self.module.EVENTS), 7)
        self.assertEqual(
            [row for row in after.rows if row.get("event") == "generation"],
            [row for row in before.rows if row.get("event") == "generation"],
        )
        self.assertEqual((after.number, after.intent), (before.number, before.intent))
        self.assertIn({"name": config.LABEL_HUMAN}, self.github.issue["labels"])

    def test_same_host_release_preserves_shared_assignment_and_routing(self):
        preview = self.prepare_release(owner="tank", labels=(config.LABEL_AGENT,))
        self.apply_release(preview)
        self.assert_release_only()
        self.assertEqual(self.github.issue["assignees"], [{"login": "@me"}])
