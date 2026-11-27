"""Exact proof provenance disambiguates unchanged duplicate source targets."""
from __future__ import annotations

import json

from tests import test_mutation_replay as replay_tests


class MutationReplayDuplicateTargetsTest(replay_tests.MutationReplayTest):
    def duplicate_proof(self):
        (self.root / "factory/example.py").write_text("# before\nvalue = 1\nvalue = 1\n")
        self.git("add", "factory/example.py")
        self.git("commit", "-m", "duplicate mutable lines")
        code, report = self.cli("--blocking", proof=self.proof)
        self.assertEqual(code, 1, report)
        self.assertEqual(json.loads(self.proof.read_text())["counts"]["survived"], 3, report)
        self.audit.unlink()

    def test_unchanged_duplicate_targets_replay_their_recorded_positions(self):
        self.duplicate_proof()
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        self.assertEqual(report.count(": survived "), 3, report)
        self.assertIn("replayed 3 of 3 applicable survivors", report)
        self.assertEqual(self.audit.read_text().splitlines().count("related"), 3)
        self.assertFalse(self.output_proof.exists())

    def test_deleted_duplicate_does_not_replay_the_same_current_mutant_twice(self):
        self.duplicate_proof()
        (self.root / "factory/example.py").write_text("# before\nvalue = 1\n")
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 1, report)
        self.assertIn("ambiguous survivor", report)
        self.assertIn("replayed 1 of 1 applicable survivors", report)
        self.assertNotIn(": survived ", report)
        self.assertEqual(self.audit.read_text().splitlines(), ["related"])
        self.assertFalse(self.output_proof.exists())


for _name in [name for name in dir(replay_tests.MutationReplayTest) if name.startswith("test_")]:
    if _name not in MutationReplayDuplicateTargetsTest.__dict__:
        setattr(MutationReplayDuplicateTargetsTest, _name, None)
