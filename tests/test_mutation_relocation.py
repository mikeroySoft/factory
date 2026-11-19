"""Content/context relocation through the real mutation CLI and attempt gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "# header\n# before 1\n# before 2\n# before 3\nunused = 1\n# after 1\n# after 2\n# after 3\n"


class MutationRelocationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-relocation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("factory", "scripts", "tests"):
            (self.root / name).mkdir()
            (self.root / name / "__init__.py").touch()
        shutil.copyfile(ROOT / "scripts/mutation.py", self.root / "scripts/mutation.py")
        (self.root / "factory/example.py").touch()
        (self.root / "tests/test_example.py").write_text("import factory.example\n")
        (self.root / "AGENTS.md").write_text("`factory/example.py` `tests/test_example.py`\n")
        (self.root / ".factory.toml").write_text(
            '[repo]\nslug = "fixture/project"\n[gate]\ntimeout = 120\n'
            '[[gate.check]]\nname = "mutation"\n'
            f'run = {json.dumps([sys.executable, "scripts/mutation.py", "--equivalents", "reviewed.json"])}\n'
            'when = "pr"\n'
        )
        self.entries = [
            {"file": "factory/example.py", "line": 5, "operator": "core/NumberReplacer",
             "original": "unused = 1", "mutated": f"unused = {value}",
             "argument": "The fixture never reads unused.", "reviewer": "Fixture reviewer"}
            for value in (0, 2)
        ]
        self.git("init", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-m", "fixture baseline")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "factory/example.py").write_text(SOURCE)
        (self.root / "reviewed.json").write_text(json.dumps(self.entries))
        self.git("add", "factory/example.py", "reviewed.json")
        self.git("commit", "-m", "reviewed fixture")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def change(self, source):
        (self.root / "factory/example.py").write_text(source)
        self.git("add", "factory/example.py")
        self.git("commit", "-m", "candidate change")

    def cli(self, *args):
        result = subprocess.run(
            ["env", "-u", "UV_NO_SYNC", "-u", "VIRTUAL_ENV", "UV_OFFLINE=1",
             f"UV_PROJECT_ENVIRONMENT={self.root / '.venv'}",
             "uv", "run", "--project", str(ROOT), "--frozen", "--no-managed-python",
             "--python", sys.executable, "--group", "mutation", "python", "scripts/mutation.py",
             "--blocking", "--equivalents", "reviewed.json", *args],
            cwd=self.root, capture_output=True, text=True, timeout=90, check=False,
            env={key: value for key, value in os.environ.items()
                 if key not in ("FACTORY_MUTATION_PROOF", "FACTORY_MUTATION_PROOF_BASE")},
        )
        return result.returncode, result.stdout + result.stderr

    def test_inserted_lines_preserve_next_proof(self):
        self.change("# inserted\n\n" + SOURCE)
        status, report = self.cli()
        self.assertEqual(status, 0, report)
        self.assertEqual(report.count("accepted equivalent:"), 2, report)
        self.assertIn("relocated equivalent: factory/example.py:5 -> 7", report)
        self.assertIn("2 survived", report)

    def test_changed_deleted_duplicated_or_context_changed_is_stale(self):
        sources = {
            "changed": SOURCE.replace("unused = 1", "unused = 3"),
            "deleted": SOURCE.replace("unused = 1\n", ""),
            "duplicate": SOURCE + SOURCE,
            "context": SOURCE.replace("# before 2", "# different context"),
            "moved context": "# inserted\n" + SOURCE.replace("# after 3", "# changed"),
        }
        for label, source in sources.items():
            with self.subTest(label=label):
                self.change(source)
                status, report = self.cli("--check-equivalents")
                self.assertNotEqual(status, 0, report)
                self.assertIn("stale equivalent:", report)
                self.assertNotIn("mutants:", report)

    def test_duplicate_text_with_distinct_context_resolves_uniquely(self):
        self.change("# unrelated\nunused = 1\n# different\n" + SOURCE)
        status, report = self.cli("--check-equivalents")
        self.assertEqual(status, 0, report)
        self.assertIn("relocated equivalent: factory/example.py:5 -> 8", report)

    def test_killed_mutant_at_moved_line_is_stale(self):
        self.change("# inserted\n\n" + SOURCE)
        (self.root / "tests/test_example.py").write_text(
            "import unittest\nfrom factory.example import unused\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self): self.assertEqual(unused, 1)\n"
        )
        status, report = self.cli()
        self.assertNotEqual(status, 0, report)
        self.assertIn("2 killed; 0 survived", report)
        self.assertEqual(report.count("stale equivalent: factory/example.py:7:"), 2, report)

    def attempt_gate(self):
        result = subprocess.run(
            [sys.executable, "-c",
             (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
              "from factory import gate; raise SystemExit(gate.main(sys.argv[1:]))"),
             "--report", ".factory/report.md"],
            cwd=self.root, capture_output=True, text=True, timeout=30, check=False,
        )
        report = (self.root / ".factory/report.md").read_text()
        return result.returncode, report

    def test_attempt_gate_rejects_orphan_before_pr_mutation(self):
        self.change(SOURCE.replace("# before 2", "# changed context"))
        status, report = self.attempt_gate()
        self.assertEqual(status, 1, report)
        self.assertIn("mutation-equivalents: FAIL", report)
        self.assertIn("stale equivalent:", report)
        self.assertIn("mutation: SKIP (pr tier)", report)

    def test_attempt_gate_journals_relocations_without_running_mutants(self):
        self.change("# inserted\n\n" + SOURCE)
        status, report = self.attempt_gate()
        self.assertEqual(status, 0, report)
        self.assertIn("mutation-equivalents: PASS", report)
        self.assertIn("mutation: SKIP (pr tier)", report)
        events = [json.loads(line) for line in
                  (self.root / ".factory/events.jsonl").read_text().splitlines()]
        relocations = [event["detail"] for event in events
                       if event.get("kind") == "equivalent-relocation"]
        self.assertEqual(len(relocations), 2, events)
        self.assertTrue(all("factory/example.py:5 -> 7:" in line for line in relocations))
        self.assertTrue(all("context revision" in line for line in relocations))

    def test_absent_identity_at_moved_line_is_stale(self):
        self.entries[0]["operator"] = "core/ObsoleteReplacer"
        (self.root / "reviewed.json").write_text(json.dumps(self.entries))
        self.git("add", "reviewed.json")
        self.git("commit", "-m", "review obsolete operator fixture")
        self.change("# inserted\n\n" + SOURCE)
        status, report = self.cli()
        self.assertEqual(status, 1, report)
        self.assertIn("stale equivalent: factory/example.py:7: core/ObsoleteReplacer", report)

    def test_shallow_history_cannot_supply_reviewed_context(self):
        clone = self.root / "shallow"
        self.git("clone", "--quiet", "--depth=1", self.root.as_uri(), str(clone))
        self.root = clone
        status, report = self.cli("--check-equivalents")
        self.assertEqual(status, 1, report)
        self.assertIn("requires complete Git history", report)

    def test_attempt_gate_ignores_unrelated_pr_commands(self):
        commands = (
            [sys.executable, "-c", "raise SystemExit(7)", "--equivalents", "unrelated.json"],
            [sys.executable, "scripts/mutation.py", "--test-module", "missing_test_module"],
        )
        configuration = self.root / ".factory.toml"
        original = configuration.read_text()
        for command in commands:
            with self.subTest(command=command):
                configuration.write_text(
                    original + '\n[[gate.check]]\nname = "unrelated"\nwhen = "pr"\n'
                    f"run = {json.dumps(command)}\n"
                )
                status, report = self.attempt_gate()
                self.assertEqual(status, 0, report)
                self.assertIn("unrelated: SKIP (pr tier)", report)

    def test_attempt_gate_does_not_run_producer_main(self):
        script = self.root / "scripts/mutation.py"
        script.write_text(
            script.read_text().rsplit('if __name__ == "__main__":', 1)[0]
            + 'if __name__ == "__main__":\n    raise RuntimeError("producer main ran")\n'
        )
        status, report = self.attempt_gate()
        self.assertEqual(status, 0, report)
        self.change(SOURCE.replace("# after 3", "# changed context"))
        status, report = self.attempt_gate()
        self.assertEqual(status, 1, report)
        self.assertIn("stale equivalent:", report)


if __name__ == "__main__":
    unittest.main()
