"""#925: one landing stage considers every listed PR without bypassing safety checks."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests.test_refresh_conflict import build_fork, git


class LandDrainTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.root, self.origin = build_fork(Path(temp.name))
        dispatch.configure(config.Config(root=self.root, repo="acme/widgets", signoff=False))
        self.prs = {}
        self.merged = []
        self.checked = []
        self.commands = []
        self.real_run = dispatch.run
        self.enterContext(mock.patch("factory.learn.promote_pass"))
        self.enterContext(mock.patch.object(dispatch, "gh_json", side_effect=self.query))
        self.enterContext(mock.patch.object(dispatch, "run", side_effect=self.github_run))
        self.enterContext(mock.patch.object(dispatch, "pr_checks", side_effect=self.checks))
        self.enterContext(mock.patch.object(dispatch, "run_gate", return_value=(True, "gate passed")))
        self.enterContext(mock.patch.object(dispatch, "review", side_effect=self.review))

    def add_pr(self, n):
        git(self.origin, "checkout", "-qb", f"agent/{n}", "main")
        (self.origin / f"feature-{n}.txt").write_text(f"feature {n}\n")
        git(self.origin, "add", f"feature-{n}.txt")
        git(self.origin, "commit", "-qm", f"feature {n}")
        head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        pr = {"number": n * 10, "headRefName": f"agent/{n}", "headRefOid": head,
              "baseRefName": "main", "isDraft": False, "state": "OPEN",
              "labels": [{"name": config.LABEL_APPROVED}], "reviewDecision": "APPROVED",
              "title": f"feature {n}"}
        self.prs[pr["number"]] = pr
        self.evidence(n, pr["number"], head)
        return pr

    def evidence(self, n, pr, head):
        dispatch.record("attempt", ticket=n, gate="PASS", head=head, actual_head=head)
        dispatch.record("review", ticket=n, verdict="APPROVE", accepted=True,
                        head=head, actual_head=head)
        dispatch.record("approved", ticket=n, pr=pr, head=head, gate_head=head, review_head=head)

    def move_main(self):
        (self.origin / "main-moved.txt").write_text(f"main moved with {len(self.prs)} PRs\n")
        git(self.origin, "add", "main-moved.txt")
        git(self.origin, "commit", "-qm", "main moved")

    def query(self, args):
        if args[:2] == ["pr", "list"]:
            return [dict(pr) for pr in reversed(list(self.prs.values())) if pr["state"] == "OPEN"]
        if args[:2] == ["pr", "view"]:
            key = args[2]
            pr = next(pr for pr in self.prs.values() if pr["headRefName"] == key) if key.startswith("agent/") else self.prs[int(key)]
            head = git(self.origin, "rev-parse", pr["headRefName"])
            return {**pr, "headRefOid": head}
        if args[:2] == ["issue", "view"]:
            return {"number": int(args[2]), "state": "OPEN", "title": "feature", "body": "",
                    "labels": [{"name": config.LABEL_AGENT}], "comments": []}
        if args[0] == "api" and "/compare/" in args[1]:
            head = args[1].rsplit("...", 1)[1]
            return {"behind_by": int(git(self.origin, "rev-list", "--count", f"{head}..main"))}
        raise AssertionError(args)

    def github_run(self, cmd, *args, **kwargs):
        self.commands.append(cmd)
        if cmd[0] != "gh":
            return self.real_run(cmd, *args, **kwargs)
        if cmd[1:3] == ["pr", "merge"]:
            pr = self.prs[int(cmd[3])]
            head = git(self.origin, "rev-parse", pr["headRefName"])
            self.assertEqual(cmd[cmd.index("--match-head-commit") + 1], head)
            self.assertEqual(git(self.origin, "rev-list", "--count", f"{head}..main"), "0")
            git(self.origin, "merge", "-q", "--squash", pr["headRefName"])
            git(self.origin, "commit", "-qm", f"merge {pr['number']}")
            pr["state"] = "MERGED"
            self.merged.append(pr["number"])
        elif cmd[1:3] == ["pr", "edit"]:
            pr = self.prs[int(cmd[3])]
            labels = {label["name"] for label in pr["labels"]}
            if "--remove-label" in cmd:
                labels.discard(cmd[cmd.index("--remove-label") + 1])
            if "--add-label" in cmd:
                labels.add(cmd[cmd.index("--add-label") + 1])
            pr["labels"] = [{"name": label} for label in sorted(labels)]
        return subprocess.CompletedProcess(cmd, 0, "[]" if cmd[1] == "api" else "", "")

    def checks(self, pr, head):
        self.checked.append((pr, head))
        self.assertEqual(head, git(self.origin, "rev-parse", self.prs[pr]["headRefName"]))
        return [{"name": "ci", "bucket": "pass"}]

    def review(self, wt, n, report, head):
        dispatch.record("review", ticket=n, verdict="APPROVE", accepted=True,
                        head=head, actual_head=head)
        return "APPROVE", "independent review passed"

    def events(self, event):
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def test_two_eligible_prs_are_both_refreshed_in_one_land_stage(self):
        first, second = self.add_pr(7), self.add_pr(8)
        self.move_main()
        dispatch.land_pass(False)
        self.assertEqual({row["pr"] for row in self.events("refreshed")}, {70, 80})
        for pr in (first, second):
            refreshed = git(self.origin, "rev-parse", pr["headRefName"])
            self.assertNotEqual(refreshed, pr["headRefOid"])
            self.assertEqual(git(self.origin, "rev-list", "--count", f"{refreshed}..main"), "0")
            self.assertTrue(any(row["pr"] == pr["number"] and row["head"] == refreshed
                                for row in self.events("approved")))
        self.assertEqual(self.merged, [])  # Refreshed heads still need their own CI.

    def test_behind_lower_pr_does_not_block_current_approved_higher_pr(self):
        behind = self.add_pr(7)
        self.move_main()
        current = self.add_pr(8)
        dispatch.land_pass(False)
        self.assertEqual(self.merged, [80])
        self.assertTrue((self.origin / "feature-8.txt").exists())
        self.assertEqual([row["head"] for row in self.events("merged")], [current["headRefOid"]])
        self.assertEqual([row["pr"] for row in self.events("refreshed")], [behind["number"]])
        self.assertIn((80, current["headRefOid"]), self.checked)

    def test_merge_moves_main_and_next_pr_is_refreshed_not_merged_with_stale_evidence(self):
        first, second = self.add_pr(7), self.add_pr(8)
        dispatch.land_pass(False)
        self.assertEqual(self.merged, [first["number"]])
        self.assertEqual([row["pr"] for row in self.events("refreshed")], [second["number"]])
        self.assertNotEqual(git(self.origin, "rev-parse", "agent/8"), second["headRefOid"])

    def test_dry_run_considers_both_prs_without_mutation(self):
        self.add_pr(7)
        self.add_pr(8)
        with mock.patch.object(dispatch, "log") as log:
            dispatch.land_pass(True)
        self.assertEqual([call.args[0] for call in log.call_args_list if "would merge" in call.args[0]],
                         ["PR #70: would merge", "PR #80: would merge"])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.events("merged") + self.events("merge-skip"), [])

    def test_failed_refresh_does_not_block_current_sibling(self):
        self.add_pr(7)
        self.move_main()
        self.add_pr(8)
        with mock.patch.object(dispatch, "refresh_pr_branch", return_value=False) as refresh:
            dispatch.land_pass(False)
        refresh.assert_called_once_with(7, 70, False)
        self.assertEqual(self.merged, [80])

    def test_final_freshness_refresh_does_not_end_stage(self):
        first = self.add_pr(7)
        self.add_pr(8)
        original_query = self.query
        compares = 0

        def query(args):
            nonlocal compares
            if args[0] == "api" and args[1].endswith("..." + first["headRefOid"]):
                compares += 1
                return {"behind_by": int(compares == 2)}
            return original_query(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=query), \
                mock.patch.object(dispatch, "refresh_pr_branch", return_value=False) as refresh:
            dispatch.land_pass(False)
        refresh.assert_called_once_with(7, 70, False)
        self.assertEqual(self.merged, [80])

    def test_skips_are_journaled_including_known_approval_outside_listing(self):
        cases = ((7, {"labels": []}, "not_approved"),
                 (8, {"isDraft": True}, "draft"),
                 (9, {"labels": [{"name": "factory-approved"}, {"name": "factory-held"}]}, "held"),
                 (10, {"reviewDecision": "CHANGES_REQUESTED"}, "human_veto"),
                 (11, {"baseRefName": "stable"}, "wrong_base"),
                 (12, {"headRefName": "manual/feature"}, "not_ticket"))
        for n, changes, _ in cases:
            self.add_pr(n).update(changes)
        self.evidence(13, 130, "unlisted-head")
        # Previously merged approvals must not be reported as outside the listing.
        self.evidence(14, 140, "merged-head")
        dispatch.record("merged", ticket=14, pr=140, head="merged-head")
        dispatch.land_pass(False)
        skipped = {row["pr"]: row["reason"] for row in self.events("merge-skip")}
        self.assertEqual(skipped, {**{n * 10: reason for n, _, reason in cases}, 130: "outside_listing"})
        self.assertEqual(self.merged, [])
        self.assertEqual(self.checked, [])

    def test_later_pr_still_needs_exact_head_approval_and_green_ci(self):
        for failure in ("evidence", "ci", "veto", "held"):
            with self.subTest(failure=failure):
                # The first PR refreshes, leaving the second otherwise merge-ready.
                first = self.add_pr(20 + len(self.prs))
                self.move_main()
                second = self.add_pr(20 + len(self.prs))
                if failure == "evidence":
                    dispatch.record("review", ticket=int(second["headRefName"].split("/")[1]),
                                    head=second["headRefOid"], actual_head="different-head",
                                    accepted=True, verdict="APPROVE")
                elif failure == "veto":
                    second["reviewDecision"] = "CHANGES_REQUESTED"
                elif failure == "held":
                    second["labels"].append({"name": "factory-held"})
                with mock.patch.object(dispatch, "refresh_pr_branch", return_value=True), \
                        mock.patch.object(dispatch, "escalate"), \
                        mock.patch.object(dispatch, "pr_checks", side_effect=lambda pr, head, failure=failure, second=second: [
                            {"name": "ci", "bucket": "pending" if failure == "ci" and pr == second["number"] else "pass"}
                        ]):
                    dispatch.land_pass(False)
                self.assertNotIn(second["number"], self.merged)
                first["state"] = second["state"] = "CLOSED"

    def test_hold_racing_after_listing_prevents_merge(self):
        for stage in ("fresh", "final"):
            with self.subTest(stage=stage):
                pr = self.add_pr(30 + len(self.prs))
                original_query = self.query
                views = 0

                def query(args, original_query=original_query, pr=pr, stage=stage):
                    nonlocal views
                    result = original_query(args)
                    if args[:3] == ["pr", "view", str(pr["number"])] and "files" in args[-1]:
                        views += 1
                        if views == (1 if stage == "fresh" else 2):
                            result["labels"] = [*result["labels"], {"name": "factory-held"}]
                    return result

                with mock.patch.object(dispatch, "gh_json", side_effect=query):
                    dispatch.land_pass(False)
                self.assertNotIn(pr["number"], self.merged)
                pr["state"] = "CLOSED"


if __name__ == "__main__":
    unittest.main()
