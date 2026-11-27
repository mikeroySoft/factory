"""Replay reaches focused kill tests without changing full-proof ordering."""
from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from scripts import mutation


class ReplayTestOrderTest(unittest.TestCase):
    def run_order(self, *, replay=False, kill=False):
        executed = []

        def case(module, label):
            def test(self):
                executed.append(label)
                if kill and label == "focused":
                    self.fail("focused mutant kill")
            cls = type(label, (unittest.TestCase,), {"__module__": module, "test_case": test})
            return cls("test_case")

        suite = unittest.TestSuite([
            case("test_a_broad", "broad"),
            case("test_risk_other", "risk"),
            case("test_z_focused", "focused"),
            case("test_unrelated", "unrelated"),
        ])
        feature_map = (
            "| broad | `factory/example.py` | `tests/test_a_broad.py`, "
            "`tests/test_b_other.py` | test |\n"
            "| focused | `factory/example.py` | `tests/test_z_focused.py` | test |\n"
        )
        with mock.patch.object(mutation.unittest.defaultTestLoader, "discover", return_value=suite), \
                mock.patch.object(mutation.Path, "read_text", return_value=feature_map), \
                contextlib.redirect_stderr(io.StringIO()):
            code = mutation.run_tests("factory/example.py", replay=replay)
        return code, executed

    def test_replay_stops_at_focused_kill_before_broad_or_unrelated_risk_tests(self):
        self.assertEqual(self.run_order(replay=True, kill=True), (1, ["focused"]))

    def test_replay_requires_all_tests_to_declare_survivor(self):
        self.assertEqual(self.run_order(replay=True),
                         (0, ["focused", "broad", "risk", "unrelated"]))

    def test_full_proof_retains_risk_first_related_then_unrelated_order(self):
        self.assertEqual(self.run_order(), (0, ["risk", "broad", "focused", "unrelated"]))
