"""Host duration settings through real config loading and the PR gate caller."""
from __future__ import annotations

from unittest import mock

from factory import config, dispatch
from tests import test_mutation_duration_caller as duration
from tests import test_mutation_proof_config as real_config


class HostMutationDurationTest(real_config.RealConfigCallerTest):
    def prepare(self, host: str = "", *, tier: str = "High", opted: bool = True) -> None:
        host_path = config.host_config_path()
        host_path.parent.mkdir(parents=True, exist_ok=True)
        host_path.write_text(host)
        text = real_config.CANDIDATE if opted else real_config.BEFORE
        self.build(main_config=text, worktree_config=text)
        self.configure(tier=tier)

    def test_absent_duration_preserves_timeout_minus_reserve(self) -> None:
        self.prepare()
        seen = {}
        result = self.invoke([duration.clocked_runner(seen)])
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertIsNone(duration.flag_value(self.gate_cmds[0], "--mutation-duration"))
        self.assertEqual(self.children[0].timeouts, [3600])
        self.assertEqual((seen["clone"], seen["baseline"]), ([3540.0], [3540.0]))

    def test_host_duration_reaches_runner_and_preserves_cached_gate(self) -> None:
        self.prepare('[defaults.gate]\nmutation_duration = 5400\n')
        seen, cached = {}, {}
        result = self.invoke([duration.clocked_runner(seen)], pr_runs=cached)
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(duration.flag_value(self.gate_cmds[0], "--mutation-duration")[0], "5400")
        self.assertEqual(self.children[0].timeouts, [5400])
        self.assertEqual((seen["clone"], seen["baseline"]), ([5340.0], [5340.0]))
        self.assertEqual(self.invoke(pr_runs=cached)["verdict"], "APPROVE")
        self.assertEqual(len(self.gate_cmds), 1)

    def test_host_duration_reaches_moved_main_retry(self) -> None:
        self.prepare('[defaults.gate]\nmutation_duration = 5400\n')
        # Existing race detection owns the decision; exercise both real gate launches.
        with mock.patch.object(dispatch, "_resolved_commit", side_effect=["a" * 40, "b" * 40, "b" * 40]), \
                mock.patch.object(dispatch, "_mutation_proof", side_effect=[("STALE", None), ("PASS", None)]), \
                mock.patch.object(dispatch, "_moved_main_proof", return_value=True):
            result = self.invoke([duration.clocked_runner({})], [duration.clocked_runner({})])
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual([duration.flag_value(cmd, "--mutation-duration")[0] for cmd in self.gate_cmds],
                         ["5400", "5400"])
        self.assertEqual([child.timeouts for child in self.children], [[5400], [5400]])

    def test_repo_specific_host_duration_overrides_host_default(self) -> None:
        self.prepare('[defaults.gate]\nmutation_duration = 5400\n'
                     '[repo."acme/widgets".gate]\nmutation_duration = 60.25\n')
        seen = {}
        result = self.invoke([duration.clocked_runner(seen)])
        self.assertEqual(result["verdict"], "APPROVE")
        self.assertEqual(self.children[0].timeouts, [60.25])
        self.assertEqual(seen["clone"], [0.25])

    def test_manual_duration_takes_precedence(self) -> None:
        self.prepare('[defaults.gate]\nmutation_duration = 5400\n')
        self.scripts.append([duration.clocked_runner({})])
        result = dispatch.pr_gate_and_review(self.root, duration.N, "attempt", self.head, {},
                                             mutation_duration=1800)
        self.assertEqual(result[0], "APPROVE")
        self.assertEqual(self.children[0].timeouts, [1800])

    def test_host_duration_does_not_enable_proof_on_other_paths(self) -> None:
        self.prepare('[defaults.gate]\nmutation_duration = 5400\n', tier="Medium")
        self.assertEqual(self.invoke([real_config.NO_PROOF_PASS])["verdict"], "APPROVE")
        self.assertIsNone(duration.flag_value(self.gate_cmds[0], "--mutation-duration"))
        self.assertEqual(self.children[0].timeouts, [3600])

    def test_invalid_host_durations_are_refused(self) -> None:
        self.prepare()
        host_path = config.host_config_path()
        for section in ('defaults', 'repo."acme/widgets"'):
            for value in ('0', '-1', '59.99', '60', 'nan', 'inf', '-inf', 'true', '"5400"', '[]', '{}'):
                with self.subTest(section=section, value=value):
                    host_path.write_text(f'[{section}.gate]\nmutation_duration = {value}\n')
                    with self.assertRaisesRegex(config.ConfigError, r"gate\.mutation_duration"):
                        real_config.REAL_LOAD(self.root)


# The parent's own cases run in their original module, not again here.
for _name in dir(real_config.RealConfigCallerTest):
    if _name.startswith("test_") and _name not in vars(HostMutationDurationTest):
        setattr(HostMutationDurationTest, _name, None)
