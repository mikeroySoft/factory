"""CI and the factory gate must run the same pinned Ruff command (#207)."""

import shlex
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ruff(cmd):
    return any(token.startswith("ruff") for token in cmd)


def ci_ruff_commands():
    lines = (ROOT / ".github/workflows/ci.yml").read_text().splitlines()
    cmds = (shlex.split(line.strip().removeprefix("- ").removeprefix("run:")) for line in lines)
    return [cmd for cmd in cmds if ruff(cmd)]


def gate_ruff_commands():
    checks = tomllib.loads((ROOT / ".factory.toml").read_text())["gate"]["check"]
    return [check["run"] for check in checks if ruff(check["run"])]


class RuffPinTest(unittest.TestCase):
    def test_gate_runs_every_ci_ruff_command(self):
        ci = ci_ruff_commands()
        self.assertTrue(ci, "CI runs no Ruff command")
        self.assertEqual(sorted(ci), sorted(gate_ruff_commands()))


if __name__ == "__main__":
    unittest.main()
