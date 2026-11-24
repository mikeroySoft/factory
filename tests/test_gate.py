"""#228: `[gate].add_only_paths` lets a worker add tests without touching existing ones.

Run: python -m unittest discover -s tests
"""
from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from factory import platform_identity

ROOT = Path(__file__).resolve().parents[1]

TOML = '[gate]\nprotected_paths = [".factory.toml", "checks/**"]\nadd_only_paths = ["tests/**"]\n'
ADD_ONLY = "- protected-paths: FAIL (add-only path removed or changed existing lines)"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def gate(cwd: Path, *args: str) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("FACTORY_LIFECYCLE_CONTEXT", None)  # #253: never journal into a worker's live ledger
    proc = subprocess.run([sys.executable, "-m", "factory", "gate", *args],
                          cwd=cwd, capture_output=True, text=True, env=env, check=False)
    report = cwd / ".factory" / "gate-report.md"
    return proc.returncode, proc.stdout + proc.stderr, report.read_text() if report.exists() else ""


def repo_with_test(tmp: Path, toml: str = TOML) -> Path:
    """A base ref (`origin/main`) with `toml` and one committed test file of two lines."""
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / ".factory.toml").write_text(toml)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def commit(repo: Path, message: str = "worker") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def stage_name(repo: Path, name: bytes, content: bytes) -> None:
    """Put ``name`` in git's index without asking the filesystem to create it."""
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=content, capture_output=True, check=True,
    ).stdout.strip()
    subprocess.run(
        [b"git", b"-C", os.fsencode(repo), b"update-index", b"--add", b"--cacheinfo",
         b"100644," + blob + b"," + name],
        check=True,
    )


class AddOnlyPathsTest(unittest.TestCase):
    def test_new_test_file_passes_without_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            (repo / "tests" / "test_new.py").write_text("def test_b():\n    assert True\n")
            commit(repo)
            code, out, report = gate(repo)
            self.assertEqual(code, 0, report + out)
            self.assertNotIn("- protected-paths: FAIL", report)

    def test_appending_to_existing_test_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            with (repo / "tests" / "test_old.py").open("a") as f:
                f.write("\n\ndef test_b():\n    assert True\n")
            commit(repo)
            code, out, report = gate(repo)
            self.assertEqual(code, 0, report + out)
            self.assertNotIn("- protected-paths: FAIL", report)

    def test_editing_deleting_renaming_or_chmod_fails_with_add_only_reason(self) -> None:
        cases = {
            "edit": lambda p: p.write_text("def test_a():\n    assert False\n"),
            "delete line": lambda p: p.write_text("def test_a():\n"),
            "delete file": lambda p: p.unlink(),
            "rename": lambda p: p.rename(p.with_name("test_renamed.py")),
            "mode": lambda p: p.chmod(p.stat().st_mode | 0o111),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as d:
                repo = repo_with_test(Path(d))
                mutate(repo / "tests" / "test_old.py")
                commit(repo)
                code, _, report = gate(repo)
                self.assertEqual(code, 1, report)
                self.assertIn(ADD_ONLY, report)
                self.assertIn("tests/test_old.py", report)
                if name in ("edit", "delete line"):
                    self.assertIn("-    assert True", report)
                # The human escape hatch still applies.
                code, _, report = gate(repo, "--protected-override")
                self.assertEqual(code, 0, report)

    def test_fully_protected_paths_still_fail_on_any_change(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            (repo / "checks").mkdir()
            (repo / "checks" / "new.txt").write_text("added\n")
            commit(repo)
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            self.assertNotIn("add-only", report)

    def test_worker_cannot_widen_add_only_allowance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), '[gate]\nprotected_paths = [".factory.toml", "checks/**"]\n')
            (repo / ".factory.toml").write_text(
                '[gate]\nprotected_paths = [".factory.toml"]\nadd_only_paths = ["checks/**", "tests/**"]\n')
            (repo / "checks").mkdir()
            (repo / "checks" / "new.txt").write_text("added\n")
            commit(repo)
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            self.assertIn("+++ b/.factory.toml", report)
            # A pure addition under the worker's own add-only glob is still a full-protection hit.
            self.assertIn("+++ b/checks/new.txt", report)

    def test_upstream_refs_exempt_upstream_content_but_not_worker_edits(self) -> None:
        """#151: a sync worker gets no bypass. Content equal to an `--upstream` ref is
        upstream's; anything else under a protected path is the worker's and still fails."""
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), TOML + '[leak_scan]\nextra = ["codename"]\n')
            # Upstream rewrote an existing test (not additive) and ships a word the leak scan flags.
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert 1\n")
            (repo / "NOTES.md").write_text("codename upstream notes\n")
            commit(repo, "upstream")
            tip = git(repo, "rev-parse", "HEAD")
            code, _, report = gate(repo)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            code, out, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 0, report + out)
            self.assertIn("- leak-scan: PASS", report)
            # The worker edits the same test and adds its own flagged word: both are caught.
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert 2\n")
            commit(repo)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            self.assertIn("+    assert 2", report)
            # An unknown ref fails closed instead of silently exempting nothing.
            code, _, report = gate(repo, "--upstream", "0" * 40)
            self.assertEqual(code, 1, report)
            self.assertIn("- protected-paths: FAIL\n", report)
            git(repo, "reset", "-q", "--hard", tip)
            (repo / "worker.md").write_text("codename\n")
            commit(repo)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn("worker.md:1: [codename]", report)
            self.assertNotIn("NOTES.md", report)

    def test_upstream_tip_does_not_exempt_fork_only_files(self) -> None:
        """#151 round 2: a path upstream never changed is the fork's. Matching the tip
        (both absent, or reset to upstream's version) does not make a worker edit upstream's."""
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d))
            root = git(repo, "rev-parse", "HEAD")
            (repo / "tests" / "test_fork.py").write_text("def test_fork():\n    assert True\n")
            (repo / "tests" / "test_old.py").write_text("def test_a():\n    assert True\n# fork line\n")
            commit(repo, "fork")
            git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(repo, "checkout", "-q", root)
            (repo / "NOTES.md").write_text("upstream\n")
            commit(repo, "upstream")
            tip = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "main")
            git(repo, "merge", "-q", "--no-ff", "-m", "sync", tip)
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 0, report)
            git(repo, "rm", "-q", "tests/test_fork.py")
            git(repo, "checkout", "-q", tip, "--", "tests/test_old.py")
            git(repo, "commit", "-q", "-m", "worker drops fork tests")
            code, _, report = gate(repo, "--upstream", tip)
            self.assertEqual(code, 1, report)
            self.assertIn(ADD_ONLY, report)
            self.assertIn("--- a/tests/test_fork.py", report)
            self.assertIn("-# fork line", report)


