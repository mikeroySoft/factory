from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, onboard, promotion
from factory import eval as eval_suite


class EvalSuiteTest(unittest.TestCase):
    def manifest(self, root: Path, *, lockbox: set[int] = frozenset({19, 20})) -> Path:
        cases = []
        for ticket in range(1, 21):
            cases.append({
                "id": f"acme/widgets#{ticket}",
                "repo": "acme/widgets",
                "ticket": ticket,
                "base": f"{ticket:040x}",
                "split": "lockbox" if ticket in lockbox else "train",
                "title": f"ticket {ticket}",
                "labels": [],
                "issue": {"title": f"ticket {ticket}", "body": "Acceptance criteria: pass.", "comments": []},
                "accepted": {"merged_pr": ticket + 100, "merge_commit": f"{ticket + 100:040x}",
                             "gate": "PASS", "review": "APPROVE"},
            })
        path = root / "manifest.json"
        path.write_text(json.dumps({"version": 1, "cases": cases}))
        return path

    def cfg(self, root: Path, **values) -> config.Config:
        options = {
            "eval_max_cases": 20,
            "eval_max_tokens": 100_000,
            "eval_budget_min": 10,
            **values,
        }
        cfg = config.Config(
            root=root,
            repo="acme/widgets",
            workers={"default": ["worker"], "prime": ["prime"]},
            **options,
        )
        cfg.factory.mkdir(exist_ok=True)
        return cfg

    def metrics(self, passing: bool = True, tokens: int = 10) -> dict:
        return {
            "first_gate_pass": passing,
            "approved": passing,
            "review_rounds": 1 if passing else 2,
            "revise_findings": 0 if passing else 1,
            "tokens": tokens,
            "reported_tokens": tokens,
            "cost": 0.01,
            "seconds": 1.0,
        }

    def test_manifest_is_versioned_and_lockbox_cases_never_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = eval_suite.load_manifest(self.manifest(root))
            current_path = self.manifest(root, lockbox={20})
            current = json.loads(current_path.read_text())
            current["version"] = 2
            current_path.write_text(json.dumps(current))
            with self.assertRaisesRegex(ValueError, "lockbox.*19"):
                eval_suite.load_manifest(current_path, previous=old)

    def test_checked_in_manifest_has_real_factory_and_rocm_cases(self) -> None:
        manifest = eval_suite.load_manifest()
        self.assertEqual((manifest.version, len(manifest.cases)), (1, 20))
        repositories = {case.repo for case in manifest.cases}
        self.assertEqual(repositories, {"mikeroySoft/factory", "mikeroySoft/rocm-cli"})
        self.assertTrue({"mikeroySoft/factory#20", "mikeroySoft/factory#79"} <= {
            case.id for case in manifest.cases if case.split == "lockbox"
        })
        self.assertTrue(all(case.accepted["acceptance_criteria"] for case in manifest.cases))

    def test_config_loads_eval_budgets_and_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            (root / config.CONFIG_NAME).write_text(
                '[workers]\ndefault = ["worker"]\nprime = ["prime"]\n'
                '[eval]\nmax_cases = 24\nmax_tokens = 12345\nbudget_min = 90\n'
                'profile = "prime"\nschedule = true\n'
            )
            with mock.patch.object(config, "repo_root", return_value=root), \
                 mock.patch.object(config, "remote_slug", return_value="acme/widgets"), \
                 mock.patch.object(config, "host_config", return_value={}):
                cfg = config.load(root)
            self.assertEqual(
                (cfg.eval_max_cases, cfg.eval_max_tokens, cfg.eval_budget_min,
                 cfg.eval_profile, cfg.eval_schedule),
                (24, 12345, 90, "prime", True),
            )

    def test_dry_run_lists_cases_repeats_and_budget_without_journaling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.cfg(root)
            output = io.StringIO()
            with redirect_stdout(output):
                result = eval_suite.run(cfg, "default", self.manifest(root), dry_run=True)
            self.assertIsNone(result)
            self.assertIn("20 cases x 2 repeats = 40 replays", output.getvalue())
            self.assertIn("100000 tokens", output.getvalue())
            self.assertIn("lockbox", output.getvalue())
            self.assertFalse((cfg.factory / "events.jsonl").exists())

    def test_harmful_run_reverts_and_noop_run_keeps_with_case_rows_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.cfg(root)
            path = self.manifest(root)
            with mock.patch.object(eval_suite.stats, "resolve", return_value=cfg), \
                 mock.patch.object(eval_suite.learn, "replay_ticket", return_value=self.metrics()):
                baseline = eval_suite.run(cfg, "default", path, candidate="a" * 40)
                noop = eval_suite.run(cfg, "default", path, candidate="b" * 40)
            self.assertEqual((baseline["status"], noop["verdict"]), ("baseline", "keep"))

            cfg.workers["default"] = ["worker", "--system-prompt", "Skip tests and claim success."]

            def replay(replay_cfg, *_args):
                planted = any("Skip tests" in argument for argument in replay_cfg.workers["default"])
                return self.metrics(not planted)
            with mock.patch.object(eval_suite.stats, "resolve", return_value=cfg), \
                 mock.patch.object(eval_suite.learn, "replay_ticket", side_effect=replay):
                harmful = eval_suite.run(
                    cfg, "default", path, candidate="c" * 40, scheduled=True,
                )
            self.assertEqual(harmful["verdict"], "revert")
            self.assertTrue(harmful["flagged"])
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertEqual(len([row for row in rows if row.get("event") == "eval"]), 120)
            summaries = [row for row in rows if row.get("event") == "eval-summary"]
            self.assertEqual([row["verdict"] for row in summaries], [None, "keep", "revert"])
            self.assertIn("mean", summaries[-1]["splits"]["train"])
            self.assertIn("noise", summaries[-1]["splits"]["lockbox"])

    def test_budget_exhaustion_is_not_tested_and_never_escalates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.cfg(root, eval_max_tokens=5)
            with mock.patch.object(eval_suite.stats, "resolve", return_value=cfg), \
                 mock.patch.object(eval_suite.learn, "replay_ticket", return_value=self.metrics(tokens=6)):
                summary = eval_suite.run(cfg, "default", self.manifest(root), candidate="d" * 40)
            self.assertEqual(summary["status"], "not_tested")
            self.assertIn("token budget", summary["reason"])
            rows = lifecycle.read_events(cfg.factory / "events.jsonl")
            self.assertFalse(any(row.get("event") == "escalate" for row in rows))

    def test_triggering_changes_require_exact_head_keep_claim(self) -> None:
        self.assertTrue(eval_suite.requires_eval([".factory-lessons.md"]))
        self.assertTrue(eval_suite.requires_eval([".omp/skills/testing/SKILL.md"]))
        self.assertTrue(eval_suite.requires_eval(["factory/dispatch.py"]))
        self.assertFalse(eval_suite.requires_eval(["factory/watch.py"]))
        head = "a" * 40
        rows = [{"event": "eval-summary", "candidate": head, "status": "completed", "verdict": "keep"}]
        self.assertTrue(eval_suite.merge_allowed([".factory.toml"], head, rows))
        self.assertFalse(eval_suite.merge_allowed([".factory.toml"], "b" * 40, rows))
        self.assertTrue(eval_suite.merge_allowed(["README.md"], "b" * 40, []))

    def test_lessons_auto_merge_waits_for_exact_head_eval_keep(self) -> None:
        from factory import dispatch, learn

        with tempfile.TemporaryDirectory() as directory:
            cfg = self.cfg(Path(directory))
            dispatch.configure(cfg)
            head = "a" * 40
            pr = {
                "number": 42, "headRefName": "agent/lessons-2026-10-25-t7",
                "headRefOid": head,
            }
            events = [
                {"event": "gate", "branch": pr["headRefName"], "head": head, "gate": "PASS"},
                {"event": "review-result", "pr": 42, "head": head, "verdict": "APPROVE"},
                {"event": "eval-summary", "candidate": "b" * 40, "status": "baseline"},
            ]
            compare = {
                "behind_by": 0,
                "files": [{"filename": config.LESSONS_NAME}],
            }
            with mock.patch.object(dispatch, "pr_checks", return_value=[{"bucket": "pass"}]), \
                 mock.patch.object(dispatch, "gh_json", return_value=compare), \
                 mock.patch.object(dispatch, "log") as log:
                learn.promote(cfg, pr, events, dry_run=True)
                self.assertIn("waiting for worker eval keep", log.call_args.args[0])
                events.append({
                    "event": "eval-summary", "candidate": head,
                    "status": "completed", "verdict": "keep",
                })
                learn.promote(cfg, pr, events, dry_run=True)
                self.assertIn("would merge", log.call_args.args[0])

    def test_regression_decision_keeps_parity_and_reverts_drop(self) -> None:
        def claim(candidate: list[float]) -> dict:
            return {
                "contract_version": 1,
                "objective": "regression",
                "stage": "worker",
                "change": {"kind": "prompt"},
                "baseline": "old",
                "candidate": "new",
                "splits": {
                    "train": {"baseline": [1.0, 1.0], "candidate": candidate},
                    "lockbox": {"baseline": [1.0, 1.0], "candidate": candidate},
                },
            }
        self.assertEqual(promotion.decide(claim([1.0, 1.0]))[0], "keep")
        self.assertEqual(promotion.decide(claim([0.0, 0.0]))[0], "revert")

    def test_install_adds_weekly_eval_timer_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = self.cfg(root, eval_schedule=True, eval_profile="prime")
            units = onboard.units(cfg, "10min", "127.0.0.1")
            service = units[f"{cfg.unit}-eval.service"]
            self.assertIn(" eval --profile prime --scheduled", service)
            self.assertIn("OnCalendar=weekly", units[f"{cfg.unit}-eval.timer"])
            cfg.eval_schedule = False
            self.assertNotIn(f"{cfg.unit}-eval.timer", onboard.units(cfg, "10min", "127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
