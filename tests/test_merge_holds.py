"""#783: holds and drafts veto every merge path, including late state changes."""
# ruff: noqa: B023 -- callbacks execute synchronously inside each subtest.
from __future__ import annotations

import copy
import unittest
from contextlib import contextmanager
from unittest import mock

from factory import learn, release
from tests import test_auto_learn, test_land_host, test_release


@contextmanager
def fixture(case_type):
    case = case_type()
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


class MergeHoldsTest(unittest.TestCase):
    def test_lessons_hold_or_draft_prevents_approval_and_merge(self):
        for branch in ("agent/lessons-2026-10-17-t10", "agent/curate-2026-10-17-t10",
                       "agent/revert-lessons-42"):
            for phase in ("listed", "final"):
                for veto in ("held", "draft", "none"):
                    with self.subTest(branch=branch, phase=phase, veto=veto), fixture(test_auto_learn.PromotionTest) as case:
                        case.BRANCH = branch
                        listed = {**case.open_prs[0], "headRefName": branch, "labels": []}
                        final = {**listed, "state": "OPEN"}
                        target = listed if phase == "listed" else final
                        if veto == "held":
                            target["labels"] = [{"name": "factory-held"}]
                        elif veto == "draft":
                            target["isDraft"] = True
                        case.journal(*case.evidence())
                        case.gh.side_effect = lambda args: (
                            final if args[:2] == ["pr", "view"] else case.compare
                            if args[0] == "api" and "/compare/" in args[1] else [listed]
                        )
                        learn.promote_pass([listed])
                        if veto == "none":
                            self.assertTrue(any("pr merge" in cmd for cmd in case.gh_calls()))
                        else:
                            self.assertEqual(case.gh_calls(), [])
                            self.assertEqual(case.rows("approved", "merged", "promotion-open"), [])

    def test_ticket_land_and_merge_recheck_held_and_draft(self):
        for entry in ("land_pass", "merge_pass_locked"):
            for phase in ("listed", "eligibility", "final"):
                for veto in ("held", "draft"):
                    with self.subTest(entry=entry, phase=phase, veto=veto), fixture(test_land_host.LandHostTest) as case:
                        module = case.dispatcher("tank")
                        case.approve(module)
                        original = module.gh_json.side_effect
                        views = 0

                        def read(args):
                            nonlocal views
                            result = original(args)
                            current = "listed" if args[:2] == ["pr", "list"] else None
                            if args[:2] == ["pr", "view"]:
                                views += 1
                                current = "eligibility" if views == 1 else "final"
                            if current == phase:
                                result = copy.deepcopy(result)
                                pr = result[0] if current == "listed" else result
                                if veto == "held":
                                    pr["labels"].append({"name": "factory-held"})
                                else:
                                    pr["isDraft"] = True
                            return result

                        with mock.patch.object(module, "gh_json", side_effect=read):
                            getattr(module, entry)(False)
                        self.assertFalse(case.github.merged)
                        self.assertFalse(any(cmd[:3] == ["gh", "pr", "merge"] for cmd in case.commands["tank"]))
                        self.assertEqual(case.events(module, "merged"), [])

    def test_release_rechecks_held_and_draft_after_ci(self):
        with fixture(test_release.ReleaseTest) as case:
            case.tag_and_advance(None)
            for phase in ("initial", "final"):
                for veto in ("held", "draft", "none"):
                    with self.subTest(phase=phase, veto=veto):
                        initial = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                                   "mergeable_state": "clean", "draft": False, "labels": []}
                        final = copy.deepcopy(initial)
                        target = initial if phase == "initial" else final
                        if veto == "held":
                            target["labels"] = [{"name": "factory-held"}]
                        elif veto == "draft":
                            target["draft"] = True
                        reads = iter((initial, final))
                        with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                                mock.patch.object(release, "_pull", side_effect=lambda *_: next(reads)), \
                                mock.patch.object(release, "ci_passes", return_value=True), \
                                mock.patch.object(release, "_merge_pull", return_value={"merged": True}) as merge:
                            reason = release.run(case.cfg, open_pr=False)
                        if veto == "none":
                            merge.assert_called_once_with(case.cfg, 7, "Release 0.0.2")
                            self.assertIn("merged release PR #7", reason)
                        else:
                            merge.assert_not_called()
                            self.assertIn("draft" if veto == "draft" else "factory-held", reason)

    def test_release_premerge_recheck_finishes_with_live_dry_run(self):
        # release.py:546 is the pre-merge recheck after the :544 `final = _pull`. It passes
        # `False` (not dry_run) to _finish, so a draft/held PR that only turns bad between the
        # :514 check and :544 recheck still vetoes the live merge. ReplaceFalseWithTrue there
        # keeps the same reason and still skips _merge_pull, so reason+merge assertions leave it
        # alive; spy on the _finish dry_run argument to pin the real False.
        with fixture(test_release.ReleaseTest) as case:
            case.tag_and_advance(None)
            for veto in ("draft", "held"):
                with self.subTest(veto=veto):
                    initial = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                               "mergeable_state": "clean", "draft": False, "labels": []}
                    final = copy.deepcopy(initial)
                    if veto == "draft":
                        final["draft"] = True
                    else:
                        final["labels"] = [{"name": "factory-held"}]
                    reads = iter((initial, final))
                    with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                            mock.patch.object(release, "_pull", side_effect=lambda *_: next(reads)), \
                            mock.patch.object(release, "ci_passes", return_value=True), \
                            mock.patch.object(release, "_finish", wraps=release._finish) as finish, \
                            mock.patch.object(release, "_merge_pull", return_value={"merged": True}) as merge:
                        release.run(case.cfg, open_pr=False)
                    finish.assert_called_once()
                    self.assertIs(finish.call_args.args[1], False)
                    self.assertIn("draft or factory-held", finish.call_args.args[2])
                    merge.assert_not_called()

    def test_lessons_merges_despite_labels_sorting_around_factory_held(self):
        # learn.py:1647 (early) and :1760 (final pr-view) guards: `== "factory-held"`
        # mutated to `<=` or `>=` over-blocks a PR carrying labels that sort before
        # (factory-approved, docs) or after (zzz-other) the held label. The empty-label
        # success case does not compare any label, so it leaves those mutants alive.
        labels = [{"name": "factory-approved"}, {"name": "docs"}, {"name": "zzz-other"}]
        with fixture(test_auto_learn.PromotionTest) as case:
            listed = {**case.open_prs[0], "labels": copy.deepcopy(labels)}
            final = {**listed, "state": "OPEN", "labels": copy.deepcopy(labels)}
            case.journal(*case.evidence())
            case.gh.side_effect = lambda args: (
                final if args[:2] == ["pr", "view"] else case.compare
                if args[0] == "api" and "/compare/" in args[1] else [listed]
            )
            learn.promote_pass([listed])
            self.assertTrue(any("pr merge" in cmd for cmd in case.gh_calls()))

    def test_merge_pass_lands_eligible_pr_listed_after_held_pr(self):
        # dispatch.py:4795 `continue` -> `break` on the factory-held skip: a held PR
        # (factory-approved AND factory-held, so it reaches the held check) listed before
        # the eligible one must not end the candidate scan. The eligible PR also carries
        # labels sorting before/after factory-held for parity with the other two paths.
        with fixture(test_land_host.LandHostTest) as case:
            tank = case.dispatcher("tank")
            case.claim("tank")
            head = case.approve(tank)
            labels = [{"name": "factory-approved"}, {"name": "docs"}, {"name": "zzz-other"}]
            held = {**case.github.pr(), "number": 101, "headRefName": "agent/8",
                    "labels": [{"name": "factory-approved"}, {"name": "factory-held"}]}
            original = tank.gh_json.side_effect

            def read(args):
                if args[:2] == ["pr", "list"]:
                    return [held, *original(args)]
                if args[:2] == ["pr", "view"]:
                    pr = copy.deepcopy(original(args))
                    pr["labels"] = copy.deepcopy(labels)
                    return pr
                return original(args)

            with mock.patch.object(tank, "gh_json", side_effect=read):
                tank.merge_pass_locked(False)
            self.assertTrue(case.github.merged)
            self.assertEqual([(row["pr"], row["head"]) for row in case.events(tank, "merged")],
                             [(102, head)])

    def test_release_merges_despite_labels_sorting_around_factory_held(self):
        # release.py:514 `== "factory-held"` mutated to `<=`, `>=`, or `is not` over-blocks
        # a release PR labelled with factory-approved/docs (sort before) and zzz-other
        # (sorts after); `is not` trips on any non-held label. The empty-label success
        # case compares nothing and leaves all three alive.
        labels = [{"name": "factory-approved"}, {"name": "docs"}, {"name": "zzz-other"}]
        with fixture(test_release.ReleaseTest) as case:
            case.tag_and_advance(None)
            pull = {"number": 7, "head": {"sha": "abc"}, "mergeable": True,
                    "mergeable_state": "clean", "draft": False, "labels": copy.deepcopy(labels)}
            reads = iter((copy.deepcopy(pull), copy.deepcopy(pull)))
            with mock.patch.object(release, "_pulls", return_value=[{"number": 7}]), \
                    mock.patch.object(release, "_pull", side_effect=lambda *_: next(reads)), \
                    mock.patch.object(release, "ci_passes", return_value=True), \
                    mock.patch.object(release, "_merge_pull", return_value={"merged": True}) as merge:
                reason = release.run(case.cfg, open_pr=False)
            merge.assert_called_once_with(case.cfg, 7, "Release 0.0.2")
            self.assertIn("merged release PR #7", reason)
