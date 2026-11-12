"""Real-producer regressions for the mutation gate's interpreter and child harness trust."""
from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gate
from tests.test_mutation_scope import build_repo

ROOT = Path(__file__).resolve().parents[1]
UV_COMMAND = [
    "uv", "run", "--frozen", "--no-managed-python", "--group", "mutation",
    "python", "scripts/mutation.py", "--blocking", "--equivalents", "mutation-equivalents.json",
]
WEAK_TEST = "import unittest\nclass ExampleTest(unittest.TestCase):\n    def test_example(self): self.assertTrue(True)\n"
STRONG_TEST = (
    "import unittest\nfrom factory.example import value\n"
    "class ExampleTest(unittest.TestCase):\n"
    "    def test_example(self): self.assertEqual(value, 1)\n"
)


class MutationRunnerTrustRevisionTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "repo"

    def repository(self, test_source: str, runner: str | None = None) -> str:
        files = {
            name: (ROOT / name).read_text()
            for name in (
                "pyproject.toml", "uv.lock", "README.md", "docs/ticket-flow.md",
                "factory/__init__.py", "factory/mutation_scope.py", "scripts/mutation.py",
            )
        }
        files.update({
            "factory/example.py": "value = 0\n",
            "tests/__init__.py": "",
            "tests/test_example.py": test_source,
            "AGENTS.md": "`factory/example.py` `tests/test_example.py`\n",
            "mutation-equivalents.json": "[]\n",
        })
        head = {"factory/example.py": "value = 1\n"}
        if runner is not None:
            head["scripts/mutation.py"] = runner
        base, _ = build_repo(self.root, files, head)
        return base

    @contextlib.contextmanager
    def uv_environment(self):
        with mock.patch.dict(os.environ, {
            "UV_PROJECT_ENVIRONMENT": str(self.root / ".venv"),
            "UV_OFFLINE": "1",
        }):
            for name in ("UV_NO_SYNC", "VIRTUAL_ENV", "UV_CONFIG_FILE", "UV_PROJECT",
                         "UV_WORKING_DIR", "UV_PYTHON"):
                os.environ.pop(name, None)
            yield

    def run_proof(self, base: str) -> tuple[bool, str, str, str]:
        with contextlib.chdir(self.root), self.uv_environment(), \
                mock.patch.object(gate.lifecycle, "current", return_value=None):
            return gate.run_mutation_proof(UV_COMMAND, base)

    def test_untracked_worktree_venv_startup_cannot_forge_pass(self) -> None:
        base = self.repository(WEAK_TEST)
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"UV_NO_SYNC", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}
        environment.update(UV_PROJECT_ENVIRONMENT=str(self.root / ".venv"), UV_OFFLINE="1")
        subprocess.run(
            ["uv", "sync", "--project", str(self.root), "--frozen", "--no-managed-python",
             "--group", "mutation"],
            cwd=self.root, env=environment, check=True, capture_output=True, text=True,
        )
        site_packages = next((self.root / ".venv" / "lib").glob("python*/site-packages"))
        marker = self.root.parent / "startup-ran"
        attack = f'''import json, os, sys
from pathlib import Path
sys.path.insert(0, {str(self.root)!r})
from factory import mutation_scope
scope = mutation_scope.compute(os.environ["FACTORY_MUTATION_PROOF_BASE"])
counts = dict.fromkeys({gate.MUTATION_PROOF_COUNTS!r}, 0)
counts.update(planned=1, ran=1, killed=1)
record = dict(protocol="factory-mutation-proof", version=1, mode="blocking", status="complete",
              reason="no-blocking-survivors", exit=0, files=sorted(scope.lines),
              scope=scope.receipt(), counts=counts)
Path({str(marker)!r}).write_text("startup executed")
Path(os.environ["FACTORY_MUTATION_PROOF"]).write_text(json.dumps(record))
os._exit(0)
'''
        (site_packages / "startup_attack.py").write_text(attack)
        (site_packages / "startup_attack.pth").write_text("import startup_attack\n")

        passed, output, status, _ = self.run_proof(base)

        self.assertEqual((passed, status), (False, "FAIL"), output)
        self.assertFalse(marker.exists(), "the reviewed worktree's .pth ran before trusted code")
        self.assertIn("blocking survivors", output)

    def test_head_child_harness_cannot_forge_kills(self) -> None:
        marker = self.root.parent / "head-harness-ran"
        runner = f'''import sys
from pathlib import Path
Path({str(marker)!r}).write_text("head harness executed")
module = sys.argv[sys.argv.index("--test-module") + 1]
raise SystemExit(1 if module else 0)
'''
        base = self.repository(WEAK_TEST, runner)

        passed, output, status, _ = self.run_proof(base)

        self.assertEqual((passed, status), (False, "FAIL"), output)
        self.assertFalse(marker.exists(), "the reviewed head's scripts.mutation ran as the child harness")
        self.assertIn("blocking survivors", output)

    def test_legitimate_real_producer_still_passes(self) -> None:
        base = self.repository(STRONG_TEST)

        passed, output, status, binding = self.run_proof(base)

        self.assertEqual((passed, status), (True, "PASS"), output)
        self.assertIn(f"base={base}", binding)
        self.assertIn("mutation proof: PASS (complete:", output)

    def test_legitimate_real_producer_child_imports_provisioned_only_module(self) -> None:
        original = gate._provision_trusted_uv_environment

        def provision(
            uv: str, options: list[str], project: Path, environment: Path,
        ) -> None:
            original(uv, options, project, environment)
            site = next(environment.glob("lib/python*/site-packages"))
            (site / "provisioned_only_marker.py").write_text("PRESENT = True\n")

        source = (
            "import unittest\nimport provisioned_only_marker\n"
            "from factory.example import value\n"
            "class ExampleTest(unittest.TestCase):\n"
            "    def test_example(self):\n"
            "        self.assertTrue(provisioned_only_marker.PRESENT)\n"
            "        self.assertEqual(value, 1)\n"
        )
        base = self.repository(source)
        with mock.patch.object(gate, "_provision_trusted_uv_environment", provision):
            passed, output, status, binding = self.run_proof(base)
        self.assertEqual((passed, status), (True, "PASS"), output)
        self.assertIn(f"base={base}", binding)


if __name__ == "__main__":
    unittest.main()
