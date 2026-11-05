"""#662: one resolved worker spec for launch, and unavailable or auth-failed workers are host defects."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from factory import config, dispatch, lifecycle, manage
from tests import test_frontier as frontier_suite
from tests import test_refresh_conflict as refresh_suite
from tests.test_settings import isolated_xdg, make_repo, snapshot, write_host

H = frontier_suite.H
AUTH_FAILED = [sys.executable, "-c", "print('Error during droid execution: Authentication failed'); raise SystemExit(1)"]
PROBE_FAILS = [sys.executable, "-c", "import sys; print('not logged in', file=sys.stderr); sys.exit(2)"]
PROBE_PASSES = [sys.executable, "-c", ""]
COMMIT = (
    "import subprocess\nfrom pathlib import Path\n"
    "Path('work.txt').write_text('work\\n')\n"
    "subprocess.run(['git', 'add', 'work.txt'], check=True)\n"
    "subprocess.run(['git', 'commit', '-qm', 'work'], check=True)\n"
)


class WorkerSpecTest(unittest.TestCase):
    def test_built_in_default_spec_is_what_launch_and_settings_show(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory))
            cfg = config.load(repo)
            spec = cfg.worker_spec("default", Path("/p"), Path("/w"))
            self.assertEqual(spec.argv, ["omp", "-p", "--model", config.DEFAULT_WORKER_MODEL, "--cwd", "/w", "@/p"])
            self.assertEqual((spec.model, spec.source, spec.wrap), (config.DEFAULT_WORKER_MODEL, "Built-in default", 0))
            [default] = [w for w in snapshot(repo)["agents"]["workers"] if w["label"] == "default"]
            self.assertEqual((default["model"], default["source"]), (config.DEFAULT_WORKER_MODEL, "Built-in default"))
            cfg.worker_wrap = ["sandbox", "--"]
            wrapped = cfg.worker_spec("default", Path("/p"), Path("/w"))
            self.assertEqual(wrapped.argv[:5], ["sandbox", "--", "omp", "-p", "--model"])
            self.assertEqual((wrapped.wrap, wrapped.argv.count("--model")), (2, 1))

    def test_spec_source_names_the_layer_that_defined_each_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)) as xdg:
            repo = make_repo(Path(directory), '[workers]\ndefault = ["agent", "--model", "a/b", "{prompt}"]\n')
            write_host(xdg, '[defaults.workers]\ndroid = { command = ["droid", "exec"], probe = ["droid", "whoami"] }\n')
            cfg = config.load(repo)
            self.assertEqual(cfg.worker_source, {"default": "Repository", "droid": "Host default"})
            self.assertEqual(cfg.worker_probe, {"droid": ["droid", "whoami"]})
            self.assertEqual(cfg.worker_spec("default", Path("/p"), Path("/w")).model, "a/b")
            self.assertIsNone(cfg.worker_spec("droid", Path("/p"), Path("/w")).model)
            write_host(xdg, '[defaults.workers]\ndroid = { command = ["droid"], probe = [] }\n')
            with self.assertRaisesRegex(config.ConfigError, "probe"):
                config.load(repo)

    def test_spec_model_and_source_follow_the_effective_launched_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)) as xdg:
            repo = make_repo(Path(directory), '[workers]\ndefault = { probe = ["true"] }\n'
                                              'scoped = ["worker", "--model", "{repo}", "{prompt}"]\n')
            write_host(xdg, '[defaults.workers]\ndefault = { command = ["host-worker", "--model", "host/model", "{prompt}"] }\n')
            cfg = config.load(repo)
            scoped = cfg.worker_spec("scoped", Path("/p"), Path("/w"))
            self.assertEqual(scoped.argv, ["worker", "--model", "acme/widgets", "/p"])
            self.assertEqual((scoped.model, cfg.worker_model("scoped")), ("acme/widgets", "acme/widgets"))
            # The repository only adds a probe; the launched command and its model are the host's.
            default = cfg.worker_spec("default", Path("/p"), Path("/w"))
            self.assertEqual((default.argv[0], default.model, default.source), ("host-worker", "host/model", "Host default"))
            self.assertEqual(cfg.worker_probe, {"default": ["true"]})
            shown = {w["label"]: (w["model"], w["source"]) for w in snapshot(repo)["agents"]["workers"]}
            self.assertEqual(shown, {"default": ("host/model", "Host default"), "scoped": ("acme/widgets", "Repository")})


class WorkerHealthTest(unittest.TestCase):
    """The git-backed ticket fixture of `test_refresh_conflict`, borrowed without its tests."""

    setUp = refresh_suite.RefreshConflictTest.setUp
    patched, events = refresh_suite.RefreshConflictTest.patched, refresh_suite.RefreshConflictTest.events

    def ticket(self, *labels: str, **workers: list[str]) -> None:
        commit = self.tmp / "commit_worker.py"
        commit.write_text(COMMIT)
        self.cfg.workers = {"default": [sys.executable, str(commit)], **workers}
        self.github.issue_labels = {config.LABEL_AGENT, *labels}
        self.github.assignees = []
        self.github.pr_labels = set()

    def process(self) -> bool | None:
        with self.patched(), mock.patch.object(dispatch, "run_gate", return_value=(True, "gate ok")) as gate, \
                mock.patch.object(dispatch, "run_worker", wraps=dispatch.run_worker) as launches:
            [ready] = dispatch.frontier()
            result = dispatch.process_ticket(ready, 30, False)
        self.gate, self.launches = gate, launches
        return result

    def test_auth_failed_worker_is_not_a_strike_and_the_attempt_falls_back_to_default(self) -> None:
        self.ticket("droid", droid=AUTH_FAILED)
        self.process()
        [infra] = self.events("worker-infra")
        self.assertEqual((infra["worker"], infra["reason"], infra["worker_exit"]), ("droid", "auth_failed", 1))
        self.assertIn("Authentication failed", infra["detail"])
        [health] = self.events("worker-health")
        self.assertEqual((health["worker"], health["available"], health["fallback"], health["source"]),
                         ("droid", False, "default", "launch"))
        [attempt] = self.events("attempt")
        self.assertEqual((attempt["worker"], attempt["fallback_from"], attempt["gate"]), ("default", "droid", "PASS"))
        self.assertEqual(self.events("escalate"), [])
        self.gate.assert_called_once()
        wt = dispatch.FACTORY / "wt-9"
        launched = self.launches.call_args_list[-1].args[0]
        self.assertEqual(launched, self.cfg.worker_spec("default", wt / ".factory-prompt.md", wt).argv)

    def test_auth_failed_default_gives_the_claim_back_without_an_attempt(self) -> None:
        self.ticket()
        self.cfg.workers["default"] = AUTH_FAILED
        self.assertIs(self.process(), False)
        self.assertEqual((self.events("attempt"), self.events("escalate")), ([], []))
        self.gate.assert_not_called()
        self.assertEqual(len(self.events("worker-infra")), 1)
        [health] = self.events("worker-health")
        self.assertEqual((health["worker"], health["available"], health["fallback"]), ("default", False, None))
        self.assertEqual(self.github.assignees, [])
        self.assertEqual(self.github.issue_labels, {config.LABEL_AGENT})

    def test_failing_probe_on_a_label_selected_profile_waits_without_claiming(self) -> None:
        self.ticket("gpu", prime=[sys.executable, "-c", ""])
        self.cfg.worker_select_label = {"prime": "gpu"}
        self.cfg.worker_probe = {"prime": PROBE_FAILS}
        dispatch.worker_health_pass()
        [health] = self.events("worker-health")
        self.assertEqual((health["worker"], health["available"], health["fallback"], health["source"]),
                         ("prime", False, None, "probe"))
        self.assertEqual(health["reason"], "probe exited 2: not logged in")
        self.assertIsNone(self.process())
        self.assertEqual(self.events("claimed"), [])
        self.assertFalse(any("--add-assignee" in cmd for cmd in self.github.calls))
        self.launches.assert_not_called()

    def test_probe_journals_only_transitions_and_the_manager_cannot_pick_an_unavailable_worker(self) -> None:
        self.ticket(droid=[sys.executable, "-c", ""])
        fix = 'DECISION: FIX\n{"worker": "droid", "guidance": "retry"}'
        self.assertEqual(manage.parse(fix, manage.selectable_workers(self.cfg), approval=True)[0], "FIX")
        dispatch.worker_health_pass()
        self.assertEqual(self.events("worker-health"), [])  # no probe configured: nothing runs or journals
        self.cfg.worker_probe = {"droid": PROBE_FAILS}
        for _ in range(2):
            dispatch.worker_health_pass()
        [down] = self.events("worker-health")
        self.assertEqual((down["available"], down["fallback"]), (False, "default"))
        self.assertNotIn("droid", manage.selectable_workers(self.cfg))
        self.assertEqual(manage.parse(fix, manage.selectable_workers(self.cfg), approval=True)[0], "HUMAN")
        dispatch.configure(self.cfg)  # a later pass starts from the journal, not process memory
        self.cfg.worker_probe = {"droid": PROBE_PASSES}
        dispatch.worker_health_pass()
        self.assertEqual([row["available"] for row in self.events("worker-health")], [False, True])
        self.assertIn("droid", manage.selectable_workers(self.cfg))

    def test_a_failed_worker_that_committed_is_ordinary_work(self) -> None:
        script = self.tmp / "commit_then_fail.py"
        script.write_text(COMMIT + "print('Authentication failed'); raise SystemExit(1)\n")
        self.ticket("droid", droid=[sys.executable, str(script)])
        self.process()
        self.assertEqual(self.events("worker-infra"), [])
        self.assertEqual([(a["worker"], a["worker_exit"]) for a in self.events("attempt")], [("droid", 1)])
        self.assertNotIn("droid", dispatch.UNAVAILABLE_WORKERS)
        self.assertTrue(all(row.get("event") != "worker-health" for row in lifecycle.read_events(dispatch.EVENTS)))

    def test_every_profile_is_health_checked_before_the_pass_even_without_a_probe(self) -> None:
        self.ticket("droid", droid=["factory-test-missing-worker", "exec"])
        dispatch.worker_health_pass()
        [down] = self.events("worker-health")
        self.assertEqual((down["worker"], down["available"], down["fallback"], down["source"]),
                         ("droid", False, "default", "probe"))
        self.assertIn("factory-test-missing-worker", down["reason"])
        self.assertNotIn("droid", manage.selectable_workers(self.cfg))
        self.process()
        [attempt] = self.events("attempt")
        self.assertEqual((attempt["worker"], attempt["fallback_from"], attempt["gate"]), ("default", "droid", "PASS"))
        self.assertEqual(self.events("worker-infra"), [])
        self.assertTrue(all(call.args[0][0] != "factory-test-missing-worker" for call in self.launches.call_args_list))

    def test_probe_output_is_bounded_and_its_last_line_is_the_reason(self) -> None:
        noisy = [sys.executable, "-c", "import sys; sys.stderr.write('x' * 300000 + '\\nlogin expired\\n'); sys.exit(3)"]
        self.ticket(droid=[sys.executable, "-c", ""])
        self.cfg.worker_probe = {"droid": noisy}
        dispatch.worker_health_pass()
        [down] = self.events("worker-health")
        self.assertEqual(down["reason"], "probe exited 3: login expired")


class ManagerFixInfraTest(unittest.TestCase):
    """The frontier fixture of `test_frontier`, borrowed without re-running its tests."""

    setUp = frontier_suite.FrontierTest.setUp
    read, fake_run = frontier_suite.FrontierTest.read, frontier_suite.FrontierTest.fake_run
    events, escalations = frontier_suite.FrontierTest.events, frontier_suite.FrontierTest.escalations
    approved_head, quiet_provider = frontier_suite.FrontierTest.approved_head, frontier_suite.FrontierTest.quiet_provider
    FIX: ClassVar[dict[str, str]] = {"worker": "droid", "guidance": "tighten the boundary"}

    def gh_json(self, args):
        if args[:2] == ["issue", "list"]:
            return [{"number": 79, "title": "Ticket", "body": "Scope", "labels": [{"name": "ready-for-human"}]}]
        return frontier_suite.FrontierTest.gh_json(self, args)

    def fix_decided(self) -> None:
        self.cfg.workers = {"default": ["omp"], "droid": ["droid", "exec"]}
        self.manager_output = "DECISION: FIX\n" + json.dumps(self.FIX)
        self.down = dispatch.WorkerUnavailable("worker `droid` is unavailable: auth_failed: Authentication failed")

    def manage_rows(self) -> list[tuple]:
        return [(e.get("head"), e["decision"], e["round"]) for e in self.events() if e.get("event") == "manage"]

    def assert_resumes_once_the_worker_is_healthy(self, run_pass, apply) -> None:
        self.assertEqual(apply.call_count, 1)
        dispatch.configure(self.cfg)
        dispatch.UNAVAILABLE_WORKERS.update(droid="auth_failed", default="auth_failed")
        run_pass()  # still no healthy worker: the FIX waits; nothing is re-applied
        self.assertEqual(apply.call_count, 1)
        dispatch.configure(self.cfg)  # the host was fixed
        run_pass()
        self.assertEqual(apply.call_count, 2)
        self.assertEqual(apply.call_args_list[1].args[2:5], ("FIX", apply.call_args_list[0].args[3], self.FIX))
        run_pass()  # a resumed FIX is taken up once
        self.assertEqual(apply.call_count, 2)

    def test_frontier_fix_that_found_no_healthy_worker_resumes_without_another_manager_round(self) -> None:
        self.fix_decided()
        self.cfg.manager_review = "all"
        self.quiet_provider()
        self.approved_head(H)
        with mock.patch.object(manage, "apply", side_effect=[self.down, None]) as apply:
            manage.frontier_pass()
            self.assert_resumes_once_the_worker_is_healthy(manage.frontier_pass, apply)
        self.assertEqual(self.manage_rows(), [(H, "FIX", 1)])
        self.assertEqual(len([cmd for cmd in self.mutations if cmd[1:3] == ["pr", "comment"]]), 1)
        self.assertEqual(self.escalations(), [])

    def test_escalation_fix_that_found_no_healthy_worker_resumes_without_another_manager_round(self) -> None:
        self.fix_decided()
        dispatch.record("claimed", ticket=79)
        dispatch.escalate(79, "gate failed", None, code="gate_failed")
        dispatch.configure(self.cfg)
        self.enterContext(mock.patch.object(manage, "human_activity", return_value=False))
        with mock.patch.object(manage, "apply", side_effect=[self.down, None]) as apply:
            manage.escalation_pass()
            self.assert_resumes_once_the_worker_is_healthy(manage.escalation_pass, apply)
        self.assertEqual(self.manage_rows(), [(None, "FIX", 1)])
        self.assertEqual(len(self.escalations()), 1)


if __name__ == "__main__":
    unittest.main()
