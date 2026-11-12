"""Proof producers must be trusted; completion paths must never block the gate."""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import gate, mutation_scope
from tests.test_mutation_scope import build_repo

ROOT = str(Path(__file__).resolve().parents[1])
TRUSTED_RUNNER = '''from factory import mutation_scope
assert mutation_scope.PROVENANCE == "base"
print("trusted base runner")
raise SystemExit(1)
'''
FORGED_RUNNER = '''import json, os
from factory import mutation_scope
scope = mutation_scope.compute(os.environ["FACTORY_MUTATION_PROOF_BASE"])
counts = dict.fromkeys(("planned", "ran", "killed", "survived", "timeouts",
                       "blocking_survivors", "not_started", "cut_short", "broken"), 0)
counts.update(planned=1, ran=1, killed=1)
record = dict(protocol="factory-mutation-proof", version=1, mode="blocking",
              status="complete", reason="no-blocking-survivors", exit=0,
              files=sorted(scope.lines), scope=scope.receipt(), counts=counts)
with open(os.environ["FACTORY_MUTATION_PROOF"], "w") as handle:
    json.dump(record, handle)
'''


class GateIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "repo"

    def test_head_cannot_replace_proof_runner_or_its_scope_import(self) -> None:
        helper = Path(mutation_scope.__file__).read_text()
        for changed in ("scripts/mutation.py", "factory/mutation_scope.py", "factory/__init__.py"):
            with self.subTest(changed=changed):
                root = self.root / changed.replace("/", "-")
                base_files = {
                    "factory/example.py": "value = 0\n",
                    "factory/__init__.py": "",
                    "factory/mutation_scope.py": helper + '\nPROVENANCE = "base"\n',
                    "scripts/mutation.py": TRUSTED_RUNNER,
                }
                if changed == "scripts/mutation.py":
                    attack = FORGED_RUNNER
                else:
                    # Import-time code can forge a proof just as easily as the runner.
                    attack = (base_files[changed] + "\n" + FORGED_RUNNER
                              + "\nraise SystemExit(0)\n")
                base, _ = build_repo(root, base_files, {
                    "factory/example.py": "value = 1\n", changed: attack,
                })
                with contextlib.chdir(root), \
                        mock.patch.object(gate.lifecycle, "current", return_value=None), \
                        mock.patch.dict(os.environ, {"PYTHONPATH": str(root)}):
                    passed, output, status, _ = gate.run_mutation_proof(
                        [sys.executable, "scripts/mutation.py", "--blocking"], base,
                    )
                self.assertEqual((passed, status), (False, "FAIL"), output)
                self.assertIn("trusted base runner", output)

    def test_trusted_complete_producer_keeps_passing_for_plain_source_change(self) -> None:
        base, _ = build_repo(self.root, {
            "factory/example.py": "value = 0\n",
            "factory/__init__.py": "",
            "factory/mutation_scope.py": Path(mutation_scope.__file__).read_text(),
            "scripts/mutation.py": FORGED_RUNNER,
        }, {"factory/example.py": "value = 1\n"})
        with contextlib.chdir(self.root), \
                mock.patch.object(gate.lifecycle, "current", return_value=None):
            passed, output, status, binding = gate.run_mutation_proof(
                [sys.executable, "scripts/mutation.py", "--blocking"], base,
            )
        self.assertEqual((passed, status), (True, "PASS"), output)
        self.assertIn(f"base={base}", binding)

    def test_missing_base_producer_fails_closed_before_head_runner_executes(self) -> None:
        base, _ = build_repo(self.root, {"factory/example.py": "value = 0\n"}, {
            "factory/example.py": "value = 1\n",
            "scripts/mutation.py": "print('untrusted runner executed')\n",
        })
        with contextlib.chdir(self.root), \
                mock.patch.object(gate.lifecycle, "current", return_value=None):
            passed, output, status, _ = gate.run_mutation_proof(
                [sys.executable, "scripts/mutation.py"], base,
            )
        self.assertEqual((passed, status), (False, "FAIL"), output)
        self.assertIn("trusted mutation producer unavailable", output)
        self.assertNotIn("untrusted runner executed", output)

    def test_unrecognized_producer_spelling_fails_closed(self) -> None:
        absolute = str(Path(ROOT) / "scripts/mutation.py")
        for cmd in (
            [sys.executable, "./scripts/mutation.py", "--blocking"],
            [sys.executable, "-m", "scripts.mutation", "--blocking"],
            [sys.executable, "-mscripts.mutation", "--blocking"],
            [sys.executable, "-Bm", "scripts.mutation", "--blocking"],
            [sys.executable, "-c", 'import runpy; runpy.run_module("scripts.mutation")'],
            [sys.executable, absolute, "--blocking"],
            ["uv", "run", "python", "./scripts/mutation.py"],
            ["make", "mutation"],
        ):
            with self.subTest(cmd=cmd), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(mutation_scope.ScopeError):
                    gate._trusted_mutation_command(cmd, "HEAD", Path(directory))
                self.assertEqual(list(Path(directory).iterdir()), [])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                gate._trusted_mutation_command(["mutation"], "HEAD", Path(directory)),
                ["mutation"],
            )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_isolated_environment_paths_ignore_interpreter_packaging_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            site = environment / "lib" / "python3.12" / "site-packages"
            site.mkdir(parents=True)
            self.assertEqual(gate._isolated_environment_paths(environment), [str(site)])
            with self.assertRaises(mutation_scope.ScopeError):
                gate._isolated_environment_paths(Path(directory) / "empty")

    def test_isolated_environment_paths_prefer_environment_glob_over_probe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            site = environment / "lib" / "python3.12" / "site-packages"
            site.mkdir(parents=True)
            probe = environment / "bin" / "python"
            probe.parent.mkdir(parents=True)
            wrong = Path(directory) / "base-interpreter" / "lib" / "python3.12" / "site-packages"
            wrong.mkdir(parents=True)
            probe.write_text(
                "#!/bin/sh\nprintf '%s\n%s\n' "
                f"{str(wrong)!r} {str(wrong)!r}\n"
            )
            probe.chmod(0o755)
            self.assertEqual(gate._isolated_environment_paths(environment), [str(site)])
            self.assertNotIn(str(wrong), gate._isolated_environment_paths(environment))

    def test_provision_keeps_uv_lock_files_out_of_inherited_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as inherited, tempfile.TemporaryDirectory() as work:
            inherited_tmp = Path(inherited)
            directory = Path(work)
            environment = directory / "environment"
            uv = directory / "uv"
            uv.write_text("#!/bin/sh\n: > \"$TMPDIR/uv-probe.lock\"\n")
            uv.chmod(0o755)
            with mock.patch.dict(os.environ, {"TMPDIR": str(inherited_tmp)}):
                gate._provision_trusted_uv_environment(str(uv), [], directory, environment)
            self.assertEqual(list(inherited_tmp.iterdir()), [])
            scratch = environment.parent / "uv-tmp"
            self.assertTrue((scratch / "uv-probe.lock").is_file())


    def test_fifo_completion_fails_under_independent_watchdog(self) -> None:
        base, _ = build_repo(self.root, {
            "factory/example.py": "value = 0\n",
            "factory/__init__.py": "",
            "factory/mutation_scope.py": "",
            "scripts/mutation.py": "import os\nos.mkfifo(os.environ['FACTORY_MUTATION_PROOF'])\n",
        }, {"factory/example.py": "value = 1\n"})
        script = f'''import json, sys
sys.path.insert(0, {ROOT!r})
from factory import gate
result = gate.run_mutation_proof(
    [sys.executable, "scripts/mutation.py"],
    {base!r}, 61,
)
print(json.dumps(result))
'''
        try:
            process = subprocess.run([sys.executable, "-c", script], cwd=self.root,
                                     capture_output=True, text=True, timeout=5, check=True)
        except subprocess.TimeoutExpired:
            self.fail("gate hung opening a FIFO after the check exited")
        passed, output, status, _ = json.loads(process.stdout)
        self.assertEqual((passed, status), (False, "FAIL"), output)
        self.assertIn("malformed completion record", output)

    def test_regular_completion_transport_still_reads_and_rejects_directory(self) -> None:
        self.root.mkdir()
        regular = self.root / "regular"
        regular.write_bytes(b'{"proof": "bytes"}')
        self.assertEqual(gate._read_proof_file(regular), regular.read_bytes())
        # A directory must be invalid proof, not an uncaught IsADirectoryError.
        data = gate._read_proof_file(self.root)
        self.assertIsInstance(data, bytes)
        self.assertEqual(gate.read_mutation_proof(data, 0, mutation_scope.Scope(
            "b" * 40, "b" * 40, "h" * 40, (),
        ))[0], "FAIL")


    def test_provision_succeeds_when_scratch_dir_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as inherited, tempfile.TemporaryDirectory() as work:
            inherited_tmp = Path(inherited)
            directory = Path(work)
            environment = directory / "environment"
            scratch = directory / "uv-tmp"
            scratch.mkdir()
            leftover = scratch / "prior.lock"
            leftover.write_text("kept")
            uv = directory / "uv"
            uv.write_text("#!/bin/sh\n: > \"$TMPDIR/uv-probe.lock\"\n")
            uv.chmod(0o755)
            with mock.patch.dict(os.environ, {"TMPDIR": str(inherited_tmp)}):
                gate._provision_trusted_uv_environment(str(uv), [], directory, environment)
            self.assertEqual(list(inherited_tmp.iterdir()), [])
            self.assertTrue((scratch / "uv-probe.lock").is_file())
            self.assertTrue(leftover.is_file())

    def test_provision_failure_includes_output_for_any_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            environment = directory / "environment"
            uv = directory / "uv"
            uv.write_text("#!/bin/sh\necho 'uv could not resolve the lock'\nexit 1\n")
            uv.chmod(0o755)
            with self.assertRaises(mutation_scope.ScopeError) as ctx:
                gate._provision_trusted_uv_environment(str(uv), [], directory, environment)
            self.assertIn("uv could not resolve the lock", str(ctx.exception))

    def test_provision_failure_includes_signal_exits(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            directory = Path(work)
            environment = directory / "environment"
            uv = directory / "uv"
            uv.write_text("#!/bin/sh\nkill -9 $$\n")
            uv.chmod(0o755)
            with self.assertRaises(mutation_scope.ScopeError) as ctx:
                gate._provision_trusted_uv_environment(str(uv), [], directory, environment)
            self.assertIn("trusted mutation environment could not be provisioned", str(ctx.exception))

    def test_isolated_paths_treat_a_failing_probe_as_missing_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            probe = environment / "bin" / "python"
            probe.parent.mkdir(parents=True)
            existing = Path(directory) / "apparent-site"
            existing.mkdir()
            probe.write_text(f"#!/bin/sh\nprintf '%s\\n' {str(existing)!r}\nexit 1\n")
            probe.chmod(0o755)
            with self.assertRaises(mutation_scope.ScopeError):
                gate._isolated_environment_paths(environment)

    def test_isolated_paths_use_a_successful_probe_when_the_layout_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            site = Path(directory) / "probed-site"
            site.mkdir()
            probe = environment / "bin" / "python"
            probe.parent.mkdir(parents=True)
            probe.write_text(f"#!/bin/sh\nprintf '%s\\n' {str(site)!r}\n")
            probe.chmod(0o755)
            self.assertEqual(gate._isolated_environment_paths(environment), [str(site)])

    def test_isolated_paths_a_broken_probe_is_still_a_scope_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "environment"
            probe = environment / "bin" / "python"
            probe.parent.mkdir(parents=True)
            probe.mkdir()  # exists, but cannot be executed
            with self.assertRaises(mutation_scope.ScopeError):
                gate._isolated_environment_paths(environment)

    def _trusted_uv(self, cmd: list[str]):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provision = mock.Mock()
            with mock.patch.object(mutation_scope, "_git", return_value="# trusted\n"), \
                    mock.patch.object(gate, "_provision_trusted_uv_environment", provision), \
                    mock.patch.object(gate, "_isolated_environment_paths",
                                      return_value=[str(root / "site")]):
                (root / "site").mkdir()
                result = gate._trusted_mutation_command(cmd, "base", root)
            return result, provision

    def test_trusted_uv_command_requires_the_run_subcommand_and_uv_binary(self) -> None:
        python = (" " + "python").lstrip()
        run = (" " + "run").lstrip()
        with self.assertRaises(mutation_scope.ScopeError):
            self._trusted_uv(["uw", "run", python, "scripts/mutation.py"])
        with self.assertRaises(mutation_scope.ScopeError):
            self._trusted_uv(["uv", "sync", python, "scripts/mutation.py"])
        with self.assertRaises(mutation_scope.ScopeError):
            self._trusted_uv(["uv", "init", python, "scripts/mutation.py"])
        with self.assertRaises(mutation_scope.ScopeError) as ctx:
            self._trusted_uv(["uv", run, "scripts/mutation.py"])
        self.assertIn("no Python entry point", str(ctx.exception))
        with self.assertRaises(mutation_scope.ScopeError):
            self._trusted_uv(["python", run, python, "scripts/mutation.py"])

    def test_trusted_uv_command_accepts_a_noninterned_run_token(self) -> None:
        run = (" " + "run").lstrip()
        python = (" " + "python").lstrip()
        self.assertIsNot(run, "run")
        self.assertIsNot(python, "python")
        result, provision = self._trusted_uv(["uv", run, python, "scripts/mutation.py"])
        self.assertTrue(result)
        provision.assert_called_once()
        self.assertEqual(provision.call_args.args[1], [])

    def test_trusted_uv_command_rejects_a_non_python_entry_point(self) -> None:
        with self.assertRaises(mutation_scope.ScopeError) as ctx:
            self._trusted_uv(["uv", "run", "ruby", "scripts/mutation.py"])
        self.assertIn("no Python entry point", str(ctx.exception))

    def test_trusted_uv_command_keeps_flags_when_the_script_index_is_even(self) -> None:
        # An extra trusted flag makes scripts/mutation.py sit at an even index,
        # where index-1 and index^1 differ. Options passed to provision must
        # stay the uv flags, not include python or the producer path.
        result, provision = self._trusted_uv(
            ["uv", "run", "--frozen", "python", "scripts/mutation.py"],
        )
        self.assertTrue(result)
        provision.assert_called_once()
        self.assertEqual(provision.call_args.args[1], ["--frozen"])

    def test_trusted_command_accepts_this_interpreter_by_path_equality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(mutation_scope, "_git", return_value="# trusted\n"):
                result = gate._trusted_mutation_command(
                    [sys.executable, "scripts/mutation.py"], "base", root,
                )
        self.assertEqual(result[0], sys.executable)
        self.assertEqual(result[1], "-I")

    def test_trusted_command_rejects_this_interpreter_unless_it_is_the_entry_point(self) -> None:
        # index == 1 is the only accepted "[this interpreter, scripts/mutation.py]"
        # form. A later producer token with the same argv0 must still fail closed
        # (Eq->GtE on the index would accept it). A different file whose path
        # compares >= sys.executable must also fail closed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            other = root / "other-python"
            other.write_text("")
            other.chmod(0o755)
            self.assertGreaterEqual(other.resolve(), Path(sys.executable).resolve())
            with mock.patch.object(mutation_scope, "_git", return_value="# trusted\n"):
                with self.assertRaises(mutation_scope.ScopeError) as ctx:
                    gate._trusted_mutation_command(
                        [sys.executable, "run", "scripts/mutation.py"], "base", root,
                    )
                self.assertIn("unsupported trusted mutation command", str(ctx.exception))
                with self.assertRaises(mutation_scope.ScopeError) as ctx:
                    gate._trusted_mutation_command(
                        [str(other), "scripts/mutation.py"], "base", root,
                    )
                self.assertIn("unsupported trusted mutation command", str(ctx.exception))
