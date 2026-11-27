"""Survivor replay through the real producer and isolated test harness."""
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


class MutationReplayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-replay-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()
        self.audit = Path(temporary.name) / "audit"
        self.proof = Path(temporary.name) / "full.json"
        self.output_proof = Path(temporary.name) / "replay.json"
        self.scratch = Path(temporary.name) / "tmp"
        self.scratch.mkdir()
        for name in ("factory", "scripts", "tests"):
            (self.root / name).mkdir()
            (self.root / name / "__init__.py").touch()
        for name in ("scripts/mutation.py", "factory/mutation_scope.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / "factory/example.py").write_text("# before\nvalue = 0\n")
        (self.root / ".factory.toml").write_text(
            '[repo]\nslug = "fixture/project"\n[gate]\ntimeout = 120\n'
            '[[gate.check]]\nname = "mutation"\nwhen = "pr"\nproof = "mutation-v1"\n'
            f'run = {json.dumps([str(self.root / ".venv/bin/python"), "scripts/mutation.py"])}\n'
        )
        (self.root / "AGENTS.md").write_text("`factory/example.py` `tests/test_example.py`\n")
        self.git("init", "-b", "main")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        (self.root / "factory/example.py").write_text("# before\nvalue = 1\n")
        (self.root / "tests/test_example.py").write_text(
            "import os, unittest\nfrom pathlib import Path\nfrom factory import example\n"
            "class Related(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        with Path(os.environ['REPLAY_AUDIT']).open('a') as out: out.write('related\\n')\n"
            "        self.assertNotEqual(example.value, 0)\n"
        )
        (self.root / "tests/test_z_unrelated.py").write_text(
            "import os, unittest\nfrom pathlib import Path\n"
            "class Unrelated(unittest.TestCase):\n"
            "    def test_other(self):\n"
            "        with Path(os.environ['REPLAY_AUDIT']).open('a') as out: out.write('unrelated\\n')\n"
        )
        self.git("add", ".")
        self.git("commit", "-m", "one survivor")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def cli(self, *args, proof=None):
        env = {key: value for key, value in os.environ.items()
               if key not in ("FACTORY_MUTATION_PROOF", "FACTORY_MUTATION_PROOF_BASE",
                              "VIRTUAL_ENV", "UV_NO_SYNC")}
        env.update(TMPDIR=str(self.scratch), REPLAY_AUDIT=str(self.audit), UV_OFFLINE="1",
                   UV_PROJECT_ENVIRONMENT=str(self.root / ".venv"))
        if proof:
            env.update(FACTORY_MUTATION_PROOF=str(proof), FACTORY_MUTATION_PROOF_BASE=self.base)
        result = subprocess.run(
            ["uv", "run", "--project", str(ROOT), "--frozen", "--no-managed-python",
             "--python", sys.executable, "--group", "mutation", "python", "scripts/mutation.py",
             *args], cwd=self.root, capture_output=True, text=True, timeout=90, env=env, check=False,
        )
        return result.returncode, result.stdout + result.stderr

    def full_proof(self):
        code, report = self.cli("--blocking", proof=self.proof)
        self.assertEqual(code, 1, report)
        record = json.loads(self.proof.read_text())
        self.assertEqual(record["counts"]["survived"], 1, report)
        self.assertEqual(len(record["survivors"]), 1)
        self.audit.unlink()
        return self.proof.read_bytes()

    def test_survivor_then_killed_without_baseline_or_completion_record(self):
        original = self.full_proof()
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        self.assertIn("factory/example.py:2: survived", report)
        self.assertEqual(self.audit.read_text().splitlines(), ["related", "unrelated"])
        self.assertNotIn("baseline:", report)
        self.assertFalse(self.output_proof.exists())
        self.assertEqual(self.proof.read_bytes(), original)
        test = self.root / "tests/test_example.py"
        test.write_text(test.read_text() + "        self.assertEqual(example.value, 1)\n")
        self.audit.unlink()
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        self.assertIn("factory/example.py:2: killed", report)
        self.assertEqual(self.audit.read_text().splitlines(), ["related"])
        self.assertFalse(self.output_proof.exists())
        self.assertEqual(self.proof.read_bytes(), original)
        self.assertEqual((self.root / "factory/example.py").read_text(), "# before\nvalue = 1\n")

    def test_changed_source_is_not_replayed(self):
        self.full_proof()
        (self.root / "factory/example.py").write_text("# before\nvalue = 3\n")
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        self.assertIn("factory/example.py:2: no longer applies", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())

    def test_earlier_mutation_occurrence_drift_keeps_exact_target(self):
        self.full_proof()
        (self.root / "factory/example.py").write_text("other = 1\nvalue = 1\n")
        code, report = self.cli("--replay", str(self.proof))
        self.assertEqual(code, 0, report)
        self.assertIn("factory/example.py:2: survived", report)
        self.assertEqual(self.audit.read_text().splitlines(), ["related", "unrelated"])

    def test_legacy_proof_and_unsafe_paths_fail_without_running_tests(self):
        self.full_proof()
        record = json.loads(self.proof.read_text())
        for change in ("legacy", "path"):
            with self.subTest(change=change):
                bad = json.loads(json.dumps(record))
                if change == "legacy":
                    del bad["survivors"]
                else:
                    bad["survivors"][0]["file"] = "../outside.py"
                self.proof.write_text(json.dumps(bad))
                code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
                self.assertEqual(code, 1, report)
                self.assertIn("invalid replay proof:", report)
                self.assertFalse(self.audit.exists())
                self.assertFalse(self.output_proof.exists())

    def test_pr_gate_ignores_saved_replay_output_and_runs_full_proof(self):
        self.full_proof()
        code, replay = self.cli("--replay", str(self.proof))
        self.assertEqual(code, 0, replay)
        saved = self.root / ".factory"
        saved.mkdir()
        report = saved / "gate-report.md"
        report.write_text(replay)
        head = self.git("rev-parse", "HEAD")
        (saved / f"mutation-{head}.json").write_text(replay)
        result = subprocess.run(
            [str(self.root / ".venv/bin/python"), "-c",
             (f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
              "from factory.cli import main; sys.exit(main())"),
             "gate", "--tier", "pr", "--base", self.base, "--mutation-proof",
             "--blocking-mutation", "--report", str(report)],
            cwd=self.root, capture_output=True, text=True, timeout=90, check=False,
            env={**os.environ, "TMPDIR": str(self.scratch), "REPLAY_AUDIT": str(self.audit)},
        )
        text = report.read_text()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr + text)
        self.assertIn("baseline:", text)
        self.assertIn("ran 2 of 2 mutants", text)
        self.assertIn("1 survived", text)
        self.assertIn("- mutation: FAIL", text)


if __name__ == "__main__":
    unittest.main()
