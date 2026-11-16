"""District's host-claims capability probe is exact and repository-independent."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE = """
import runpy
import sys

attempts = []
def audit(event, args):
    if event.startswith("socket.") or event in {
        "subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.spawn"
    }:
        attempts.append(event)
        raise AssertionError("network/external commands forbidden: " + event)

sys.addaudithook(audit)
sys.argv = ["factory", "claims", "--supported"]
try:
    runpy.run_module("factory", run_name="__main__")
finally:
    assert not attempts, attempts
"""


class ClaimsCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = {**os.environ, "PYTHONPATH": str(ROOT),
                    "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(self.root),
                    "XDG_CONFIG_HOME": str(self.root / "config")}

    def invoke(self, *argv):
        return subprocess.run(
            [sys.executable, *argv], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_supported(self):
        result = self.invoke("-m", "factory", "claims", "--supported")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "host-claims 1\n")
        self.assertEqual(result.stderr, "")

    def test_invalid_arguments(self):
        for args in ((), ("--unknown",), ("--supported", "extra"),
                     ("--supported", "--unknown")):
            with self.subTest(args=args):
                result = self.invoke("-m", "factory", "claims", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_listed_in_help(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                result = self.invoke("-m", "factory", flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(result.stdout, r"(?m)^  claims\s+")

    def test_supported_without_repository_or_network(self):
        result = self.invoke("-c", OFFLINE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "host-claims 1\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
