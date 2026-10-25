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


class LearnEvaluationTest(unittest.TestCase):
    def test_config_is_off_by_default_and_loads_strict_budgets(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".git").mkdir()
            with mock.patch.object(config, "repo_root", return_value=root), \
                 mock.patch.object(config, "remote_slug", return_value="acme/widgets"), \
                 mock.patch.object(config, "host_config", return_value={}):
                cfg = config.load(root)
                self.assertFalse(cfg.learn_evaluate)
                (root / config.CONFIG_NAME).write_text(
                    "[learn]\nevaluate = true\nmax_tickets = 3\nmax_tokens = 12000\nbudget_min = 45\n"
                )
                cfg = config.load(root)
        self.assertEqual(
            (cfg.learn_evaluate, cfg.learn_max_tickets, cfg.learn_max_tokens, cfg.learn_budget_min),
            (True, 3, 12000, 45),
        )

    def test_only_helpful_candidate_is_kept_and_both_outcomes_are_journaled(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                manager=["manager"],
                learn_evaluate=True,
                learn_max_tickets=1,
                learn_max_tokens=1000,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            events = cfg.factory / "events.jsonl"
            dispatch.configure(cfg)
            case = learn.ReplayCase(
                ticket=7,
                base="a" * 40,
                title="fix parser",
                labels=("default",),
                issue={"title": "fix parser", "body": "Make it pass.", "comments": []},
            )
            candidates = [
                learn.Candidate("lessons", "harmful"),
                learn.Candidate("curate", "helpful"),
            ]
            metrics = {
                None: {"first_gate_pass": False, "approved": True, "review_rounds": 2,
                       "revise_findings": 1, "tokens": 100, "cost": 0.2, "seconds": 2.0},
                "lessons": {"first_gate_pass": False, "approved": False, "review_rounds": 3,
                            "revise_findings": 2, "tokens": 100, "cost": 0.2, "seconds": 2.0},
                "curate": {"first_gate_pass": True, "approved": True, "review_rounds": 1,
                           "revise_findings": 0, "tokens": 100, "cost": 0.2, "seconds": 2.0},
            }

            def replay(_cfg, replay_case, candidate, _deadline):
                self.assertEqual(replay_case, case)
                return metrics[candidate.kind if candidate else None]

            with mock.patch.object(learn, "replay_ticket", side_effect=replay):
                results = learn.evaluate_candidates(cfg, [case], candidates)

            self.assertEqual(results["lessons"]["status"], "rejected")
            self.assertEqual(results["curate"]["status"], "kept")
            rows = lifecycle.read_events(events)
            evaluated = [row for row in rows if row.get("event") == "learn-evaluation"]
            self.assertEqual([(row["candidate"], row["status"]) for row in evaluated],
                             [("lessons", "rejected"), ("curate", "kept")])
            self.assertFalse(any(row.get("event") == "escalate" for row in rows))
            evidence = learn.evaluation_markdown(results["curate"])
            self.assertIn("| #7 |", evidence)
            self.assertIn("aaaaaaaaaaaa", evidence)
            self.assertIn("200 tokens", evidence)
            self.assertIn("$0.4000", evidence)

    def test_budget_exhaustion_is_not_tested_without_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                learn_evaluate=True,
                learn_max_tickets=1,
                learn_max_tokens=50,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(7, "b" * 40, "t", (), {"title": "t", "body": "b", "comments": []})
            candidate = learn.Candidate("lessons", "candidate")
            replay = mock.Mock(return_value={
                "first_gate_pass": True,
                "approved": True,
                "review_rounds": 1,
                "revise_findings": 0,
                "tokens": 60,
                "cost": None,
                "seconds": 1.0,
            })
            with mock.patch.object(learn, "replay_ticket", replay):
                result = learn.evaluate_candidates(cfg, [case], [candidate])["lessons"]
            self.assertEqual(result["status"], "not_tested")
            self.assertIn("token budget", result["reason"])
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertEqual(rows[-1]["status"], "not_tested")
            self.assertFalse(any(row.get("event") == "escalate" for row in rows))

    def test_wall_time_exhaustion_after_variant_replay_is_not_tested(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                learn_evaluate=True,
                learn_max_tickets=1,
                learn_max_tokens=1000,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(7, "b" * 40, "t", (), {"title": "t", "body": "b", "comments": []})
            metrics = {
                "first_gate_pass": True,
                "approved": True,
                "review_rounds": 1,
                "revise_findings": 0,
                "tokens": 10,
                "cost": None,
                "seconds": 60.0,
            }

            with mock.patch.object(learn, "replay_ticket", return_value=metrics), \
                 mock.patch.object(learn.time, "monotonic", side_effect=[0, 0, 0, 61]):
                result = learn.evaluate_candidates(
                    cfg,
                    [case],
                    [learn.Candidate("lessons", "candidate")],
                )["lessons"]

            self.assertEqual(result["status"], "not_tested")
            self.assertIn("wall-time budget", result["reason"])

    def test_learn_proposes_only_helpful_curate_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                manager=["manager"],
                learn_evaluate=True,
                learn_max_tickets=1,
                learn_max_tokens=1000,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(
                7, "a" * 40, "fix parser", (),
                {"title": "fix parser", "body": "Make it pass.", "comments": []},
            )
            baseline = {"first_gate_pass": False, "approved": True, "review_rounds": 2,
                        "revise_findings": 1, "tokens": 100, "cost": 0.2, "seconds": 2.0}
            variants = {
                "lessons": {"first_gate_pass": False, "approved": False, "review_rounds": 3,
                            "revise_findings": 2, "tokens": 100, "cost": 0.2, "seconds": 2.0},
                "curate": {"first_gate_pass": True, "approved": True, "review_rounds": 1,
                           "revise_findings": 0, "tokens": 100, "cost": 0.2, "seconds": 2.0},
            }

            def replay(_cfg, _case, candidate, _deadline):
                return baseline if candidate is None else variants[candidate.kind]

            diff = (
                "diff --git a/AGENTS.md b/AGENTS.md\n"
                "new file mode 100644\n--- /dev/null\n+++ b/AGENTS.md\n"
                "@@ -0,0 +1 @@\n+helpful\n"
            )
            with mock.patch.object(learn, "evidence", return_value=([7], "incident")), \
                 mock.patch.object(learn, "replay_cases", return_value=[case]), \
                 mock.patch.object(learn, "propose", return_value=(["harmful"], diff)), \
                 mock.patch.object(learn, "replay_ticket", side_effect=replay), \
                 mock.patch.object(learn, "chore_pr") as proposed:
                event = learn.run(cfg, evaluate=True)

            self.assertEqual(event["evaluation"], {"lessons": "rejected", "curate": "kept"})
            proposed.assert_called_once()
            self.assertIn("curate harness context", proposed.call_args.args[3])
            self.assertIn("## Learn evaluation evidence", proposed.call_args.args[4])
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertEqual(
                [(row["candidate"], row["status"]) for row in rows if row["event"] == "learn-evaluation"],
                [("lessons", "rejected"), ("curate", "kept")],
            )

    def test_sandboxed_replays_keep_only_helpful_change_and_block_pushes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            repo.mkdir()

            def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
                return subprocess.run(
                    ["git", *args], cwd=repo, text=True, capture_output=True, check=check,
                )

            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            worker = repo / "worker.py"
            worker.write_text(
                "import json,subprocess\n"
                "from pathlib import Path\n"
                "prompt=Path('.factory-prompt.md').read_text()\n"
                "lessons=Path('.factory-lessons.md').read_text() if Path('.factory-lessons.md').exists() else ''\n"
                "rules=Path('AGENTS.md').read_text() if Path('AGENTS.md').exists() else ''\n"
                "value='harmful' if 'harmful' in lessons else 'helpful' if 'helpful' in rules else "
                "'revised' if 'Reviewer findings' in prompt else 'baseline'\n"
                "Path('solution.txt').write_text(value+'\\n')\n"
                "subprocess.run(['git','push','origin','HEAD:refs/heads/replay-leak'],check=False)\n"
                "print(json.dumps({'type':'usage','prompt':5,'completion':5,'cost':0.01}))\n"
            )
            (repo / "check.py").write_text(
                "from pathlib import Path\n"
                "raise SystemExit(Path('solution.txt').read_text().strip() == 'harmful')\n"
            )
            (repo / config.CONFIG_NAME).write_text(
                '[repo]\nslug = "acme/widgets"\n'
                '[[gate.check]]\nname = "solution"\nrun = ['
                + json.dumps(sys.executable) + ', "check.py"]\n'
                '[leak_scan]\npattern = ""\n'
            )
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            base = git("rev-parse", "HEAD").stdout.strip()

            binary = root / "bin"
            binary.mkdir()
            reviewer = binary / "omp"
            prompts = root / "review-prompts"
            reviewer.write_text(
                "#!" + sys.executable + "\n"
                "import json,os\n"
                "from pathlib import Path\n"
                "prompt=__import__('sys').argv[-1]\n"
                "with Path(os.environ['REVIEW_PROMPTS']).open('a') as f: f.write(prompt+'\\0')\n"
                "value=Path('snapshot/solution.txt').read_text().strip()\n"
                "text=('- solution.txt:1: baseline needs revision\\nVERDICT: REVISE' "
                "if value == 'baseline' else 'VERDICT: APPROVE')\n"
                "print(json.dumps({'type':'message_end','message':{'role':'assistant','content':text,"
                "'model':'fixture','usage':{'input':5,'output':5}}}))\n"
            )
            reviewer.chmod(0o755)
            cfg = config.Config(
                root=repo,
                repo="acme/widgets",
                workers={"default": [sys.executable, "worker.py"]},
                reviewer=[str(reviewer), "{prompt}"],
                manager=["manager"],
                signoff=False,
                review_rounds=1,
                learn_evaluate=True,
                learn_max_tickets=1,
                learn_max_tokens=1000,
                learn_budget_min=1,
            )
            cfg.worker_usage["default"] = config.usage_spec("default", {
                "match": {"type": "usage"},
                "prompt_tokens": "prompt",
                "completion_tokens": "completion",
                "cost": "cost",
            })
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(
                7, base, "fix parser", (),
                {"title": "fix parser", "body": "Produce a reviewed solution.", "comments": []},
            )
            candidates = [
                learn.Candidate("lessons", "harmful\n"),
                learn.Candidate(
                    "curate",
                    "diff --git a/AGENTS.md b/AGENTS.md\n"
                    "new file mode 100644\n--- /dev/null\n+++ b/AGENTS.md\n"
                    "@@ -0,0 +1 @@\n+helpful\n",
                ),
            ]
            cfg.worker_transcript["default"] = "omp-session"
            project = Path(__file__).resolve().parents[1]
            with mock.patch.dict(
                os.environ,
                {"PYTHONPATH": str(project), "REVIEW_PROMPTS": str(prompts)},
            ), mock.patch.object(
                dispatch, "_with_transcript", side_effect=lambda argv, *_args: (argv, None),
            ) as transcripts:
                results = learn.evaluate_candidates(cfg, [case], candidates)

            self.assertTrue(transcripts.call_args_list, results)
            self.assertTrue(all(call.args[2:] == ("omp-session", 0)
                                for call in transcripts.call_args_list))

            self.assertEqual(
                (results["lessons"]["status"], results["curate"]["status"]),
                ("rejected", "kept"),
                results,
            )
            self.assertNotIn("refs/heads/replay-leak", git("show-ref", check=False).stdout)
            self.assertFalse((repo / "solution.txt").exists())
            self.assertIn("## Learn replay context", prompts.read_text())
            self.assertNotIn("Candidate arm:", prompts.read_text())
            self.assertNotIn("recorded baseline evidence", prompts.read_text())
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertEqual([row["event"] for row in rows], ["learn-evaluation", "learn-evaluation"])
            self.assertFalse(any(row.get("event") == "escalate" for row in rows))

    def test_replay_cases_keep_the_first_claimed_pre_work_revision(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(root=root, repo="acme/widgets")
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            issue = {"title": "fix parser", "body": "Make it pass.", "comments": []}
            for base in ("a" * 40, "b" * 40):
                lifecycle.append(
                    cfg.factory / "events.jsonl",
                    {
                        "event": "claimed",
                        "ticket": 7,
                        "title": "fix parser",
                        "labels": ["default"],
                        "base": base,
                        "issue": issue,
                    },
                )

            [case] = learn.replay_cases([7], 1)

            self.assertEqual(case.base, "a" * 40)

    def test_unreported_token_usage_does_not_disqualify_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                learn_max_tickets=1,
                learn_max_tokens=1000,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(
                7,
                "a" * 40,
                "fix parser",
                (),
                {"title": "fix parser", "body": "Make it pass.", "comments": []},
            )
            baseline = {
                "first_gate_pass": False,
                "approved": True,
                "review_rounds": 2,
                "revise_findings": 1,
                "tokens": None,
                "cost": None,
                "seconds": 1.0,
            }
            variant = {
                **baseline,
                "first_gate_pass": True,
                "review_rounds": 1,
                "revise_findings": 0,
            }

            with mock.patch.object(
                learn,
                "replay_ticket",
                side_effect=lambda _cfg, _case, candidate, _deadline: (
                    baseline if candidate is None else variant
                ),
            ):
                result = learn.evaluate_candidates(
                    cfg,
                    [case],
                    [learn.Candidate("lessons", "helpful")],
                )["lessons"]

            self.assertEqual(result["status"], "kept")
            self.assertIsNone(result["cost"]["tokens"])

    def test_revise_finding_count_ignores_prose_and_optional_items(self) -> None:
        findings = """Review summary.

## Required fixes

1. **Reject empty input** (`parser.py:12`)

   Empty values currently reach the decoder.

2. **Preserve the error** (`parser.py:30`)

   The replacement loses the original context.

## Optional suggestions

1. Rename the helper (`parser.py:8`).
2. Add an explanatory comment (`parser.py:9`).

VERDICT: REVISE
"""

        self.assertEqual(learn._finding_count(findings), 2)

    def test_evidence_excludes_frozen_issue_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(root=root, repo="acme/widgets")
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            lifecycle.append(
                cfg.factory / "events.jsonl",
                {
                    "event": "claimed",
                    "ticket": 7,
                    "title": "fix parser",
                    "at": "2026-01-01T00:00:00Z",
                    "issue": {
                        "title": "fix parser",
                        "body": "frozen-body-must-not-enter-lessons",
                        "comments": ["frozen-comment-must-not-enter-lessons"],
                    },
                },
            )
            lifecycle.append(
                cfg.factory / "events.jsonl",
                {"event": "merged", "ticket": 7, "title": "fix parser", "at": "2026-01-02T00:00:00Z"},
            )

            with mock.patch.object(learn.stats, "escapes", return_value=[]):
                _, prompt_evidence = learn.evidence(1)

            self.assertNotIn("frozen-body-must-not-enter-lessons", prompt_evidence)
            self.assertNotIn("frozen-comment-must-not-enter-lessons", prompt_evidence)

    def test_no_candidates_skip_baseline_replays(self) -> None:
        cfg = config.Config(root=Path("/tmp"), repo="acme/widgets")
        case = learn.ReplayCase(7, "a" * 40, "t", (), {"title": "t", "body": "b"})
        with mock.patch.object(learn, "replay_ticket") as replay:
            self.assertEqual(learn.evaluate_candidates(cfg, [case], []), {})
        replay.assert_not_called()


    def test_reported_partial_usage_counts_toward_token_budget(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = config.Config(
                root=root,
                repo="acme/widgets",
                learn_max_tickets=1,
                learn_max_tokens=50,
                learn_budget_min=1,
            )
            cfg.factory.mkdir()
            dispatch.configure(cfg)
            case = learn.ReplayCase(
                7,
                "a" * 40,
                "fix parser",
                (),
                {"title": "fix parser", "body": "Make it pass.", "comments": []},
            )
            replay = mock.Mock(return_value={
                "first_gate_pass": True,
                "approved": True,
                "review_rounds": 1,
                "revise_findings": 0,
                "tokens": None,
                "reported_tokens": 60,
                "cost": None,
                "seconds": 1.0,
            })

            with mock.patch.object(learn, "replay_ticket", replay):
                result = learn.evaluate_candidates(
                    cfg,
                    [case],
                    [learn.Candidate("lessons", "candidate")],
                )["lessons"]

            self.assertEqual(result["status"], "not_tested")
            self.assertIn("token budget", result["reason"])



if __name__ == "__main__":
    unittest.main()
