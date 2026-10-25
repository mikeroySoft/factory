"""Kept learning proposals are gated and reviewed separately from their replays."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle

APPROVE = '```json\n{"verdict":"APPROVE","findings":[]}\n```\nVERDICT: APPROVE'
REVISE = (
    '```json\n{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug",'
    '"path":"AGENTS.md","line":2,"summary":"Guidance is incorrect."}]}\n```\nVERDICT: REVISE'
)


class LearnCandidateReviewTest(unittest.TestCase):
    def scenario(self, directory: str, reply: str, *, gate_failure: str = "") -> tuple:
        root = Path(directory)
        repo = root / "repo"
        repo.mkdir()

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
            ).stdout.strip()

        git("init", "-q", "-b", "main")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.com")
        (repo / ".gitignore").write_text(".factory/\n")
        (repo / "AGENTS.md").write_text("Existing standards.\n")
        (repo / config.CONFIG_NAME).write_text(
            '[repo]\nslug = "acme/widgets"\n'
            '[[gate.check]]\nname = "attempt"\nrun = ['
            + json.dumps(sys.executable) + ', "-c", "raise SystemExit('
            + str(int(gate_failure == "attempt")) + ')"]\n'
            '[[gate.check]]\nname = "publication"\nwhen = "pr"\nrun = ['
            + json.dumps(sys.executable) + ', "-c", "raise SystemExit('
            + str(int(gate_failure == "pr")) + ')"]\n'
            '[leak_scan]\npattern = ""\n'
        )
        git("add", "--", ".gitignore", "AGENTS.md", config.CONFIG_NAME)
        git("commit", "-q", "-m", "base")
        base = git("rev-parse", "HEAD")
        remote = root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(remote)], check=True)
        git("remote", "add", "origin", str(remote))

        captured = root / "candidate-review.json"
        reviewer = root / "reviewer.py"
        reviewer.write_text(
            "import json,subprocess,sys\n"
            "from pathlib import Path\n"
            "diff=Path('review.diff').read_text()\n"
            f"Path({str(captured)!r}).write_text(json.dumps("
            "{'prompt':sys.argv[1],'diff':diff}))\n"
            + (
                f"wt=next(Path({str(repo / '.factory')!r}).glob('wt-*'))\n"
                "subprocess.run(['git','-C',str(wt),'commit','--allow-empty','-qm','review changed head'],check=True)\n"
                if reply == "move-head" else ""
            )
            + f"print({(APPROVE if reply == 'move-head' else reply)!r})\n"
        )
        cfg = config.Config(
            root=repo, repo="acme/widgets", manager=["manager"],
            reviewer=[sys.executable, str(reviewer), "{prompt}"],
            signoff=True, learn_evaluate=True, learn_max_tickets=1,
            learn_max_tokens=1000, learn_budget_min=1,
        )
        cfg.factory.mkdir()
        dispatch.configure(cfg)
        case = learn.ReplayCase(
            7, base, "fix parser", (),
            {"title": "fix parser", "body": "INCIDENT_IMPLEMENTATION_CRITERIA", "comments": []},
        )
        diff = (
            "diff --git a/AGENTS.md b/AGENTS.md\n"
            "--- a/AGENTS.md\n+++ b/AGENTS.md\n"
            "@@ -1 +1,2 @@\n Existing standards.\n+Helpful parser guidance.\n"
        )
        return cfg, case, diff, captured, git

    def run_proposal(
        self, cfg, case, diff, *, helpful: str = "curate", exhausted: bool = False,
        evaluate: bool | None = True,
    ) -> dict | None:
        baseline = {
            "first_gate_pass": False, "approved": True, "review_rounds": 2,
            "revise_findings": 1, "tokens": 100, "cost": 0.2, "seconds": 2.0,
        }

        def replay(_cfg, _case, candidate, _deadline):
            if exhausted:
                return {**baseline, "tokens": 1001}
            if candidate is None:
                return baseline
            good = candidate.kind == helpful
            return {
                **baseline, "first_gate_pass": good, "approved": good,
                "review_rounds": 1 if good else 3, "revise_findings": 0 if good else 2,
            }

        project = Path(__file__).resolve().parents[1]
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(project)}), \
             mock.patch.object(learn, "evidence", return_value=([7], "incident")), \
             mock.patch.object(learn, "replay_cases", return_value=[case]), \
             mock.patch.object(learn, "propose", return_value=(["Parser guidance."], diff)), \
             mock.patch.object(learn, "replay_ticket", side_effect=replay):
            return learn.run(cfg, evaluate=evaluate)

    def test_only_helpful_candidate_is_published_after_candidate_review_with_evidence(self) -> None:
        for helpful in ("curate", "lessons"):
            with self.subTest(helpful=helpful), tempfile.TemporaryDirectory() as directory:
                cfg, case, diff, captured, git = self.scenario(directory, APPROVE)
                with mock.patch.object(dispatch, "push_and_pr", return_value=True) as publish:
                    event = self.run_proposal(cfg, case, diff, helpful=helpful)
                publish.assert_called_once()
                wt, branch, _title, body = publish.call_args.args
                self.assertIn(f"agent/{helpful}-", branch)
                self.assertFalse(wt.exists())
                review = json.loads(captured.read_text())
                self.assertIn("Helpful parser guidance." if helpful == "curate" else "Parser guidance.",
                              review["diff"])
                for text in (review["prompt"], body):
                    self.assertIn("## Learn evaluation evidence", text)
                    self.assertIn(case.base[:12], text)
                    self.assertIn("| #7 |", text)
                    self.assertIn("| baseline |", text)
                    self.assertIn("| variant |", text)
                    self.assertIn("200 tokens, $0.4000, 4 seconds", text)
                self.assertIn("learning proposal", review["prompt"])
                self.assertNotIn("GitHub issue #7", review["prompt"])
                self.assertNotIn("INCIDENT_IMPLEMENTATION_CRITERIA", review["prompt"])
                self.assertIn("publication: PASS", review["prompt"])
                self.assertIn("## Candidate review", body)
                self.assertIn("VERDICT: APPROVE", body)
                self.assertIn("merges after gate, reviewer APPROVE and CI", body)
                rows = lifecycle.read_events(cfg.factory / "events.jsonl")
                evaluated = {row["candidate"]: row["status"] for row in rows
                             if row["event"] == "learn-evaluation"}
                self.assertEqual(evaluated[helpful], "kept")
                self.assertEqual(evaluated["lessons" if helpful == "curate" else "curate"], "rejected")
                [record] = [row for row in rows if row["event"] == "learn-candidate-review"]
                self.assertTrue(record["accepted"])
                self.assertEqual(record["verdict"], "APPROVE")
                self.assertEqual(record["head"], git("rev-parse", f"refs/heads/{branch}"))
                self.assertEqual(record["base"], case.base)
                self.assertIn("200 tokens", record["proposal"])
                decisions = [row for row in rows if row["event"] == "decision"
                             and row["stage"] in ("learn", "curate")]
                input_kinds = {item["kind"] for decision in decisions for item in decision["inputs"]}
                self.assertIn("learn_evaluation", input_kinds)
                self.assertIn("candidate_review", input_kinds)
                self.assertFalse(any(row["event"] == "escalate" for row in rows))
                self.assertEqual(event["evaluation"], evaluated)

    def test_unsuccessful_candidate_review_is_retained_and_publishes_nothing(self) -> None:
        for helpful in ("curate", "lessons"):
            for reply, reason in (
                (REVISE, "REVISE"),
                ("Looks good", "unparsed_verdict"),
                ("VERDICT: APPROVE\nVERDICT: REVISE", "unparsed_verdict"),
                ("move-head", "state_changed"),
            ):
                with self.subTest(helpful=helpful, reason=reason), \
                     tempfile.TemporaryDirectory() as directory:
                    cfg, case, diff, captured, _ = self.scenario(directory, reply)
                    with mock.patch.object(dispatch, "push_and_pr") as publish:
                        event = self.run_proposal(cfg, case, diff, helpful=helpful)
                    self.assertTrue(captured.exists())
                    publish.assert_not_called()
                    rows = lifecycle.read_events(cfg.factory / "events.jsonl")
                    [record] = [row for row in rows if row["event"] == "learn-candidate-review"]
                    self.assertEqual(record["verdict"], "REVISE")
                    self.assertEqual(record["reason"], reason)
                    self.assertIn("200 tokens", record["proposal"])
                    if reason != "REVISE":
                        self.assertIn("Factory rejected reviewer evidence", record["findings"])
                    self.assertIsNone(event["branch"])
                    if helpful == "curate":
                        self.assertIn("rejected:", event["curate"])
                    self.assertFalse(any(row["event"] == "escalate" for row in rows))

    def test_candidate_gate_failure_skips_review_and_publication(self) -> None:
        for tier in ("attempt", "pr"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as directory:
                cfg, case, diff, captured, _ = self.scenario(
                    directory, APPROVE, gate_failure=tier,
                )
                with mock.patch.object(dispatch, "push_and_pr") as publish:
                    self.run_proposal(cfg, case, diff)
                publish.assert_not_called()
                self.assertFalse(captured.exists())
                rows = lifecycle.read_events(cfg.factory / "events.jsonl")
                [record] = [row for row in rows if row["event"] == "learn-candidate-review"]
                self.assertEqual(record["reason"], "gate_failed")
                self.assertIn("FAIL", record["findings"])
                self.assertFalse(record["accepted"])

    def test_not_tested_candidates_skip_candidate_review_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, case, diff, captured, _ = self.scenario(directory, APPROVE)
            with mock.patch.object(dispatch, "push_and_pr") as publish:
                event = self.run_proposal(cfg, case, diff, exhausted=True)
            publish.assert_not_called()
            self.assertFalse(captured.exists())
            self.assertEqual(event["evaluation"], {"lessons": "not_tested", "curate": "not_tested"})
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertFalse(any(row["event"] in ("learn-candidate-review", "escalate") for row in rows))

    def test_candidate_gate_moving_head_cannot_produce_review_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, case, diff, captured, _ = self.scenario(directory, APPROVE)
            run_gate = dispatch.run_gate

            def moving_gate(wt, name, **kwargs):
                result = run_gate(wt, name, **kwargs)
                if kwargs.get("tier") != "pr":
                    dispatch.run(["git", "commit", "--allow-empty", "-qm", "gate moved head"], cwd=wt)
                return result

            with mock.patch.object(dispatch, "push_and_pr") as publish, \
                 mock.patch.object(dispatch, "run_gate", side_effect=moving_gate):
                self.run_proposal(cfg, case, diff)
            publish.assert_not_called()
            self.assertFalse(captured.exists())
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            [record] = [row for row in rows if row["event"] == "learn-candidate-review"]
            self.assertEqual(record["reason"], "state_changed")
            self.assertNotEqual(record["head"], record["actual_head"])

    def test_evaluation_off_preserves_publication_without_candidate_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg, case, diff, captured, _ = self.scenario(directory, REVISE)
            cfg.learn_evaluate = False
            with mock.patch.object(dispatch, "push_and_pr", return_value=True) as publish, \
                 mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate, \
                 mock.patch.object(dispatch, "run_reviewer") as reviewer:
                event = self.run_proposal(cfg, case, diff, evaluate=None)
            self.assertEqual(publish.call_count, 2)
            self.assertEqual(gate.call_count, 2)
            reviewer.assert_not_called()
            self.assertFalse(captured.exists())
            self.assertNotIn("evaluation", event)
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertFalse(any(row["event"] in ("learn-evaluation", "learn-candidate-review")
                                 for row in rows))
