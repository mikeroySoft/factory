"""Human corrections on agent PRs, attributed by identity with an implicit cutover (#199)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dashboard, dispatch, feedback, stats

LOGIN = "factory-agent"
BEFORE, CUTOVER, AFTER = "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z"
HUMAN = {"login": "maintainer", "__typename": "User"}
FACTORY = {"login": LOGIN, "__typename": "User"}
BOT = {"login": "ci[bot]", "__typename": "Bot"}


def pr_node(actor: dict, at: str) -> dict:
    """One of every correction kind by `actor`, plus an approval that never counts."""
    user = {"user": {"login": actor["login"]}}
    return {
        "reviews": {"nodes": [{"state": "CHANGES_REQUESTED", "submittedAt": at, "author": actor},
                              {"state": "APPROVED", "submittedAt": at, "author": actor}]},
        "reviewThreads": {"nodes": [{"comments": {"nodes": [{"createdAt": at, "author": actor}]}}]},
        "comments": {"nodes": [{"createdAt": at, "author": actor}]},
        "commits": {"nodes": [{"commit": {"committedDate": at, "author": user, "committer": user}}]},
    }


def counts(node: dict, login: str = LOGIN, cutover: str | None = CUTOVER) -> dict:
    return feedback.corrections(feedback.correction_actions(node), login, cutover)


class CorrectionAttributionTest(unittest.TestCase):
    def test_human_actions_after_cutover_each_count(self) -> None:
        self.assertEqual(counts(pr_node(HUMAN, AFTER)),
                         {"human_change_requests": 1, "human_comments": 2, "human_commits": 1})

    def test_factory_login_and_bots_are_not_human(self) -> None:
        zero = {"human_change_requests": 0, "human_comments": 0, "human_commits": 0}
        for actor in (FACTORY, {**FACTORY, "login": LOGIN.upper()}, BOT):
            with self.subTest(actor=actor["login"]):
                self.assertEqual(counts(pr_node(actor, AFTER)), zero)

    def test_actions_before_the_first_factory_action_are_unattributable(self) -> None:
        unknown = {"human_change_requests": None, "human_comments": None, "human_commits": None}
        self.assertEqual(counts(pr_node(HUMAN, BEFORE)), unknown)
        # No observed factory action (or no separate dispatcher login): nothing is attributable.
        self.assertEqual(counts(pr_node(HUMAN, AFTER), cutover=None), unknown)
        self.assertEqual(counts(pr_node(HUMAN, AFTER), login="", cutover=None), unknown)

    def test_cutover_is_the_login_first_action_across_timezones(self) -> None:
        observed = [(HUMAN, BEFORE), (FACTORY, AFTER), ({"login": "Factory-Agent"}, "2026-09-02T01:00:00+02:00")]
        self.assertEqual(feedback.first_action(LOGIN, observed), "2026-09-02T01:00:00+02:00")
        self.assertIsNone(feedback.first_action(LOGIN, [(HUMAN, AFTER)]))
        self.assertIsNone(feedback.first_action("", observed))

    def test_human_touched_share_leaves_unattributable_prs_out(self) -> None:
        touched = counts(pr_node(HUMAN, AFTER))
        clean = counts(pr_node(FACTORY, AFTER))
        early = counts(pr_node(HUMAN, BEFORE))
        self.assertEqual(stats.human_touched_pct([touched, clean, clean, early]), 33.3)
        self.assertIsNone(stats.human_touched_pct([early]))
        self.assertIsNone(stats.human_touched_pct([]))

    def test_dispatcher_login_comes_from_dispatch_env_only(self) -> None:
        cfg = config.Config(Path("/unused"), "acme/widgets")
        cfg.install = {"env": {"UV": "1"}}
        with mock.patch.object(stats.onboard, "gh_login", return_value=LOGIN) as gh_login:
            self.assertEqual(stats.dispatcher_login(cfg), "")
            gh_login.assert_not_called()
            cfg.install["dispatch_env"] = {"GH_CONFIG_DIR": "/machine"}
            stats._login.cache_clear()
            self.assertEqual(stats.dispatcher_login(cfg), LOGIN)
        env = gh_login.call_args.args[0]
        self.assertEqual((env["UV"], env["GH_CONFIG_DIR"]), ("1", "/machine"))


class StatsCorrectionsTest(unittest.TestCase):
    def collect(self, login: str) -> tuple[list[dict], str]:
        def label(event: str, at: str, actor: dict) -> dict:
            return {"event": event, "created_at": at, "label": {"name": config.LABEL_HUMAN},
                    "actor": {"login": actor["login"], "type": actor["__typename"]}}

        timelines = {
            7: [label("labeled", BEFORE, {"login": "someone", "__typename": "User"}),
                label("unlabeled", BEFORE, HUMAN)],  # pre-cutover
            8: [label("labeled", CUTOVER, FACTORY), label("unlabeled", AFTER, HUMAN)],
            9: [label("labeled", AFTER, HUMAN), label("unlabeled", AFTER, FACTORY)],
        }
        # The factory's first observed action is at CUTOVER (ticket 8's label, PR 90's review).
        nodes = {70: pr_node(HUMAN, BEFORE), 80: pr_node(HUMAN, AFTER), 90: pr_node(FACTORY, CUTOVER)}

        def gh(*args: str):
            if args[:2] == ("pr", "list"):
                return [{"number": n * 10, "state": "MERGED", "headRefName": f"agent/{n}",
                         "mergedAt": AFTER} for n in (7, 8, 9)]
            if args[:2] == ("issue", "list"):
                return []
            if args[:2] == ("issue", "view"):
                return {"number": int(args[2]), "title": "t", "createdAt": BEFORE, "closedAt": None,
                        "state": "CLOSED", "comments": []}
            if args[:2] == ("pr", "view"):
                return {"comments": []}
            if args[:2] == ("api", "graphql"):
                query = args[3]
                return {"data": {"repository": {f"pr{n}": node for n, node in nodes.items() if f"pr{n}:" in query}}}
            if args[0] == "api":
                return [timelines[int(args[1].split("/")[-2])]]
            self.fail(f"unexpected gh call: {args}")

        with tempfile.TemporaryDirectory() as d:
            stats.configure(config.Config(Path(d), "acme/widgets"))
            stats.cfg.factory.mkdir()
            (stats.cfg.factory / "events.jsonl").write_text("")
            with mock.patch.object(stats, "gh", side_effect=gh), \
                 mock.patch.object(stats, "dispatcher_login", return_value=login), \
                 mock.patch.object(stats.config, "load", return_value=stats.cfg), \
                 mock.patch("builtins.print") as printed:
                rows = stats.collect_rows()
                stats.main([])
        return rows, "\n".join(str(c.args[0]) if c.args else "" for c in printed.call_args_list)

    def test_counts_and_both_percentages_use_the_same_rule(self) -> None:
        rows, out = self.collect(LOGIN)
        by_ticket = {row["ticket"]: row for row in rows}
        self.assertEqual({n: by_ticket[n]["human_comments"] for n in (7, 8, 9)}, {7: None, 8: 2, 9: 0})
        self.assertEqual(by_ticket[7]["resolutions"], [{"actor": "maintainer", "resolved_by": "unattributable"}])
        self.assertEqual(by_ticket[8]["resolutions"], [{"actor": "maintainer", "resolved_by": "human"}])
        self.assertEqual(by_ticket[9]["resolutions"], [{"actor": LOGIN, "resolved_by": "factory"}])
        self.assertEqual(stats.human_touch_metrics(rows)["human_resolved_pct"], 50.0)
        self.assertIn("Human-resolved: 50.0%", out)
        self.assertIn("Human-touched PRs: 50.0%", out)

    def test_no_factory_action_yet_shows_na(self) -> None:
        rows, out = self.collect("")
        self.assertTrue(all(row["human_commits"] is None for row in rows))
        self.assertIn("Human-resolved: n/a", out)
        self.assertIn("Human-touched PRs: n/a", out)


class DashboardCorrectionsTest(unittest.TestCase):
    def test_json_metric_reads_each_merged_pr_once(self) -> None:
        def issue(n: int) -> dict:
            return {"id": f"I_{n}", "number": n, "title": "t", "url": f"https://github.com/acme/widgets/issues/{n}",
                    "state": "CLOSED", "body": "", "createdAt": BEFORE, "updatedAt": AFTER, "closedAt": AFTER,
                    "labels": {"nodes": []}, "assignees": {"nodes": []}, "timelineItems": {"nodes": []}}

        def pr(n: int, merged: bool) -> dict:
            return {"id": f"PR_{n}", "number": n * 10, "url": f"https://github.com/acme/widgets/pull/{n * 10}",
                    "title": "t", "headRefName": f"agent/{n}", "headRefOid": "a" * 40, "state": "MERGED" if merged else "CLOSED",
                    "isDraft": False, "createdAt": BEFORE, "closedAt": AFTER, "mergedAt": AFTER if merged else None,
                    "additions": 1, "deletions": 0, "changedFiles": 1, "body": "", "reviewDecision": None,
                    "labels": {"nodes": []}, "comments": {"nodes": []}, "commits": {"nodes": []}}

        nodes = {70: pr_node(HUMAN, AFTER), 80: pr_node(FACTORY, CUTOVER)}
        reads = []

        def github(**kwargs):
            if not kwargs:
                return {"viewer": {"login": "maintainer"}, "repository": {
                    "id": "R_1", "issues": {"nodes": [issue(7), issue(8), issue(9)]},
                    "pullRequests": {"nodes": [pr(7, True), pr(8, True), pr(9, False)]}}}
            reads.append(kwargs["query"])
            return {"repository": {f"pr{n}": node for n, node in nodes.items() if f"pr{n}:" in kwargs["query"]}}

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".factory").mkdir()
            with mock.patch.dict(dashboard.__dict__), mock.patch.dict(dispatch.__dict__):
                dashboard.configure(config.Config(Path(d), "acme/widgets"))
                with mock.patch.object(dashboard, "github", side_effect=github), \
                     mock.patch.object(dashboard, "dispatcher", return_value={}), \
                     mock.patch.object(dashboard, "upstream_state", return_value={}), \
                     mock.patch.object(dashboard, "triage_llm_online", return_value=False), \
                     mock.patch.object(dashboard.stats, "dispatcher_login", return_value=LOGIN):
                    first = dashboard.snapshot()
                    second = dashboard.snapshot()
        self.assertEqual(len(reads), 1)
        self.assertNotIn("pr90:", reads[0])
        self.assertEqual(first["metrics"]["human_touched_pct"], 50.0)
        self.assertEqual(second["metrics"]["human_touched_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
