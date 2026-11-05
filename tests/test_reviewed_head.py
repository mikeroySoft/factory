"""Agent PR verdicts identify the immutable revision reviewed, including after rebase."""
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from factory import briefing, config, dashboard, dispatch, lifecycle
from tests import test_dispatch_lifecycle, test_refresh_conflict


class ReviewedHeadTest(unittest.TestCase):
    def assert_reviewed_head(self, body, head, verdict):
        self.assertRegex(head, r"^[0-9a-f]{40}$")
        self.assertEqual(re.findall(r"(?m)^Reviewed head: (.*)$", body), [head])
        self.assertEqual(body.strip().splitlines()[-1], f"VERDICT: {verdict}")
        self.assertEqual(dashboard.VERDICT.search(body).group(1), verdict)

    def test_first_review_and_bounce_post_the_exact_reviewed_heads(self):
        fixture = test_dispatch_lifecycle.LocalCLI(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        gh = fixture.bin / "gh"
        gh.write_text(test_dispatch_lifecycle.GH.replace(
            'elif a[:2] == ["pr", "create"]:',
            'elif a[:2] == ["pr", "comment"]:\n'
            '    s.setdefault("verdict_comments", []).append(Path(a[a.index("--body-file") + 1]).read_text())\n'
            '    p.write_text(json.dumps(s))\n'
            'elif a[:2] == ["pr", "create"]:',
        ))

        fixture.cli("dispatch", "--ticket", "7")

        reviews = [row for row in lifecycle.read_events(fixture.events)
                   if row.get("event") == "review"]
        bodies = json.loads(fixture.state.read_text())["verdict_comments"]
        self.assertEqual([row["verdict"] for row in reviews], ["REVISE", "APPROVE"])
        self.assertNotEqual(reviews[0]["head"], reviews[1]["head"])
        self.assertEqual(len(bodies), 2)
        for body, review in zip(bodies, reviews):
            with self.subTest(verdict=review["verdict"]):
                self.assert_reviewed_head(body, review["head"], review["verdict"])

    def test_refresh_reapproval_posts_new_head_instead_of_old_head(self):
        for carries_upstream in (False, True):
            with self.subTest(refresh="merge" if carries_upstream else "rebase"):
                fixture = test_refresh_conflict.RefreshGateFailureTest(methodName="runTest")
                self.addCleanup(fixture.doCleanups)
                fixture.setUp()
                bodies = []

                def capture_comment(cmd, *args, **kwargs):
                    if cmd[:3] == ["gh", "pr", "comment"]:
                        bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text())
                    return fixture.github.run(cmd, *args, **kwargs)

                with mock.patch.multiple(dispatch, gh_json=fixture.github.gh_json, run=capture_comment), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")):
                    wt = dispatch.ensure_worktree(9)
                    verdict, findings = dispatch.review(wt, 9, "gate ok", fixture.approved_head)
                    self.assertEqual(verdict, "APPROVE")
                    dispatch.pr_comment(9, findings)
                    self.assertTrue(dispatch.refresh_pr_branch(9, 102, carries_upstream))

                reviews = fixture.events("review")
                new_head = fixture.github.head()
                self.assertNotEqual(new_head, fixture.approved_head)
                self.assertEqual([row["head"] for row in reviews], [fixture.approved_head, new_head])
                self.assertEqual(len(bodies), 2)
                for body, head in zip(bodies, (fixture.approved_head, new_head)):
                    self.assert_reviewed_head(body, head, "APPROVE")
                self.assertNotIn(fixture.approved_head, bodies[1])

    def test_briefing_still_excludes_verdict_comments_from_discussion(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            cfg = config.Config(root=Path(temp), repo="local/smoke")
            verdicts = [f"Reviewed head: {'a' * 40}\n\nVERDICT: {verdict}\n"
                        for verdict in ("APPROVE", "REVISE")]
            ticket = {"number": 7, "title": "change", "url": "https://example.invalid/issues/7",
                      "pr": {"number": 70, "comments": [
                          *({"body": body} for body in verdicts), {"body": "Human discussion"},
                      ]}}
            sources = briefing.sources_for(cfg, ticket, [])
            discussion = [source["text"] for source in sources if source["label"].startswith("Pr comment")]
            self.assertEqual(discussion, ["Human discussion"])
