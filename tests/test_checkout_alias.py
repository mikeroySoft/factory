"""The checkout-origin guard must hold when PYTHONPATH spells the checkout through an alias."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory import config
from tests.test_factory import installed_factory, make_repo, stub_bin

REASON = "factory comes from the repository checkout"
SERVICE_MODULES = ("cli", "drain", "triage", "dispatch", "dashboard")


class Scenario:
    """A host config selecting an installed Factory, run through the real install/doctor CLI."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.repo = make_repo(tmp)
        self.installer, _ = installed_factory(tmp / "installer")
        self.selected, self.package = installed_factory(tmp / "selected")
        self.xdg = tmp / "xdg"
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
        self.env = {
            k: v for k, v in os.environ.items()
            if k not in ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONHOME")
        }
        self.env["XDG_CONFIG_HOME"] = str(self.xdg)
        self.env["PATH"] = f"{tools}:{self.env['PATH']}"

    def configure(self, pythonpath: Path | None) -> None:
        host = self.xdg / "factory" / "config.toml"
        host.parent.mkdir(parents=True, exist_ok=True)
        text = (
            '[defaults.triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n'
            f'[repo."acme/widgets".install]\npython = {json.dumps(str(self.selected))}\n'
        )
        if pythonpath is not None:
            text += f'[repo."acme/widgets".install.env]\nPYTHONPATH = {json.dumps(str(pythonpath))}\n'
        host.write_text(text)

    def shadow(self, *, working_copy: bool = False) -> Path:
        """A checkout `factory/` whose `__init__.py` symlinks to the selected package's.

        With working_copy the checkout also carries every other package file, and each
        service module imports a checkout-only marker, so nothing but the guard stops it.
        """
        shadow = self.repo / "factory"
        shadow.mkdir()
        (shadow / "__init__.py").symlink_to(self.package / "__init__.py")
        if working_copy:
            (shadow / "checkout_only.py").write_text(
                "import os, pathlib\n"
                "if os.environ.get('CHECKOUT_MARKER'):\n"
                "    pathlib.Path(os.environ['CHECKOUT_MARKER']).write_text(__file__)\n"
            )
            for source in self.package.iterdir():
                if source.name != "__init__.py" and source.is_file():
                    shutil.copyfile(source, shadow / source.name)
            for module in SERVICE_MODULES:
                with (shadow / f"{module}.py").open("a") as handle:
                    handle.write("\nfrom . import checkout_only\n")
        return shadow

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.installer), "-P", "-m", "factory", *args],
            cwd=self.repo, env=self.env, capture_output=True, text=True, check=False,
        )

    def interpreter_row(self) -> dict:
        doctor = self.run("doctor", "--json")
        return next(
            row for row in json.loads(doctor.stdout)["rows"]
            if row["label"] == "service interpreter"
        )


class CheckoutProvenanceTest(unittest.TestCase):
    def each_temp_root(self, body) -> None:
        """Run body(Scenario) under default TMPDIR and under an explicit non-canonical root."""
        for name in ("default", "non-canonical"):
            with self.subTest(tmpdir=name), tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                if name == "non-canonical":
                    canonical = Path(os.path.realpath(d)) / "canonical"
                    canonical.mkdir()
                    tmp = Path(d) / "non-canonical"
                    tmp.symlink_to(canonical, target_is_directory=True)
                    self.assertNotEqual(os.path.realpath(tmp), str(tmp))
                body(Scenario(tmp))

    def assert_rejected_for_checkout(self, scenario: Scenario, origin: Path) -> None:
        installed = scenario.run("install", "--no-dashboard")
        self.assertEqual(installed.returncode, 1, installed.stdout + installed.stderr)
        self.assertIn(f"factory={origin}", installed.stderr)
        self.assertIn(REASON, installed.stderr)
        self.assertNotIn("service commands failed", installed.stderr)
        self.assertFalse((scenario.xdg / "systemd" / "user").exists())
        row = scenario.interpreter_row()
        self.assertEqual(row["status"], "FAIL")
        self.assertIn(REASON, row["detail"])

    def test_symlink_shadow_without_a_directory_alias_is_rejected(self) -> None:
        def body(scenario: Scenario) -> None:
            scenario.shadow()
            scenario.configure(scenario.repo)
            self.assert_rejected_for_checkout(scenario, scenario.repo / "factory" / "__init__.py")
        self.each_temp_root(body)

    def test_symlink_shadow_through_a_directory_alias_is_rejected(self) -> None:
        def body(scenario: Scenario) -> None:
            scenario.shadow()
            alias = scenario.tmp / "checkout-alias"
            alias.symlink_to(scenario.repo, target_is_directory=True)
            scenario.configure(alias)
            self.assert_rejected_for_checkout(scenario, alias / "factory" / "__init__.py")
        self.each_temp_root(body)

    def test_genuine_installed_package_is_accepted(self) -> None:
        def body(scenario: Scenario) -> None:
            scenario.configure(None)
            installed = scenario.run("install", "--no-dashboard")
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
            row = scenario.interpreter_row()
            self.assertEqual(row["status"], "PASS", row["detail"])
            self.assertIn(f"factory={scenario.package / '__init__.py'}", row["detail"])
        self.each_temp_root(body)

    def test_rejected_install_would_execute_checkout_code(self) -> None:
        def body(scenario: Scenario) -> None:
            scenario.shadow(working_copy=True)
            alias = scenario.tmp / "checkout-alias"
            alias.symlink_to(scenario.repo, target_is_directory=True)
            scenario.configure(alias)
            # The service's interpreter, cwd and PYTHONPATH load every service module
            # from the checkout and run checkout-only code: the install is truly unsafe.
            marker = scenario.tmp / "marker"
            service = subprocess.run(
                [str(scenario.selected), "-P", "-c",
                 "; ".join(f"import factory.{module}" for module in SERVICE_MODULES)],
                cwd=scenario.repo, capture_output=True, text=True, check=False,
                env={**scenario.env, "PYTHONPATH": str(alias), "CHECKOUT_MARKER": str(marker)},
            )
            self.assertEqual(service.returncode, 0, service.stderr)
            self.assertEqual(marker.read_text(), str(alias / "factory" / "checkout_only.py"))
            self.assertFalse((scenario.package / "checkout_only.py").exists())
            self.assert_rejected_for_checkout(scenario, alias / "factory" / "__init__.py")
        self.each_temp_root(body)


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
