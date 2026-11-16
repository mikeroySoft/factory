"""Venv interpreters must launch and all test builders must share their policy."""
import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests import venv_helpers


class VenvTest(unittest.TestCase):
    def test_venv_python_uses_its_own_purelib(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "venv"
            python = venv_helpers.create_venv(environment)
            env = {
                k: v for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONHOME")
            }
            result = subprocess.run(
                [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                Path(result.stdout.strip()).resolve().is_relative_to(environment.resolve()),
                result.stdout + result.stderr,
            )

    def test_only_shared_helper_builds_venvs(self) -> None:
        tests = Path(__file__).resolve().parent
        builders = []
        for path in sorted(tests.rglob("*.py")):
            if path == Path(venv_helpers.__file__).resolve():
                continue
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if (
                    isinstance(function, ast.Name) and function.id == "EnvBuilder"
                    or isinstance(function, ast.Attribute) and function.attr == "EnvBuilder"
                    or isinstance(function, ast.Attribute) and function.attr == "create"
                    and isinstance(function.value, ast.Name) and function.value.id == "venv"
                ):
                    builders.append(f"{path.relative_to(tests)}:{node.lineno}")
        self.assertEqual(builders, [], "Use tests.venv_helpers.create_venv: " + ", ".join(builders))
