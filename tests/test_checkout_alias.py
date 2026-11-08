"""The checkout-origin guard must hold when PYTHONPATH spells the checkout through an alias."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory import config
from tests.test_factory import installed_factory, make_repo, stub_bin


class CheckoutAliasTest(unittest.TestCase):
    def test_symlinked_checkout_file_is_rejected_through_a_directory_alias(self) -> None:
        # A canonical temp root keeps the only alias the one this test creates on purpose.
        with tempfile.TemporaryDirectory(dir=os.path.realpath(tempfile.gettempdir())) as d:
            tmp = Path(d)
            repo = make_repo(tmp)
            installer, _ = installed_factory(tmp / "installer")
            selected, selected_package = installed_factory(tmp / "selected")
            alias = tmp / "checkout-alias"
            alias.symlink_to(repo, target_is_directory=True)
            shadow = repo / "factory"
            shadow.mkdir()
            (shadow / "__init__.py").symlink_to(selected_package / "__init__.py")

            xdg = tmp / "xdg"
            host = xdg / "factory" / "config.toml"
            host.parent.mkdir(parents=True)
            host.write_text(
                '[defaults.triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n'
                f'[repo."acme/widgets".install]\npython = {json.dumps(str(selected))}\n'
                '[repo."acme/widgets".install.env]\n'
                f"PYTHONPATH = {json.dumps(str(alias))}\n"
            )
            manager_environment = tmp / "manager-environment"
            manager_environment.write_text("")
            gh = (
                'case "$1 $2" in "repo view") echo ADMIN;; '
                f'"label list") echo \'{json.dumps(list(config.LABELS))}\';; esac\nexit 0'
            )
            tools = stub_bin(
                tmp, gh=gh, loginctl="echo yes", ss="exit 0",
                systemctl=(
                    'case "$2" in\n'
                    f'show-environment) /bin/cat {shlex.quote(str(manager_environment))};;\n'
                    'is-active) echo inactive;;\nesac\nexit 0'
                ),
            )
            env = {
                k: v for k, v in os.environ.items()
                if k not in ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONHOME")
            }
            env["XDG_CONFIG_HOME"] = str(xdg)
            env["PATH"] = f"{tools}:{env['PATH']}"

            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    [str(installer), "-P", "-m", "factory", *args],
                    cwd=repo, env=env, capture_output=True, text=True, check=False,
                )

            installed = run("install", "--no-dashboard")
            self.assertEqual(installed.returncode, 1, installed.stdout + installed.stderr)
            self.assertIn(f"factory={alias / 'factory' / '__init__.py'}", installed.stderr)
            self.assertIn("factory comes from the repository checkout", installed.stderr)
            self.assertFalse((xdg / "systemd" / "user").exists())
            doctor = run("doctor", "--json")
            row = next(
                row for row in json.loads(doctor.stdout)["rows"]
                if row["label"] == "service interpreter"
            )
            self.assertEqual(row["status"], "FAIL")
            self.assertIn("factory comes from the repository checkout", row["detail"])


if __name__ == "__main__":
    unittest.main()
