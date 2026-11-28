from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle


class MergePassTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        dispatch.configure(config.Config(
            root=self.root,
            repo="acme/widgets",
            signoff=False,
        ))
        self.run = self.enterContext(mock.patch.object(
            dispatch,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ))
        self.log = self.enterContext(mock.patch.object(dispatch, "log"))
        self.cleanup = self.enterContext(mock.patch.object(dispatch, "cleanup_after_merge"))
        self.enterContext(mock.patch.object(dispatch, "initiative_kind", return_value=None))
        self.enterContext(mock.patch.object(
            dispatch,
            "triage_cancelled",
            side_effect=lambda _pr, checks, *_args: checks,
        ))
        self.enterContext(mock.patch("factory.learn.promote_pass"))
        self.enterContext(mock.patch.object(dispatch, "escalate", side_effect=self._escalate))

    def _escalate(self, ticket, reason, _log_path, extra="", *, code, **fields) -> None:
        dispatch.record("escalate", ticket=ticket, reason=reason, code=code, extra=extra, **fields)

    @staticmethod
    def _text(value: str) -> str:
        """Return equal text with runtime identity, as GitHub JSON decoding does."""
        return bytearray(value, "ascii").decode("ascii")

    @staticmethod
    def _head(ticket: int) -> str:
        return f"{ticket:040x}"[-40:]

    def _pr(self, offset: int) -> dict:
        ticket = 10**18 + 10_000 + offset
        return {
            "number": 10**18 + 20_000 + offset,
            "headRefName": f"agent/{ticket}",
            "headRefOid": self._head(ticket),
            "baseRefName": "main",
            "isDraft": False,
            "labels": [{"name": config.LABEL_APPROVED}],
            "reviewDecision": "APPROVED",
            "state": "OPEN",
            "title": f"candidate {offset}",
            "files": [],
        }

    @staticmethod
    def _ticket(pr: dict) -> int:
        return int(pr["headRefName"].removeprefix("agent/"))

    def _evidence(self, candidate: dict, *, event: str = "approved", **approval) -> None:
        ticket = self._ticket(candidate)
        head = candidate["headRefOid"]
        dispatch.record(
            "attempt", ticket=ticket, head=head, actual_head=head, gate="PASS",
        )
        dispatch.record(
            "review", ticket=ticket, head=head, actual_head=head,
            accepted=True, verdict="APPROVE", role="primary", model="review/model",
        )
        fields = {
            "ticket": ticket,
            "pr": candidate["number"],
            "head": head,
            "gate_head": head,
            "review_head": head,
            **approval,
        }
        dispatch.record(event, **fields)

    def _events(self, event: str) -> list[dict]:
        return [
            row for row in lifecycle.read_events(dispatch.EVENTS)
            if row.get("event") == event
        ]

    def _execute(
        self,
        prs: list[dict],
        checks: dict[int, list[dict] | list[list[dict]]],
        *,
        fresh: dict[int, dict] | None = None,
        final: dict[int, dict] | None = None,
        merge_sha: str = "c" * 40,
        dry_run: bool = False,
    ) -> dict[int, int]:
        fresh = fresh or {}
        final = final or {}
        by_number = {pr["number"]: pr for pr in prs}
        views: defaultdict[int, int] = defaultdict(int)
        check_calls: defaultdict[int, int] = defaultdict(int)

        def github(args):
            if args[:2] == ["pr", "list"]:
                return prs
            if args[:2] == ["pr", "view"]:
                number = int(args[2])
                if args[-1] == "mergeCommit":
                    return {"mergeCommit": {"oid": merge_sha}}
                base = {**by_number[number], **fresh.get(number, {})}
                result = base if views[number] == 0 else {**base, **final.get(number, {})}
                views[number] += 1
                return result
            if args[0] == "api":
                return {"behind_by": 0}
            raise AssertionError(args)

        def pr_checks(number, _head):
            sequence = checks[number]
            call = check_calls[number]
            check_calls[number] += 1
            if sequence and isinstance(sequence[0], list):
                return sequence[min(call, len(sequence) - 1)]
            return sequence

        with mock.patch.object(dispatch, "gh_json", side_effect=github), \
                mock.patch.object(dispatch, "pr_checks", side_effect=pr_checks):
            dispatch.merge_pass_locked(dry_run)
        return dict(check_calls)

    def test_review_veto_does_not_block_the_next_candidate(self) -> None:
        vetoed, mergeable = self._pr(1), self._pr(2)
        self._evidence(vetoed)
        dispatch.record(
            "review", ticket=self._ticket(vetoed), head=vetoed["headRefOid"],
            actual_head=vetoed["headRefOid"], accepted=True,
            verdict="REVISE", role="primary",
        )
        self._evidence(mergeable)

        self._execute(
            [vetoed, mergeable],
            {pr["number"]: [{"name": "ci", "bucket": "pass"}] for pr in (vetoed, mergeable)},
        )

        self.assertEqual([row["pr"] for row in self._events("merged")], [mergeable["number"]])
        self.assertEqual([row["code"] for row in self._events("escalate")], ["review_revise"])

    def test_runner_starvation_does_not_block_the_next_candidate(self) -> None:
        starved, mergeable = self._pr(3), self._pr(4)
        self._evidence(mergeable)
        checks = {
            starved["number"]: [{"name": "hosted", "bucket": "infra"}],
            mergeable["number"]: [{"name": "ci", "bucket": "pass"}],
        }

        with mock.patch.object(dispatch, "runner_unavailable", return_value=True):
            self._execute([starved, mergeable], checks)

        self.assertEqual([row["pr"] for row in self._events("merged")], [mergeable["number"]])
        decisions = [row for row in self._events("decision") if row.get("stage") == "merge"]
        self.assertIn((starved["number"], "ci-infra"), [
            (row["pr"], row["chosen"]) for row in decisions
        ])

    def test_initial_noninterned_ci_failure_is_terminal_for_the_candidate(self) -> None:
        candidate = self._pr(5)
        self._evidence(candidate)
        failed = {"name": "unit", "bucket": self._text("fail")}
        passed = {"name": "lint", "bucket": "pass"}

        self._execute(
            [candidate],
            {candidate["number"]: [[failed, passed], [passed]]},
        )

        self.assertEqual(self._events("merged"), [])
        self.assertEqual([row["code"] for row in self._events("escalate")], ["ci_failed"])
        [decision] = [
            row for row in self._events("decision")
            if row.get("stage") == "merge"
        ]
        self.assertEqual(decision["chosen"], "remove stale factory-approved label")

    def test_latest_noninterned_ci_failure_prevents_merge(self) -> None:
        candidate = self._pr(6)
        self._evidence(candidate)
        passed = {"name": "lint", "bucket": "pass"}
        failed = {"name": "unit", "bucket": self._text("fail")}

        self._execute(
            [candidate],
            {candidate["number"]: [[passed], [failed, passed]]},
        )

        self.assertEqual(self._events("merged"), [])
        self.assertEqual([row["code"] for row in self._events("escalate")], ["ci_failed"])

    def test_refreshed_pr_guards_continue_to_a_later_candidate(self) -> None:
        wrong_branch, missing_label, mergeable = (self._pr(7), self._pr(8), self._pr(9))
        for pr in (wrong_branch, missing_label, mergeable):
            self._evidence(pr)
        checks = {
            pr["number"]: [{"name": "ci", "bucket": "pass"}]
            for pr in (wrong_branch, missing_label, mergeable)
        }
        fresh = {
            wrong_branch["number"]: {
                "headRefName": f"{wrong_branch['headRefName']}z",
            },
            missing_label["number"]: {"labels": []},
        }

        self._execute([wrong_branch, missing_label, mergeable], checks, fresh=fresh)

        self.assertEqual([row["pr"] for row in self._events("merged")], [mergeable["number"]])
        self.assertEqual(self._events("escalate"), [])

    def test_final_pr_guards_continue_to_a_later_candidate(self) -> None:
        wrong_branch, missing_label, mergeable = (self._pr(10), self._pr(11), self._pr(12))
        for pr in (wrong_branch, missing_label, mergeable):
            self._evidence(pr)
        checks = {
            pr["number"]: [{"name": "ci", "bucket": "pass"}]
            for pr in (wrong_branch, missing_label, mergeable)
        }
        final = {
            wrong_branch["number"]: {"headRefName": "agent/1"},
            missing_label["number"]: {"labels": []},
        }

        self._execute([wrong_branch, missing_label, mergeable], checks, final=final)

        self.assertEqual([row["pr"] for row in self._events("merged")], [mergeable["number"]])
        self.assertEqual(self._events("escalate"), [])

    def test_dry_run_accepts_equal_nonidentical_large_pr_evidence(self) -> None:
        candidate = self._pr(13)
        self._evidence(candidate)

        self._execute(
            [candidate],
            {candidate["number"]: [{"name": "ci", "bucket": "pass"}]},
            dry_run=True,
        )

        self.log.assert_any_call(f"PR #{candidate['number']}: would merge")

    def test_dry_run_rejects_stale_gate_head_evidence(self) -> None:
        candidate = self._pr(14)
        self._evidence(candidate, gate_head="d" * 40)

        self._execute(
            [candidate],
            {candidate["number"]: [{"name": "ci", "bucket": "pass"}]},
            dry_run=True,
        )

        self.log.assert_any_call(
            f"PR #{candidate['number']}: would refuse (missing SHA-bound approval evidence)"
        )

    def test_dry_run_rejects_stale_review_head_evidence(self) -> None:
        candidate = self._pr(15)
        self._evidence(candidate, review_head="e" * 40)

        self._execute(
            [candidate],
            {candidate["number"]: [{"name": "ci", "bucket": "pass"}]},
            dry_run=True,
        )

        self.log.assert_any_call(
            f"PR #{candidate['number']}: would refuse (missing SHA-bound approval evidence)"
        )

    def test_only_exact_ticket_pr_head_gate_and_review_approval_can_merge(self) -> None:
        candidates = [self._pr(offset) for offset in range(16, 23)]
        fake_event, wrong_ticket, wrong_pr, wrong_head, wrong_gate, wrong_review, mergeable = candidates
        self._evidence(fake_event, event="zzzz")
        self._evidence(wrong_ticket, ticket=self._ticket(wrong_ticket) + 1)
        self._evidence(wrong_pr, pr=wrong_pr["number"] + 1)
        self._evidence(wrong_head, head="1" * 40)
        self._evidence(wrong_gate, gate_head="2" * 40)
        self._evidence(wrong_review, review_head="3" * 40)
        self._evidence(mergeable)
        checks = {
            pr["number"]: [{"name": "ci", "bucket": "pass"}]
            for pr in candidates
        }

        self._execute(candidates, checks)

        self.assertEqual([row["pr"] for row in self._events("merged")], [mergeable["number"]])
        self.assertEqual(
            [(row["ticket"], row["code"]) for row in self._events("escalate")],
            [(self._ticket(pr), "approval_missing") for pr in candidates[:-1]],
        )

    def test_merge_receipt_keeps_metadata_and_stops_after_one_merge(self) -> None:
        first, second = self._pr(23), self._pr(24)
        for pr in (first, second):
            self._evidence(pr)
        merge_sha = "f" * 40
        checks = {
            pr["number"]: [{"name": "ci", "bucket": "pass"}]
            for pr in (first, second)
        }

        check_calls = self._execute([first, second], checks, merge_sha=merge_sha)

        [merged] = self._events("merged")
        self.assertEqual(
            (merged["ticket"], merged["pr"], merged["head"], merged["method"], merged["merge_sha"]),
            (self._ticket(first), first["number"], first["headRefOid"], "squash", merge_sha),
        )
        [decision] = [
            row for row in self._events("decision")
            if row.get("stage") == "merge" and row.get("chosen") == "merge now"
        ]
        self.assertIn(
            {
                "kind": "merge_sha",
                "ref": f"https://github.com/acme/widgets/commit/{merge_sha}",
                "summary": f"merge SHA {merge_sha}",
            },
            decision["inputs"],
        )
        self.cleanup.assert_called_once_with(self._ticket(first), first["headRefOid"])
        self.assertEqual(check_calls, {first["number"]: 2})
        self.log.assert_any_call(
            f"PR #{first['number']}: merged into main (squash, ticket #{self._ticket(first)})"
        )


if __name__ == "__main__":
    unittest.main()