class GateOutputEncodingTest(unittest.TestCase):
    def test_configured_check_replaces_non_utf8_output(self) -> None:
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "bytes"\n'
            f'run = ["{sys.executable}", "-c", '
            '"import os; os.write(1, bytes([255])); print(\'done\')"]\n'
            "exclusive = false\n"
        )
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), toml)
            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")
            self.assertEqual(code, 0, report + out)
            self.assertIn("- bytes: PASS", report)

    def test_failed_check_replaces_non_utf8_output(self) -> None:
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "bytes"\n'
            f'run = ["{sys.executable}", "-c", '
            '"import os; os.write(2, bytes([255, 254]) + b\' failed\\\\n\'); raise SystemExit(7)"]\n'
            "exclusive = false\n"
        )
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), toml)
            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")
            self.assertEqual(code, 1, report + out)
            self.assertIn("- bytes: FAIL", report)
            self.assertIn("\ufffd\ufffd failed", report)

    def test_timeout_replaces_non_utf8_output(self) -> None:
        from unittest import mock

        from factory import gate as gate_module

        command = [
            sys.executable,
            "-c",
            "import os, time; os.write(1, bytes([255])); time.sleep(30)",
        ]
        with mock.patch.object(gate_module, "CHECK_TIMEOUT", 2):
            result = gate_module.timed(command)
        self.assertEqual(result.returncode, 124)
        self.assertIn("\ufffd", result.stdout)
        self.assertIn("check timed out", result.stdout)

    def test_leak_scan_finds_pattern_beside_non_utf8_filename(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), TOML + '\n[leak_scan]\nextra = ["codename"]\n')
            git(repo, "config", "core.quotePath", "false")
            name = os.path.join(os.fsencode(repo), b"\xff-leak.txt")
            if platform_identity.darwin():
                # Darwin filesystems positively refuse a non-UTF-8 name; git still carries it.
                with self.assertRaises(OSError) as refused:
                    os.open(name, os.O_WRONLY | os.O_CREAT, 0o600)
                self.assertEqual(refused.exception.errno, errno.EILSEQ)
                self.assertFalse(os.path.lexists(name))
                stage_name(repo, b"\xff-leak.txt", b"codename\n")
                git(repo, "commit", "-q", "-m", "worker")
            else:
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT, 0o600)
                os.write(descriptor, b"codename\n")
                os.close(descriptor)
                commit(repo)

            code, out, report = gate(repo, "--skip", "conflict-markers")

            self.assertEqual(code, 1, report + out)
            self.assertIn("- leak-scan: FAIL", report)
            self.assertIn("\ufffd-leak.txt:1: [codename] codename", report)

    def test_leak_scan_finds_pattern_in_non_utf8_path_from_git_objects(self) -> None:
        """Portable: the invalid byte reaches the diff from git objects, not the filesystem."""
        with tempfile.TemporaryDirectory() as d:
            repo = repo_with_test(Path(d), TOML + '\n[leak_scan]\nextra = ["codename"]\n')
            git(repo, "config", "core.quotePath", "false")
            stage_name(repo, b"\xff-leak.txt", b"codename\n")
            git(repo, "commit", "-q", "-m", "worker")
            self.assertNotIn(b"\xff-leak.txt", os.listdir(os.fsencode(repo)))

            code, out, report = gate(repo, "--skip", "conflict-markers")

            self.assertEqual(code, 1, report + out)
            self.assertIn("- leak-scan: FAIL", report)
            self.assertIn("\ufffd-leak.txt:1: [codename] codename", report)



