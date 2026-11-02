"""#488: live owner decisions win over in-flight dispatcher transitions."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from factory import config, dispatch, handoff, lifecycle, manage

CLAIM = "2026-10-30T17:00:00Z"
APPROVAL = "2026-10-30T17:05:00Z"
LATER = "2026-10-30T17:06:00Z"
HEAD = "a" * 40


class OwnerStateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(Path(temp.name), "acme/widgets", signoff=False)
        dispatch.configure(self.cfg)
        self.labels = {config.LABEL_AGENT}
        self.assignees = {"factory"}
        self.comments = []
        self.posted = []
        self.commands = []
        self.timeline = []
        self.pr = {
            "number": 70, "state": "OPEN", "headRefName": "agent/7", "headRefOid": HEAD,
            "baseRefName": "main", "isDraft": False, "title": "change",
            "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
        }
        for name, value in (("gh_json", self.github), ("run", self.run_command)):
            patch = mock.patch.object(dispatch, name, side_effect=value)
            patch.start()
            self.addCleanup(patch.stop)
        dispatch.record("claimed", ticket=7, at=CLAIM)

    def github(self, args):
        if args[:2] == ["issue", "view"]:
            return {"number": 7, "state": "OPEN", "body": "", "comments": list(self.comments),
                    "labels": [{"name": label} for label in self.labels]}
        if args[:2] == ["issue", "list"]:
            return [{"number": 7}] if config.LABEL_HUMAN in self.labels else []
        if args[:2] == ["pr", "list"]:
            return [self.pr]
        if args[:2] == ["pr", "view"]:
            return self.pr
        if args[0] == "api" and "/compare/" in args[1]:
            return {"behind_by": 0}
        if args[0] == "api" and args[1].endswith("/timeline"):
            return [self.timeline]
        raise AssertionError(args)

    def run_command(self, args, **kwargs):
        self.commands.append(args)
        if args[:3] == ["gh", "issue", "edit"]:
            for i, arg in enumerate(args):
                if arg == "--add-label":
                    self.labels.add(args[i + 1])
                elif arg == "--remove-label":
                    self.labels.discard(args[i + 1])
                elif arg == "--remove-assignee":
                    self.assignees.discard("factory")
        if args[:3] == ["gh", "issue", "comment"]:
            self.posted.append(args[args.index("--body") + 1])
            return subprocess.CompletedProcess(args, 0, f"https://github.com/acme/widgets/issues/7#issuecomment-{len(self.posted)}", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def decision(self, body="Hold this ticket.", at=LATER, login="acme"):
        self.comments.append({"body": f"Owner decision (Michael, 2026-10-30): {body}",
                              "author": {"login": login}, "createdAt": at})

    def held(self, at=LATER):
        self.labels.add("factory-held")
        self.timeline.append({"event": "labeled", "label": {"name": "factory-held"}, "created_at": at})

    def escalate(self):
        dispatch.escalate(7, "rounds exhausted", None, code="review_revise")
        with mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(handoff, "observe", return_value={
                    "route": {"status": "unassigned", "owner": None, "candidates": [],
                              "source": None, "provenance": []}, "pr_url": None,
                }):
            handoff.handoff_pass()

    def seed_approval(self):
        dispatch.record("attempt", ticket=7, head=HEAD, actual_head=HEAD, gate="PASS")
        dispatch.record("review", ticket=7, head=HEAD, actual_head=HEAD,
                        verdict="APPROVE", accepted=True)
        dispatch.record("approved", ticket=7, pr=70, head=HEAD, gate_head=HEAD,
                        review_head=HEAD, at=APPROVAL)

    def merge(self, *, seed=True):
        if seed:
            self.seed_approval()
        with mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "cleanup_after_merge"), \
                mock.patch("factory.learn.promote_pass"):
            dispatch.merge_pass_locked(False)
        return any(cmd[:3] == ["gh", "pr", "merge"] for cmd in self.commands)

    def test_owner_held_mid_pass_suppresses_escalation_and_handoff(self):
        self.held()
        self.escalate()
        self.assertEqual(self.labels, {config.LABEL_AGENT, "factory-held"})
        self.assertEqual(self.posted, [])
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertTrue(any(row.get("ticket") == 7 and row.get("code") == "review_revise" for row in rows))
        self.assertFalse(any(row.get("event") == "escalate" for row in rows))

    def test_owner_decision_after_claim_suppresses_escalation_and_handoff(self):
        self.decision("Keep the current labels; I will decide the next step.")
        self.escalate()
        self.assertEqual(self.labels, {config.LABEL_AGENT})
        self.assertEqual(self.posted, [])

    def test_no_new_owner_decision_still_escalates_and_hands_off(self):
        self.decision(at="2026-10-30T16:59:00Z")
        self.escalate()
        self.assertEqual(self.labels, {config.LABEL_HUMAN})
        self.assertTrue(any("Factory dispatcher escalating" in body for body in self.posted))
        self.assertTrue(any("Factory handoff request" in body for body in self.posted))

    def test_foreign_owner_marker_does_not_suppress_escalation(self):
        self.decision(login="stranger")
        self.escalate()
        self.assertIn(config.LABEL_HUMAN, self.labels)

    def test_handoff_rechecks_owner_after_escalation(self):
        dispatch.escalate(7, "rounds exhausted", None, code="review_revise")
        self.held()
        with mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(handoff, "observe", return_value=None) as observe:
            handoff.handoff_pass()
        observe.assert_not_called()
        self.assertFalse(any("Factory handoff" in body for body in self.posted))

    def test_stale_human_label_allows_merge_and_is_cleared_once(self):
        self.labels = {config.LABEL_HUMAN, "priority"}
        self.assertTrue(self.merge())
        self.assertEqual(self.labels, {"priority"})
        notes = [body for body in self.posted if "resolved the escalation without a human" in body]
        self.assertEqual(len(notes), 1)
        self.assertIn("merged", notes[0])

    def test_newer_owner_hold_blocks_merge_without_label_cleanup(self):
        self.labels = {config.LABEL_HUMAN}
        self.held()
        self.assertFalse(self.merge())
        self.assertEqual(self.labels, {config.LABEL_HUMAN, "factory-held"})
        self.assertEqual(self.posted, [])

    def test_newer_owner_no_merge_comment_blocks_merge(self):
        self.decision("Do not merge this ticket.")
        self.assertFalse(self.merge())
        self.assertEqual(self.posted, [])

    def test_old_owner_hold_and_new_nonblocking_decision_do_not_veto_approval(self):
        self.decision("Do not merge yet.", at=CLAIM)
        self.decision("Proceed with the approved change.")
        self.assertTrue(self.merge())

    def test_approval_resolves_human_gate_once(self):
        self.labels = {config.LABEL_HUMAN, "priority"}
        self.seed_approval()
        with mock.patch.object(dispatch, "retain_handoff"):
            self.assertTrue(dispatch.approve_pr(7, HEAD))
            self.assertTrue(dispatch.approve_pr(7, HEAD))
        self.assertEqual(self.labels, {"priority"})
        notes = [body for body in self.posted if "resolved the escalation without a human" in body]
        self.assertEqual(len(notes), 1)
        self.assertIn("approved", notes[0])

    def test_approval_does_not_relabel_after_owner_decision(self):
        self.seed_approval()
        self.decision("Hold the work.")
        self.labels = {"factory-held", "priority"}
        with mock.patch.object(dispatch, "retain_handoff"):
            dispatch.approve_pr(7, HEAD)
        self.assertEqual(self.labels, {"factory-held", "priority"})
        self.assertFalse(any(cmd[:3] == ["gh", "pr", "edit"] for cmd in self.commands))
        self.assertEqual(self.posted, [])

    def test_manager_requeue_resolves_human_gate_once(self):
        self.labels = {config.LABEL_HUMAN, "priority"}
        issue = self.github(["issue", "view"])
        manage.apply(7, issue, "RETRY", "Retry after the transient failure.", None, Path("packet.md"))
        self.assertEqual(self.labels, {config.LABEL_AGENT, "priority"})
        notes = [body for body in self.posted if "resolved the escalation without a human" in body]
        self.assertEqual(len(notes), 1)
        self.assertIn("RETRY", notes[0])

    def test_refresh_requeue_preserves_owner_labels_and_announces_resolution_once(self):
        self.labels = {config.LABEL_HUMAN, "priority"}
        self.assertTrue(dispatch.resolve_escalation(7, "requeued PR #70 after a refresh conflict", requeue=True))
        self.assertTrue(dispatch.resolve_escalation(7, "requeued PR #70 after a refresh conflict", requeue=True))
        self.assertEqual(self.labels, {config.LABEL_AGENT, "priority"})
        self.assertEqual(self.posted, [
            "Factory resolved the escalation without a human: requeued PR #70 after a refresh conflict.",
        ])

    def test_refresh_requeue_defers_to_a_new_owner_decision(self):
        self.labels = {config.LABEL_HUMAN, "priority"}
        self.decision()
        self.assertFalse(dispatch.resolve_escalation(7, "requeue after refresh", requeue=True))
        self.assertEqual(self.labels, {config.LABEL_HUMAN, "priority"})
        self.assertEqual(self.commands, [])

    def test_owner_hold_before_approval_does_not_block_merge(self):
        self.held(at=CLAIM)
        self.assertTrue(self.merge())
        self.assertIn("factory-held", self.labels)

    def test_multiline_owner_no_merge_comment_blocks_merge(self):
        self.decision("\nDo not merge this ticket.")
        self.assertFalse(self.merge())

    def test_unreadable_owner_state_neither_escalates_nor_merges(self):
        error = subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 502")
        github = self.github

        def unavailable(args):
            if args[:2] == ["issue", "view"]:
                raise error
            return github(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=unavailable):
            dispatch.escalate(7, "rounds exhausted", None, code="review_revise")
            self.assertFalse(self.merge())
        self.assertEqual(self.labels, {config.LABEL_AGENT})
        self.assertEqual(self.posted, [])

    def test_incomplete_owner_state_cannot_authorize_merge(self):
        github = self.github
        for issue in ({}, {"labels": []}, {"labels": [], "comments": None}):
            with self.subTest(issue=issue), mock.patch.object(
                dispatch, "gh_json",
                side_effect=lambda args, issue=issue: issue if args[:2] == ["issue", "view"] else github(args),
            ):
                self.assertFalse(self.merge())
        self.assertEqual(self.posted, [])

    def test_unrelated_later_decision_does_not_erase_a_post_approval_hold(self):
        self.decision("Do not merge this ticket.")
        self.decision("Acceptance now includes the CLI path.", at="2026-10-30T17:07:00Z")
        self.assertFalse(self.merge())


    def test_owner_hold_during_approval_io_is_newer_than_that_approval(self):
        class ApprovalClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromisoformat(APPROVAL)

        self.seed_approval()
        run_command = self.run_command

        def hold_during_label(args, **kwargs):
            result = run_command(args, **kwargs)
            if args[:3] == ["gh", "pr", "edit"] and "--add-label" in args:
                self.held()
            return result

        with mock.patch.object(dispatch, "datetime", ApprovalClock), \
                mock.patch.object(dispatch, "run", side_effect=hold_during_label), \
                mock.patch.object(dispatch, "retain_handoff"):
            self.assertTrue(dispatch.approve_pr(7, HEAD))
        self.assertFalse(self.merge(seed=False))
        self.assertIn("factory-held", self.labels)

    def test_unavailable_owner_read_releases_only_factory_assignment(self):
        self.assignees.add("owner")
        with mock.patch.object(dispatch, "gh_json", side_effect=ValueError("invalid response")):
            dispatch.escalate(7, "rounds exhausted", None, code="review_revise")
        self.assertEqual(self.assignees, {"owner"})
        self.assertEqual(self.labels, {config.LABEL_AGENT})
        self.assertEqual(self.posted, [])

    def test_owner_hold_keeps_assignment_until_owner_releases_it(self):
        self.held()
        self.escalate()
        self.assertEqual(self.assignees, {"factory"})

    def test_owner_veto_does_not_spend_refresh_conflict_rounds(self):
        self.held()

        def conflict(args, **kwargs):
            if args[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(args, 0, HEAD, "")
            if args[:2] == ["git", "rebase"] and "--abort" not in args:
                return subprocess.CompletedProcess(args, 1, "", "conflict")
            if args[:2] == ["git", "diff"]:
                return subprocess.CompletedProcess(args, 0, "conflict.txt\0", "")
            return self.run_command(args, **kwargs)

        with mock.patch.object(dispatch, "ensure_worktree", return_value=self.cfg.root), \
                mock.patch.object(dispatch, "run", side_effect=conflict):
            self.assertFalse(dispatch.refresh_pr_branch(7, 70, False))
        rows = lifecycle.read_events(dispatch.EVENTS)
        self.assertFalse(any(row.get("event") == "conflict-requeue" for row in rows))
        self.assertIn("factory-held", self.labels)

    def test_failed_resolution_note_does_not_leak_merged_worktree(self):
        from test_dispatch_lifecycle import LocalCLI

        fixture = LocalCLI("runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gh = fixture.bin / "gh"
        gh.write_text(gh.read_text().replace(
            'if s.get("sync"):',
            'if s.get("merge_ready"):\n    issue["labels"] = [{"name": "ready-for-human"}]\nif s.get("sync"):',
        ).replace(
            'elif a[:2] not in (',
            'elif a[:2] == ["issue", "comment"]:\n    print("HTTP 502", file=sys.stderr)\n    raise SystemExit(1)\nelif a[:2] not in (',
        ))
        fixture.cli("dispatch", "--ticket", "7")
        state = json.loads(fixture.state.read_text())
        state["merge_ready"] = True
        fixture.state.write_text(json.dumps(state))
        result = fixture.cli("dispatch", expected=1)
        self.assertIn("HTTP 502", result.stdout + result.stderr)
        self.assertTrue(json.loads(fixture.state.read_text()).get("merged"))
        self.assertFalse((fixture.repo / ".factory" / "wt-7").exists())
        merges = [row for row in lifecycle.read_events(fixture.events)
                  if row.get("event") == "lifecycle" and row.get("stage") == "merge-eligibility"
                  and row.get("kind") == "exit"]
        self.assertEqual(merges[-1]["outcome"], "merged")


if __name__ == "__main__":
    unittest.main()
