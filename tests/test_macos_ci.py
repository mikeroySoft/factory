"""The required gate must fail closed unless the native macOS lane succeeds."""

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MacosRequiredCheckTest(unittest.TestCase):
    def setUp(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.gate = workflow.split("\n  gate:\n", 1)[1]
        self.require_macos = self.gate.split("- name: Require macOS\n", 1)[1]

    def test_gate_runs_after_every_macos_conclusion(self):
        header = self.gate.split("    steps:\n", 1)[0]
        self.assertIn("    needs: macos\n", header)
        self.assertIn("    if: ${{ always() }}\n", header)
        self.assertNotIn("continue-on-error:", self.gate)

    def test_macos_requirement_runs_even_after_cancellation(self):
        condition = next(
            line.strip() for line in self.require_macos.splitlines()
            if line.strip().startswith("if:")
        )
        self.assertEqual(condition, "if: ${{ always() }}")

    def test_only_successful_macos_execution_satisfies_gate(self):
        self.assertIn("MACOS_RESULT: ${{ needs.macos.result }}", self.require_macos)
        command = next(
            line.strip().removeprefix("run: ")
            for line in self.require_macos.splitlines()
            if line.strip().startswith("run: ")
        )
        for conclusion in ("success", "failure", "cancelled", "skipped", ""):
            with self.subTest(conclusion=conclusion):
                result = subprocess.run(
                    ["/bin/sh", "-c", command],
                    cwd=ROOT,
                    env={**os.environ, "MACOS_RESULT": conclusion},
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode == 0, conclusion == "success")


if __name__ == "__main__":
    unittest.main()