class FailedTestEvidenceTest(unittest.TestCase):
    def test_failed_unittest_and_pytest_ids_are_journaled(self) -> None:
        import json

        output = (
            "print('FAIL: test_port "
            "(tests.test_status.StatusTest.test_port)'); "
            "print('FAILED tests/test_cli.py::CliTest::test_help - assertion'); "
            "raise SystemExit(1)"
        )
        toml = TOML + (
            "\n[[gate.check]]\n"
            'name = "tests"\n'
            f"run = [{json.dumps(sys.executable)}, \"-c\", {json.dumps(output)}]\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)

            code, out, report = gate(repo, "--skip", "conflict-markers,leak-scan")

            self.assertEqual(code, 1, report + out)
            rows = [
                json.loads(line)
                for line in (repo / ".factory" / "events.jsonl").read_text().splitlines()
            ]
            [result] = [
                row for row in rows
                if row.get("stage") == "gate-check"
                and row.get("kind") == "result"
                and row.get("check") == "tests"
            ]
            self.assertEqual(result["failed_tests"], [
                "tests.test_status.StatusTest.test_port",
                "tests/test_cli.py::CliTest::test_help",
            ])

    def test_parses_unittest_subtest_failure_id(self) -> None:
        from factory import gate as factory_gate

        output = """FAIL: test_x (mod.C.test_x) (i=1)
ERROR: test_y (mod.C.test_y) (name='a.b')
FAIL: test_z (mod.C.test_z) (a=1, b=2)
"""
        self.assertEqual(factory_gate.failed_test_ids(output), [
            "mod.C.test_x",
            "mod.C.test_y",
            "mod.C.test_z",
        ])

    def test_parses_cargo_libtest_failure_id(self) -> None:
        from factory import gate as factory_gate

        output = """running 1 test
---- comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone stdout ----
thread 'comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone' panicked

failures:
    comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone

test result: FAILED. 0 passed; 1 failed
"""
        self.assertEqual(factory_gate.failed_test_ids(output), [
            "comfyui::tests::status_reports_stopped_when_saved_comfyui_pid_is_gone",
        ])


class GateTempdirTest(unittest.TestCase):
    def test_configured_check_tmpdir_is_removed_after_exit_and_timeout(self) -> None:
        import json

        cases = (("pass", 0, 0, 0), ("fail", 7, 0, 1), ("timeout", 0, 5, 1))
        for name, exit_code, delay, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "tmpdir"
                script = (
                    "import os, time; from pathlib import Path; "
                    "tmp = Path(os.environ['TMPDIR']); "
                    f"Path({str(marker)!r}).write_text(str(tmp)); "
                    "(tmp / 'leak').mkdir(); (tmp / 'leak' / 'file').write_text('x'); "
                    f"time.sleep({delay}); raise SystemExit({exit_code})"
                )
                toml = TOML + (
                    "\n[[gate.check]]\n"
                    'name = "tempdir"\n'
                    f"run = [{json.dumps(sys.executable)}, \"-c\", {json.dumps(script)}]\n"
                )
                repo = repo_with_test(root, toml)

                code, out, report = gate(
                    repo, "--check-timeout", "1", "--skip", "conflict-markers,leak-scan",
                )

                self.assertEqual(code, expected, report + out)
                self.assertFalse(Path(marker.read_text()).exists())

class PassingAttemptEvidenceTest(unittest.TestCase):
    def test_report_records_skips_without_parsing_verbose_diagnostics(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_records.py").write_text(
                "import sys, unittest\n"
                "class Records(unittest.TestCase):\n"
                "    def test_pass(self):\n"
                "        print(\"skipped 'x'\", file=sys.stderr)\n"
                "    def test_skip(self):\n"
                "        print(\"skipped 'x'\", file=sys.stderr)\n"
                "        self.skipTest('needs hardware')\n"
            )
            commit(repo, "diagnostic skip records")
            code, out, report = gate(repo)
            self.assertEqual(code, 0, out + report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 2 tests", report)
            self.assertNotIn("test_records.Records.test_pass", report)
            self.assertEqual(report.count("test_records.Records.test_skip"), 1)
            self.assertIn("skipped 'needs hardware'", report)
            self.assertNotIn("skipped 'x'", report)

    def test_report_records_head_count_and_skipped_test_names(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_evidence.py").write_text(
                "import sys\nimport unittest\n\n"
                "class EvidenceTest(unittest.TestCase):\n"
                "    @unittest.skip('needs hardware')\n"
                "    def test_a_hardware(self):\n"
                "        pass\n"
                "    @unittest.skip('needs network')\n"
                "    def test_b_network(self):\n"
                "        \"\"\"Network-only acceptance.\"\"\"\n"
                "        pass\n"
                "    def test_z_pass(self):\n"
                "        print('noise\\n' * 100, file=sys.stderr)\n"
                "        self.assertEqual(2 + 2, 4)\n"
            )
            commit(repo, "test suite")
            head = git(repo, "rev-parse", "HEAD")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            for evidence in (
                f"Head SHA: {head}",
                "Ran 3 tests",
                "test_evidence.EvidenceTest.test_a_hardware",
                "test_evidence.EvidenceTest.test_b_network",
            ):
                with self.subTest(evidence=evidence):
                    self.assertIn(evidence, report)

    def test_report_retains_skips_across_diagnostics_and_explicit_tiers(self) -> None:
        import json

        for tier in ("attempt", "pr", "release"):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as directory:
                toml = TOML + (
                    '\n[[gate.check]]\nname = "test"\n'
                    f'when = "{tier}"\n'
                    f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
                )
                repo = repo_with_test(Path(directory), toml)
                (repo / "tests" / "test_noisy.py").write_text(
                    "import sys\nimport unittest\n\n"
                    "class NoisyTest(unittest.TestCase):\n"
                    "    def test_a_pass(self):\n"
                    "        print('passing diagnostic', file=sys.stderr)\n"
                    "    def test_b_hardware(self):\n"
                    "        print('hardware unavailable\\nmore diagnostics', file=sys.stderr)\n"
                    "        self.skipTest('needs hardware')\n"
                    "    def test_c_network(self):\n"
                    "        \"\"\"Network-only acceptance.\"\"\"\n"
                    "        print('network unavailable\\nmore diagnostics', file=sys.stderr)\n"
                    "        self.skipTest('needs network')\n"
                )
                commit(repo, "noisy skipped suite")

                code, out, report = gate(repo, "--tier", tier)

                self.assertEqual(code, 0, out + report)
                self.assertIn("- test: PASS", report)
                if tier == "attempt":
                    self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
                    self.assertIn("Ran 3 tests", report)
                    self.assertIn("test_noisy.NoisyTest.test_b_hardware", report)
                    self.assertIn("test_noisy.NoisyTest.test_c_network", report)
                    self.assertNotIn("test_noisy.NoisyTest.test_a_pass", report)
                else:
                    self.assertNotIn("Head SHA:", report)
                    self.assertNotIn("test evidence", report)
                    self.assertNotIn("test_noisy.NoisyTest", report)

    def test_report_keeps_exact_skip_identities_with_parenthesized_noise(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_parentheses.py").write_text(
                "import sys\nimport unittest\n\n"
                "class ParenthesesTest(unittest.TestCase):\n"
                "    @unittest.skip('needs hardware')\n"
                "    def test_a_decorated(self):\n"
                "        \"\"\"Hardware (optional)\"\"\"\n"
                "        pass\n"
                "    def test_b_runtime(self):\n"
                "        \"\"\"Retries (once) on failure.\"\"\"\n"
                "        print('diagnostic\\n* (HEAD detached at abc)', file=sys.stderr)\n"
                "        self.skipTest('needs network')\n"
                "    def test_c_pass(self):\n"
                "        print('passing diagnostic', file=sys.stderr)\n"
            )
            commit(repo, "parenthesized skipped suite")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 3 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_a_decorated (test_parentheses.ParenthesesTest.test_a_decorated) ... skipped 'needs hardware'",
                    "test_b_runtime (test_parentheses.ParenthesesTest.test_b_runtime) ... skipped 'needs network'",
                ],
            )

    def test_report_keeps_skip_identities_after_partial_fixture_diagnostics(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_fixtures.py").write_text(
                "import sys\nimport unittest\n\n"
                "def setUpModule():\n"
                "    print('module setup: ', end='', file=sys.stderr)\n"
                "def tearDownModule():\n"
                "    print(\"module diagnostic skipped 'not a test'\", file=sys.stderr)\n"
                "class AFixtureTest(unittest.TestCase):\n"
                "    @classmethod\n"
                "    def tearDownClass(cls):\n"
                "        print(\"class diagnostic skipped 'not a test'\", file=sys.stderr)\n"
                "    @unittest.skip('needs hardware')\n"
                "    def test_a_decorated(self):\n"
                "        pass\n"
                "    def test_b_pass(self):\n"
                "        pass\n"
                "class ZFixtureTest(unittest.TestCase):\n"
                "    @classmethod\n"
                "    def setUpClass(cls):\n"
                "        print('class setup: ', end='', file=sys.stderr)\n"
                "    def test_c_runtime(self):\n"
                "        \"\"\"Network (optional)\"\"\"\n"
                "        print('runtime diagnostic', file=sys.stderr)\n"
                "        self.skipTest('needs network')\n"
            )
            commit(repo, "partial fixture diagnostics")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 3 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_a_decorated (test_fixtures.AFixtureTest.test_a_decorated) ... skipped 'needs hardware'",
                    "test_c_runtime (test_fixtures.ZFixtureTest.test_c_runtime) ... skipped 'needs network'",
                ],
            )

    def test_report_keeps_distinct_parameterized_subtest_skips(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_subtests.py").write_text(
                "import unittest\n\n"
                "class SubtestTest(unittest.TestCase):\n"
                "    def test_parameters(self):\n"
                "        for i in range(2):\n"
                "            with self.subTest(i=i):\n"
                "                self.skipTest('needs hardware')\n"
            )
            commit(repo, "parameterized skipped subtests")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 1 test", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_parameters (test_subtests.SubtestTest.test_parameters) (i=0) ... skipped 'needs hardware'",
                    "test_parameters (test_subtests.SubtestTest.test_parameters) (i=1) ... skipped 'needs hardware'",
                ],
            )

    def test_report_keeps_labelled_subtests_and_module_fixture_skips(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_module_skip.py").write_text(
                "import sys\nimport unittest\n\n"
                "def setUpModule():\n"
                "    print('module setup: ', end='', file=sys.stderr)\n"
                "    raise unittest.SkipTest('needs module hardware')\n"
                "class ModuleTest(unittest.TestCase):\n"
                "    def test_unused(self):\n"
                "        pass\n"
            )
            (repo / "tests" / "test_labels.py").write_text(
                "import unittest\n\n"
                "class LabelTest(unittest.TestCase):\n"
                "    def test_subtests(self):\n"
                "        for label in ('first', 'second'):\n"
                "            with self.subTest(label):\n"
                "                self.skipTest('needs labelled hardware')\n"
                "        for i in range(2):\n"
                "            with self.subTest('parameterized', i=i):\n"
                "                self.skipTest('needs parameterized hardware')\n"
            )
            commit(repo, "labelled subtests and module skip")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 1 test", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_subtests (test_labels.LabelTest.test_subtests) [first] ... skipped 'needs labelled hardware'",
                    "test_subtests (test_labels.LabelTest.test_subtests) [second] ... skipped 'needs labelled hardware'",
                    "test_subtests (test_labels.LabelTest.test_subtests) [parameterized] (i=0) ... skipped 'needs parameterized hardware'",
                    "test_subtests (test_labels.LabelTest.test_subtests) [parameterized] (i=1) ... skipped 'needs parameterized hardware'",
                    "setUpModule (test_module_skip) ... skipped 'needs module hardware'",
                ],
            )

    def test_report_keeps_skip_after_diagnostic_status_text(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_status.py").write_text(
                "import sys\nimport unittest\n\n"
                "class StatusTest(unittest.TestCase):\n"
                "    def test_a_hardware(self):\n"
                "        print('hardware probe ... ERROR', file=sys.stderr)\n"
                "        self.skipTest('needs hardware')\n"
                "    def test_b_network(self):\n"
                "        \"\"\"Network (optional)\"\"\"\n"
                "        print('network probe ... ERROR', file=sys.stderr)\n"
                "        self.skipTest('needs network')\n"
                "    def test_c_pass(self):\n"
                "        \"\"\"Completed test\"\"\"\n"
                "        pass\n"
                "    @classmethod\n"
                "    def tearDownClass(cls):\n"
                "        print(\"class diagnostic skipped 'not a test'\", file=sys.stderr)\n"
            )
            commit(repo, "diagnostic status before skips")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 3 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_a_hardware (test_status.StatusTest.test_a_hardware) ... skipped 'needs hardware'",
                    "test_b_network (test_status.StatusTest.test_b_network) ... skipped 'needs network'",
                ],
            )

    def test_report_matches_skips_only_at_completion_position(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            # A long header exercises description matching beyond cached small integers.
            long_name = "test_f_" + "hardware_" * 35
            (repo / "tests" / "test_completion.py").write_text(
                "import sys\nimport unittest\n\n"
                "class CompletionTest(unittest.TestCase):\n"
                "    def test_a_pass(self):\n"
                "        print(\"hardware probe skipped 'not a test'\", file=sys.stderr)\n"
                "    def test_b_skip(self):\n"
                "        print(\"hardware probe skipped 'not a test'\", file=sys.stderr)\n"
                "        self.skipTest('needs hardware')\n"
                "    def test_c_pass_description(self):\n"
                "        \"\"\"Passing hardware probe\"\"\"\n"
                "        print(\"hardware probe skipped 'not a test'\", file=sys.stderr)\n"
                "    def test_d_skip_description(self):\n"
                "        \"\"\"Optional hardware probe\"\"\"\n"
                "        print(\"hardware probe skipped 'not a test'\", file=sys.stderr)\n"
                "        self.skipTest('needs described hardware')\n"
                "    def test_e_trailing_diagnostic(self):\n"
                "        print('trailing diagnostic\\nx ... ok', file=sys.stderr)\n"
                "        self.skipTest('needs trailing hardware')\n"
                f"    def {long_name}(self):\n"
                "        \"\"\"Long header description\"\"\"\n"
                "        self.skipTest('needs long-named hardware')\n"
            )
            commit(repo, "diagnostic skip suffixes and completion boundaries")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 6 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_b_skip (test_completion.CompletionTest.test_b_skip) ... skipped 'needs hardware'",
                    "test_d_skip_description (test_completion.CompletionTest.test_d_skip_description) ... skipped 'needs described hardware'",
                    "test_e_trailing_diagnostic (test_completion.CompletionTest.test_e_trailing_diagnostic) ... skipped 'needs trailing hardware'",
                    f"{long_name} (test_completion.CompletionTest.{long_name}) ... skipped 'needs long-named hardware'",
                ],
            )

    def test_report_clears_skipped_identity_before_teardown_diagnostic(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_clear_skip.py").write_text(
                "import sys\nimport unittest\n\n"
                "class ClearSkipTest(unittest.TestCase):\n"
                "    def test_hardware(self):\n"
                "        self.skipTest('needs hardware')\n"
                "    @classmethod\n"
                "    def tearDownClass(cls):\n"
                "        print(\"skipped 'teardown diagnostic'\", file=sys.stderr)\n"
            )
            commit(repo, "skip-shaped teardown diagnostic")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 1 test", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_hardware (test_clear_skip.ClearSkipTest.test_hardware) ... skipped 'needs hardware'",
                ],
            )

    def test_report_keeps_identities_through_dotted_description_and_diagnostics(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_dotted.py").write_text(
                "import sys\nimport unittest\n\n"
                "class DottedTest(unittest.TestCase):\n"
                "    @unittest.skip('needs decorated GPU')\n"
                "    def test_a_decorated(self):\n"
                "        \"\"\"Uses backend (torch.cuda)\"\"\"\n"
                "        pass\n"
                "    def test_b_runtime(self):\n"
                "        \"\"\"Uses backend (torch.cuda)\"\"\"\n"
                "        self.skipTest('needs runtime GPU')\n"
                "    def test_c_diagnostic(self):\n"
                "        print('probing\\nusing backend (torch.cuda)', file=sys.stderr)\n"
                "        self.skipTest('needs diagnostic GPU')\n"
                "    def test_d_diagnostic_description(self):\n"
                "        \"\"\"Uses backend (torch.cuda)\"\"\"\n"
                "        print('probing\\nusing backend (torch.cuda)', file=sys.stderr)\n"
                "        self.skipTest('needs described diagnostic GPU')\n"
            )
            commit(repo, "dotted descriptions and diagnostics")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 4 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_a_decorated (test_dotted.DottedTest.test_a_decorated) ... skipped 'needs decorated GPU'",
                    "test_b_runtime (test_dotted.DottedTest.test_b_runtime) ... skipped 'needs runtime GPU'",
                    "test_c_diagnostic (test_dotted.DottedTest.test_c_diagnostic) ... skipped 'needs diagnostic GPU'",
                    "test_d_diagnostic_description (test_dotted.DottedTest.test_d_diagnostic_description) ... skipped 'needs described diagnostic GPU'",
                ],
            )

    def test_report_keeps_skips_with_description_separators(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_descriptions.py").write_text(
                "import sys\nimport unittest\n\n"
                "class DescriptionTest(unittest.TestCase):\n"
                "    @unittest.skip('needs decorated hardware')\n"
                "    def test_a_decorated(self):\n"
                "        \"\"\"Hardware ... optional\"\"\"\n"
                "        pass\n"
                "    def test_b_runtime(self):\n"
                "        \"\"\"Hardware ... optional ... (torch.cuda)\"\"\"\n"
                "        self.skipTest('needs runtime hardware')\n"
                "    def test_c_diagnostic(self):\n"
                "        \"\"\"Hardware ... optional\"\"\"\n"
                "        print('hardware probe ... ERROR', file=sys.stderr)\n"
                "        self.skipTest('needs diagnostic hardware')\n"
                "    def test_d_pass(self):\n"
                "        \"\"\"Hardware ... optional\"\"\"\n"
                "        print(\"hardware probe ... skipped 'not a test'\", file=sys.stderr)\n"
            )
            commit(repo, "description separators and diagnostic status suffixes")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 4 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_a_decorated (test_descriptions.DescriptionTest.test_a_decorated) ... skipped 'needs decorated hardware'",
                    "test_b_runtime (test_descriptions.DescriptionTest.test_b_runtime) ... skipped 'needs runtime hardware'",
                    "test_c_diagnostic (test_descriptions.DescriptionTest.test_c_diagnostic) ... skipped 'needs diagnostic hardware'",
                ],
            )

    def test_report_keeps_packaged_module_and_class_fixture_skips(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests", "-t", "."]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "__init__.py").touch()
            (repo / "tests" / "test_module_fixture.py").write_text(
                "import unittest\n\n"
                "def setUpModule():\n"
                "    raise unittest.SkipTest('needs module hardware')\n"
                "class ModuleTest(unittest.TestCase):\n"
                "    def test_unused(self):\n"
                "        pass\n"
            )
            (repo / "tests" / "test_class_fixture.py").write_text(
                "import unittest\n\n"
                "class ClassTest(unittest.TestCase):\n"
                "    @classmethod\n"
                "    def setUpClass(cls):\n"
                "        raise unittest.SkipTest('needs class hardware')\n"
                "    def test_unused(self):\n"
                "        pass\n"
                "class PassingTest(unittest.TestCase):\n"
                "    def test_pass(self):\n"
                "        pass\n"
            )
            commit(repo, "packaged fixture skips")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 1 test", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "setUpClass (tests.test_class_fixture.ClassTest) ... skipped 'needs class hardware'",
                    "setUpModule (tests.test_module_fixture) ... skipped 'needs module hardware'",
                ],
            )

    def test_report_keeps_evidence_for_uv_run_unittest_command(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            'run = ["uv", "run", "--no-project", "--no-managed-python", "--offline", '
            f'{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_uv_evidence.py").write_text(
                "import unittest\n\n"
                "class UvEvidenceTest(unittest.TestCase):\n"
                "    @unittest.skip('needs uv hardware')\n"
                "    def test_hardware(self):\n"
                "        \"\"\"Hardware ... optional (torch.cuda)\"\"\"\n"
                "        pass\n"
                "    def test_pass(self):\n"
                "        pass\n"
            )
            commit(repo, "uv-run skipped suite")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 2 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_hardware (test_uv_evidence.UvEvidenceTest.test_hardware) ... skipped 'needs uv hardware'",
                ],
            )

    def test_attempt_preserves_noninterpreter_unittest_arguments(self) -> None:
        import json

        commands = (
            ("coverage", "run", "-m", "unittest"),
            ("pytest", "-m", "unittest"),
            (sys.executable, "wrapper.py", "-m", "unittest"),
            (sys.executable, "-m", "coverage", "run", "-m", "unittest"),
            ("uv", "run", "--offline", "coverage", "run", "-m", "unittest"),
        )
        for command in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "repo"
                argv = [str(repo / command[0]), *command[1:]] if command[0] in {"coverage", "pytest", "uv"} else list(command)
                expected = list(command[1:])
                filename = command[0]
                if command[0] == sys.executable:
                    filename = "coverage.py" if command[1] == "-m" else "wrapper.py"
                    expected = list(command[3:] if command[1] == "-m" else command[2:])
                toml = TOML + (
                    '\n[[gate.check]]\nname = "test"\n'
                    f'run = {json.dumps(argv)}\n'
                )
                repo = repo_with_test(Path(directory), toml)
                program = repo / filename
                program.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    f"assert sys.argv[1:] == {expected!r}, sys.argv\n"
                )
                program.chmod(0o755)
                commit(repo, "commands with unrelated unittest arguments")

                code, out, report = gate(repo)

                self.assertEqual(code, 0, out + report)
                self.assertIn("- test: PASS", report)

    def test_report_keeps_skip_after_unterminated_runtime_diagnostic(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_partial.py").write_text(
                "import sys\nimport unittest\n\n"
                "class PartialTest(unittest.TestCase):\n"
                "    def test_hardware(self):\n"
                "        print('hardware probe: ', end='', file=sys.stderr)\n"
                "        self.skipTest('needs hardware')\n"
            )
            commit(repo, "unterminated runtime diagnostic")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 1 test", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                [
                    "test_hardware (test_partial.PartialTest.test_hardware) ... skipped 'needs hardware'",
                ],
            )

    def test_report_keeps_evidence_with_python_interpreter_flags(self) -> None:
        import json

        for prefix in ((sys.executable, "-P", "-E"), ("uv", "run", sys.executable)):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                toml = TOML + (
                    '\n[[gate.check]]\nname = "test"\n'
                    f'run = {json.dumps([*prefix, "-m", "unittest", "discover", "-s", "tests"])}\n'
                )
                repo = repo_with_test(Path(directory), toml)
                (repo / "tests" / "test_flags.py").write_text(
                    "import unittest\n\n"
                    "class FlagsTest(unittest.TestCase):\n"
                    "    @unittest.skip('needs hardware')\n"
                    "    def test_hardware(self):\n"
                    "        \"\"\"Hardware ... optional\"\"\"\n"
                    "        pass\n"
                )
                commit(repo, "Python interpreter flags and bare uv run")

                code, out, report = gate(repo)

                self.assertEqual(code, 0, out + report)
                self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
                self.assertIn("Ran 1 test", report)
                self.assertEqual(
                    [line for line in report.splitlines() if " ... skipped " in line],
                    ["test_hardware (test_flags.FlagsTest.test_hardware) ... skipped 'needs hardware'"],
                )

    def test_attempt_preserves_wrapper_and_uv_subcommand_lookalikes(self) -> None:
        import json

        for name, subcommand in (("coverage", "run"), ("wrapper", "run"), ("uv", "help"), ("uv", "tree")):
            with self.subTest(name=name, subcommand=subcommand), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "repo"
                arguments = [subcommand, sys.executable, "-m", "unittest", "discover", "-s", "tests"]
                toml = TOML + (
                    '\n[[gate.check]]\nname = "test"\n'
                    f'run = {json.dumps([str(repo / name), *arguments])}\n'
                )
                repo = repo_with_test(Path(directory), toml)
                program = repo / name
                program.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    f"assert sys.argv[1:] == {arguments!r}, sys.argv\n"
                )
                program.chmod(0o755)
                commit(repo, "wrapper and uv subcommand lookalikes")

                code, out, report = gate(repo)

                self.assertEqual(code, 0, out + report)
                self.assertIn("- test: PASS", report)

    def test_attempt_preserves_uv_option_values_and_module_mode(self) -> None:
        import json

        prefixes = (
            ("--python", sys.executable),
            ("-p", sys.executable),
            ("--offline", "--python", sys.executable),
            ("--python", sys.executable, "python"),
            (f"--python={sys.executable}", "python"),
            ("--project", "python"),
            ("--unknown-option", "python"),
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory) / "repo"
                arguments = ["run", *prefix, "-m", "unittest", "discover", "-s", "tests"]
                toml = TOML + (
                    '\n[[gate.check]]\nname = "test"\n'
                    f'run = {json.dumps([str(repo / "uv"), *arguments])}\n'
                )
                repo = repo_with_test(Path(directory), toml)
                program = repo / "uv"
                program.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    f"assert sys.argv[1:] == {arguments!r}, sys.argv\n"
                )
                program.chmod(0o755)
                commit(repo, "uv option values and module mode")

                code, out, report = gate(repo)

                self.assertEqual(code, 0, out + report)
                self.assertIn("- test: PASS", report)

    def test_report_ignores_bare_diagnostic_skips_before_identified_results(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_bare_status.py").write_text(
                "import sys\nimport unittest\n\n"
                "class BareStatusTest(unittest.TestCase):\n"
                "    def test_a_pass(self):\n"
                "        print(\"\\nskipped 'not a test'\", file=sys.stderr)\n"
                "    def test_b_skip(self):\n"
                "        print(\"\\nskipped 'not a test'\", file=sys.stderr)\n"
                "        self.skipTest('needs hardware')\n"
            )
            commit(repo, "bare diagnostic skips before real results")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
            self.assertIn("Ran 2 tests", report)
            self.assertEqual(
                [line for line in report.splitlines() if " ... skipped " in line],
                ["test_b_skip (test_bare_status.BareStatusTest.test_b_skip) ... skipped 'needs hardware'"],
            )

    def test_report_fails_closed_when_unittest_exits_without_a_result_record(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        for action in ("path.write_text('invalid JSON')", "path.unlink()"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                repo = repo_with_test(Path(directory), toml)
                (repo / "tests" / "test_aborted.py").write_text(
                    "import os, unittest\nfrom pathlib import Path\n"
                    "class Aborted(unittest.TestCase):\n"
                    "    def test_abort(self):\n"
                    "        path = Path(os.environ['FACTORY_UNITTEST_RESULT'])\n"
                    f"        {action}\n"
                    "        print('process exited before result delivery', flush=True)\n"
                    "        os._exit(0)\n"
                )
                commit(repo, "unittest exited before recording results")

                code, out, report = gate(repo)

                self.assertEqual(code, 1, out + report)
                self.assertIn("- test: FAIL", report)
                self.assertIn("process exited before result delivery", report)
                self.assertIn("unittest result record unavailable:", report)
                self.assertNotIn("## test test evidence", report)

    def test_report_includes_evidence_only_for_passing_status_values(self) -> None:
        import json
        from unittest import mock

        # Cross the status producer/renderer boundary with equal, distinct strings.
        # A report must depend on the status value, not Python's string interning.
        driver = (
            "import sys\nfrom factory import gate\n"
            "original = gate._check_status\n"
            "gate._check_status = lambda name, passed, output, blocking: "
            "original(name, passed, output, True).encode().decode()\n"
            "raise SystemExit(gate.main(sys.argv[1:]))\n"
        )
        original_run = subprocess.run
        for name, assertion, status, exit_code in (
            ("test", "self.assertTrue(True)", "PASS", 0),
            ("test", "self.fail('broken check')", "FAIL", 1),
            ("mutation", "print('no mutable files')", "SKIP (no changed factory code)", 0),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                toml = TOML + (
                    f'\n[[gate.check]]\nname = "{name}"\n'
                    f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
                )
                repo = repo_with_test(Path(directory), toml)
                (repo / "tests" / "test_status.py").write_text(
                    "import unittest\nclass Status(unittest.TestCase):\n"
                    f"    def test_check(self):\n        {assertion}\n"
                )
                commit(repo, "unittest status evidence")

                def run(command, *args, **kwargs):
                    if command[:4] == [sys.executable, "-m", "factory", "gate"]:
                        command = [sys.executable, "-c", driver, *command[4:]]
                    return original_run(command, *args, **kwargs)

                with mock.patch.object(subprocess, "run", side_effect=run):
                    code, out, report = gate(repo)

                self.assertEqual(code, exit_code, out + report)
                self.assertIn(f"- {name}: {status}", report)
                if status == "PASS":
                    self.assertIn(f"## {name} test evidence\n```\nRan 1 tests\n```", report)
                else:
                    self.assertNotIn(f"## {name} test evidence", report)
                if status == "FAIL":
                    self.assertIn("broken check", report)

    def test_report_fails_closed_for_malformed_or_wrongly_shaped_result_records(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        records = [
            "invalid JSON", "{}", "[]", "null", "1",
            '{"testsRun": 1}', '{"skipped": []}',
            *[json.dumps({"testsRun": count, "skipped": []})
              for count in (None, True, -1, 1.5, "1", [], {})],
            *[json.dumps({"testsRun": 1, "skipped": skipped})
              for skipped in (None, {}, "", [None], [[]], [{}],
                              [{"name": "test"}], [{"reason": "hardware"}],
                              [{"name": None, "reason": "hardware"}],
                              [{"name": "test", "reason": None}])],
        ]
        for record in records:
            with self.subTest(record=record), tempfile.TemporaryDirectory() as directory:
                repo = repo_with_test(Path(directory), toml)
                (repo / "tests" / "test_bad_record.py").write_text(
                    "import os, unittest\nfrom pathlib import Path\n"
                    "class BadRecord(unittest.TestCase):\n"
                    "    def test_record(self):\n"
                    f"        Path(os.environ['FACTORY_UNITTEST_RESULT']).write_text({record!r})\n"
                    "        print('child diagnostic retained', flush=True)\n"
                    "        os._exit(0)\n"
                )
                commit(repo, "malformed unittest result record")

                code, out, report = gate(repo)

                self.assertEqual(code, 1, out + report)
                self.assertIn("- test: FAIL", report, out)
                self.assertIn(f"Head SHA: {git(repo, 'rev-parse', 'HEAD')}", report)
                self.assertIn("child diagnostic retained", report)
                self.assertIn("unittest result record unavailable:", report)
                self.assertNotIn("## test test evidence", report)

    def test_report_accepts_zero_tests_in_result_record(self) -> None:
        import json

        toml = TOML + (
            '\n[[gate.check]]\nname = "test"\n'
            f'run = [{json.dumps(sys.executable)}, "-m", "unittest", "discover", "-s", "tests"]\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = repo_with_test(Path(directory), toml)
            (repo / "tests" / "test_zero.py").write_text(
                "import os, unittest\nfrom pathlib import Path\n"
                "class ZeroRecord(unittest.TestCase):\n"
                "    def test_record(self):\n"
                "        Path(os.environ['FACTORY_UNITTEST_RESULT']).write_text("
                "'{\"testsRun\": 0, \"skipped\": []}')\n"
                "        os._exit(0)\n"
            )
            commit(repo, "valid zero-count unittest result record")

            code, out, report = gate(repo)

            self.assertEqual(code, 0, out + report)
            self.assertIn("- test: PASS", report)
            self.assertIn("## test test evidence\n```\nRan 0 tests\n```", report)


if __name__ == "__main__":
    unittest.main()
