"""Pre-handoff mutation findings cannot masquerade as a clean worker finish."""
from __future__ import annotations

import os
import unittest
from contextlib import chdir
from unittest import mock

import test_dispatch_lifecycle as fixtures

from factory import config, dispatch, lifecycle

SURVIVOR = "factory/example.py:2: NumberReplacer `1` -> `2`"
SURVIVORS = SURVIVOR + "\nran 1 of 1 mutants in 1s: 1 survived; 1 survived\nworker mutation: FAIL\n"
PASS = "ran 1 of 1 mutants in 1s: 1 killed; 0 survived\nworker mutation: PASS\n"


class PreHandoffMutationTest(unittest.TestCase):
    setUp = fixtures.LocalCLI.setUp
    git = fixtures.LocalCLI.git
    write_config = fixtures.LocalCLI.write_config

    def worker_round(self, mutation_report, *, expired=False, blocking="", stale=None):
        (self.repo / ".factory/logs").mkdir(parents=True)
        # The real worker process produces the same durable report as the runner.
        script = self.repo / "scripts/mutation.py"
        script.parent.mkdir()
        script.write_text("# mutation runner exists in this repository\n")
        self.git("add", "scripts/mutation.py")
        self.git("commit", "-qm", "mutation workflow fixture")
        handoff = "## Changed\nDone.\n## Still unverified\nNone.\n" + blocking
        self.worker.write_text(
            fixtures.WORKER + "\nimport subprocess\n"
            "subprocess.run(['git', 'add', 'change.txt'], check=True)\n"
            "subprocess.run(['git', 'commit', '-qm', 'finished'], check=True)\n"
            "Path('.factory').mkdir(exist_ok=True)\n"
            + (f"Path('.factory/mutation-7.md').write_text({mutation_report!r})\n"
               if mutation_report is not None else "")
            + f"Path('.factory/handoff-7.md').write_text({handoff!r})\n"
        )
        clock = [0]
        original_worker = dispatch.run_worker

        def worker(*args, **kwargs):
            result = original_worker(*args[:4], float("inf"), **kwargs)
            self.assertEqual(result[0], 0, args[2].read_text())
            clock[0] = 61 if expired else 1
            return result

        with chdir(self.repo), mock.patch.dict(os.environ, self.env), \
                mock.patch.object(dispatch.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(dispatch, "run_worker", side_effect=worker), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "attempt gate passed")) as gate:
            dispatch.configure(config.load(self.repo))
            if stale is not None:
                (self.repo / ".factory/mutation-7.md").write_text(stale)
            result = dispatch.worker_round(7, self.repo, set(), "local change", "", 1, 60)
        return result, gate

    def test_known_survivors_block_expired_clean_finish_and_are_carried(self):
        (ok, report, _, _), gate = self.worker_round(SURVIVORS, expired=True)
        self.assertFalse(ok, report)
        gate.assert_not_called()
        self.assertIn("budget exceeded", report)
        self.assertIn(SURVIVOR, report)
        handoff = (self.repo / ".factory/handoff-7.md").read_text()
        self.assertIn("## Blocking mutation findings", handoff)
        self.assertIn(SURVIVOR, handoff)
        self.assertFalse(dispatch._handoff_done(self.repo / ".factory/handoff-7.md"))

    def test_on_time_survivors_are_blocking_without_overruling_attempt_gate(self):
        (ok, report, _, _), gate = self.worker_round(SURVIVORS)
        self.assertTrue(ok, report)
        gate.assert_called_once()
        self.assertIn(SURVIVOR, report)
        attempt = next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "attempt")
        self.assertEqual(attempt["gate"], "PASS")
        self.assertFalse(attempt["finished"])
        self.assertFalse(dispatch._handoff_done(self.repo / ".factory/handoff-7.md"))

    def test_incomplete_trials_block_without_becoming_survivors(self):
        incomplete = "ran 0 of 2 mutants in 1s: none; 0 survived\nincomplete: 1 mutants not started and 1 cut short by the 1s deadline\nworker mutation: FAIL\n"
        (ok, report, _, _), gate = self.worker_round(incomplete, expired=True)
        self.assertFalse(ok, report)
        gate.assert_not_called()
        self.assertIn("incomplete: 1 mutants not started and 1 cut short", report)
        self.assertNotIn(SURVIVOR, report)

    def test_on_time_incomplete_evidence_is_carried_without_failing_attempt_gate(self):
        incomplete = (
            "ran 0 of 2 mutants in 1s: none; 0 survived\n"
            "incomplete: 1 mutants not started and 1 cut short by the 1s deadline\n"
            "worker mutation: FAIL\n"
        )
        (ok, report, _, _), gate = self.worker_round(incomplete)
        self.assertTrue(ok, report)
        gate.assert_called_once()
        self.assertIn("attempt gate passed", report)
        self.assertIn(incomplete.strip(), report)
        handoff_path = self.repo / ".factory/handoff-7.md"
        self.assertIn(incomplete.strip(), handoff_path.read_text())
        self.assertFalse(dispatch._handoff_done(handoff_path))
        attempt = next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "attempt")
        self.assertEqual(attempt["gate"], "PASS")
        self.assertFalse(attempt["finished"])

    def test_stale_passing_report_cannot_finish_missing_current_run(self):
        (ok, report, _, _), gate = self.worker_round(None, expired=True, stale=PASS)
        self.assertFalse(ok, report)
        gate.assert_not_called()
        self.assertIn("missing pre-handoff mutation report", report)

    def test_passing_mutation_still_allows_clean_finish_after_deadline(self):
        (ok, report, _, _), gate = self.worker_round(PASS, expired=True)
        self.assertTrue(ok, report)
        gate.assert_called_once()
        attempt = next(row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == "attempt")
        self.assertTrue(attempt["finished"])

    def test_separate_blocking_section_prevents_clean_finish(self):
        (ok, report, _, _), gate = self.worker_round(
            PASS, expired=True, blocking="## Blocking\n- " + SURVIVOR + "\n",
        )
        self.assertFalse(ok, report)
        gate.assert_not_called()

    def test_prompt_runs_mutation_before_finish_and_forbids_post_handoff_rerun(self):
        dispatch.configure(config.load(self.repo))
        with mock.patch.object(dispatch, "gh_json", return_value={
            "title": "change", "body": "acceptance", "comments": [],
        }), mock.patch.object(dispatch, "admit_plan", return_value=None), \
                mock.patch.object(dispatch.brief, "ensure", return_value=""):
            prompt = dispatch.build_prompt(7, self.repo, attempt=1, labels=set())
        self.assertIn("scripts/mutation.py --working-tree --blocking --report .factory/mutation-7.md", prompt)
        self.assertIn("before the final commit and handoff", prompt)
        self.assertIn("observable outcome", prompt)
        self.assertIn("Blocking mutation findings", prompt)
        self.assertIn("Do not re-run the full gate or mutation check after the final commit or handoff", prompt)
        self.assertIn("1140", prompt)
        self.assertIn("attempt's wall-clock", prompt)
        self.assertIn("incomplete", prompt)
        self.assertIn("sequentially, never concurrently", prompt)
        self.assertIn("60 seconds reserved for the final commit, handoff", prompt)


if __name__ == "__main__":
    unittest.main()
