from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EXP = Path(__file__).resolve().parents[1] / "experiments" / "reviewer-calibration"


class ReviewerCalibrationStdinTest(unittest.TestCase):
    def test_large_prompt_reaches_stdin_not_argv(self) -> None:
        with mock.patch.object(sys, "path", [str(EXP), *sys.path]):
            spec = importlib.util.spec_from_file_location("calibration_stdin_tools", EXP / "run_tools.py")
            tools = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tools)

        # Exceed the host's total argv budget, not just Linux's per-argument limit.
        prompt = "review this diff:\n" + "x" * (os.sysconf("SC_ARG_MAX") + 1) + "\n"
        run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "receipt.json"
            omp = root / "omp"
            omp.write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"pathlib.Path({str(receipt)!r}).write_text(json.dumps({{\n"
                "    'argv': sys.argv[1:], 'stdin': sys.stdin.read(),\n"
                "}))\n"
                "print('VERDICT: APPROVE')\n"
            )
            omp.chmod(0o755)

            def launch(argv, **kwargs):
                if argv[0] == "omp":
                    return run(argv, **kwargs)
                self.assertEqual(argv[0], "git")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with (
                mock.patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}),
                mock.patch.object(tools, "worktree", side_effect=lambda case, dest: dest.mkdir()),
                mock.patch.object(tools, "capture", return_value=prompt),
                mock.patch.object(tools, "contract_commit", return_value="fixture-commit"),
                mock.patch.object(tools.subprocess, "run", side_effect=launch),
            ):
                result = tools.review("fixture-contract", "fixture-case", 1, "fixture/model")

            received = json.loads(receipt.read_text())
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(received["stdin"], prompt)
        self.assertEqual(received["argv"], [
            "-p", "--mode", "json", "--no-session", "--no-title", "--auto-approve",
            "--max-time", "600", "--model", "fixture/model",
        ])
        self.assertEqual(result["prompt_sha256"], tools.sha(prompt))


if __name__ == "__main__":
    unittest.main()
