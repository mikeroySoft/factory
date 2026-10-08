"""Candidate escaped defects: closed bugs blamed back to the agent ticket that introduced the lines (#194)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, stats


class EscapesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.commit("agent/5: add f", f="a\nb\nc\nd\n")
        self.commit("tweak f", f="a\nb\nc\nD\n")  # hand commit: never a candidate
        self.fix9 = self.commit("agent/9: fix f", f="a\nB\nc\nDD\n")  # modifies agent/5's b and the hand D
        self.fix11 = self.commit("agent/11: add g", g="new\n")  # only adds lines
        self.fix14 = self.commit("Fix f again (#40)", f="a\nB\nC\nDD\n")  # PR-linked squash; agent/5's c
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        stats.configure(config.Config(self.root, "acme/widgets"))
        self.bugs = [self.bug(9), self.bug(11), self.bug(12), self.bug(14)]

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True).stdout.strip()

    def commit(self, subject: str, **files: str) -> str:
        for name, text in files.items():
            (self.root / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def bug(self, number: int, body: str = "") -> dict:
        return {"number": number, "title": f"bug {number}", "body": body,
                "closedAt": f"2026-10-{number:02d}T00:00:00Z"}

    def gh(self, *args: str):
        if args[:2] == ("issue", "list"):
            self.assertEqual(args[args.index("--label") + 1], "bug")
            self.assertEqual(args[args.index("--state") + 1], "closed")
            return self.bugs
        if args[:2] == ("pr", "list"):
            return [{"mergeCommit": {"oid": self.fix14}, "closingIssuesReferences": [{"number": 14}]},
                    {"mergeCommit": None, "closingIssuesReferences": [{"number": 12}]}]
        self.fail(f"unexpected gh call: {args}")

    def rows(self) -> dict[int, dict]:
        with mock.patch.object(stats, "gh", side_effect=self.gh):
            return {row["bug"]: row for row in stats.escapes()}

    def test_blame_names_the_introducing_agent_ticket_and_ignores_hand_commits(self) -> None:
        row = self.rows()[9]
        self.assertEqual((row["fixes"], row["source"], row["status"]), ([self.fix9], "blame", "candidates"))
        self.assertEqual(row["candidates"], [{"ticket": 5, "title": "add f", "lines": 1, "paths": ["f"]}])

    def test_fix_that_only_adds_lines_has_no_blamed_lines(self) -> None:
        row = self.rows()[11]
        self.assertEqual((row["fixes"], row["status"], row["candidates"]), ([self.fix11], "no_blamed_lines", []))

    def test_pr_closing_reference_links_a_fix_without_an_agent_subject(self) -> None:
        row = self.rows()[14]
        self.assertEqual((row["fixes"], row["source"]), ([self.fix14], "blame"))
        self.assertEqual([c["ticket"] for c in row["candidates"]], [5])

    def test_bug_without_a_fix_commit_is_unlinked(self) -> None:
        row = self.rows()[12]
        self.assertEqual((row["fixes"], row["status"], row["candidates"]), ([], "unlinked", []))

    def test_only_unprefixed_blame_is_no_candidates(self) -> None:
        self.commit("hand: reword", f="a\nB\nC\nD5\n")
        fix = self.commit("agent/21: fix D", f="a\nB\nC\nD6\n")  # D5 came from a hand commit
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.bugs = [self.bug(21)]
        row = self.rows()[21]
        self.assertEqual((row["fixes"], row["status"], row["candidates"]), ([fix], "no_candidates", []))

    def test_regressed_by_overrides_blame(self) -> None:
        self.bugs = [self.bug(9, "Steps...\r\nRegressed-by: #3\nRegressed-by: #5\n")]
        row = self.rows()[9]
        self.assertEqual((row["source"], row["status"]), ("declared", "candidates"))
        self.assertEqual(row["candidates"], [{"ticket": 3, "title": "", "lines": None, "paths": []},
                                             {"ticket": 5, "title": "add f", "lines": None, "paths": []}])

    def test_cli_prints_rows_and_candidate_footer(self) -> None:
        with mock.patch.object(stats, "gh", side_effect=self.gh), \
             mock.patch.object(stats.config, "load", return_value=stats.cfg), \
             mock.patch("builtins.print") as printed:
            stats.main(["--escapes"])
            text = "\n".join(str(c.args[0]) for c in printed.call_args_list)
            printed.reset_mock()
            stats.main(["--escapes", "--json"])
            rows = json.loads(printed.call_args.args[0])
        self.assertIn(f"#9    {self.fix9[:7]}  blame    #5 (1 line)", text)
        self.assertIn("#11", text)
        self.assertIn("no_blamed_lines", text)
        self.assertIn("unlinked", text)
        self.assertNotIn("caused", text)
        # Merged agent tickets on main: 5, 9, 11; candidates among them: 5.
        self.assertIn("Candidate escapes: 1 of 3 merged agent tickets (33.3%)", text)
        self.assertEqual([row["bug"] for row in rows], [9, 11, 12, 14])

    def test_learn_evidence_carries_recent_candidates(self) -> None:
        events = self.root / "events.jsonl"
        events.write_text(json.dumps({"event": "escalate", "ticket": 2, "at": "2026-10-01T00:00:00Z"}) + "\n")
        with mock.patch.object(stats, "gh", side_effect=self.gh), \
             mock.patch.multiple(dispatch, FACTORY=self.root, EVENTS=events, create=True):
            _, text = learn.evidence(10)
        section = text[text.index("## Candidate escapes"):]
        self.assertIn("Bug #9: bug 9 <- candidate #5: add f (blame; f)", section)
        self.assertIn("Bug #14: bug 14 <- candidate #5", section)
        self.assertNotIn("#11", section)
        self.assertLessEqual(len(text), learn.MAX_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
