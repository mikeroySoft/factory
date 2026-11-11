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

    def test_fifo_completion_fails_under_independent_watchdog(self) -> None:
        base, _ = build_repo(self.root, {"factory/example.py": "value = 0\n"},
                             {"factory/example.py": "value = 1\n"})
        script = f'''import json, sys
sys.path.insert(0, {ROOT!r})
from factory import gate
result = gate.run_mutation_proof(
    [sys.executable, "-c", "import os; os.mkfifo(os.environ['FACTORY_MUTATION_PROOF'])"],
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
