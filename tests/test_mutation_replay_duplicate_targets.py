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
        self.assertNotIn("replayed ", report)
        self.assertNotIn(": survived ", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())

    def test_moved_duplicate_targets_replay_after_inserted_and_deleted_lines(self):
        self.duplicate_proof()
        for prefix in ("# added\n# before\n", ""):
            with self.subTest(prefix=prefix):
                (self.root / "factory/example.py").write_text(prefix + "value = 1\nvalue = 1\n")
                code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
                self.assertEqual(code, 0, report)
                self.assertEqual(report.count(": survived "), 3, report)
                self.assertIn("replayed 3 of 3 applicable survivors", report)
                self.assertNotIn("no longer applies", report)
                self.assertEqual(self.audit.read_text().splitlines().count("related"), 3)
                self.audit.unlink()
                self.assertFalse(self.output_proof.exists())

    def test_moved_repeated_returns_replay_their_containing_functions(self):
        source = (
            "# before\nvalue = 1\n\n"
            "def first():\n    return 1\n\n"
            "def second():\n    return 1\n"
        )
        (self.root / "factory/example.py").write_text(source)
        self.git("add", "factory/example.py")
        self.git("commit", "-m", "repeated returns in distinct functions")
        code, report = self.cli("--blocking", proof=self.proof)
        self.assertEqual(code, 1, report)
        recorded = self.proof.read_bytes()
        survivors = json.loads(recorded)["survivors"]
        return_targets = [entry for entry in survivors if entry["original"] == "    return 1"]
        self.assertEqual({entry["line"] for entry in return_targets}, {5, 8}, report)
        self.audit.unlink()
        (self.root / "factory/example.py").write_text("# added\n" + source)
        test = self.root / "tests/test_example.py"
        test.write_text(
            test.read_text()
            + "        self.assertEqual(example.first(), 1)\n"
            + "        self.assertEqual(example.second(), 1)\n"
        )
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        for entry in return_targets:
            self.assertIn(f"factory/example.py:{entry['line'] + 1}: killed", report)
        self.assertIn(f"replayed {len(survivors)} of {len(survivors)} applicable survivors", report)
        self.assertNotIn("no longer applies", report)
        self.assertNotIn("baseline:", report)
        self.assertFalse(self.output_proof.exists())
        self.assertEqual(self.proof.read_bytes(), recorded)

    def test_expanding_an_identical_run_does_not_guess_the_recorded_occurrences(self):
        self.duplicate_proof()
        (self.root / "factory/example.py").write_text("# before\n" + "value = 1\n" * 3)
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 1, report)
        self.assertIn("ambiguous survivor", report)
        self.assertNotIn("no longer applies", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())


for _name in [name for name in dir(replay_tests.MutationReplayTest) if name.startswith("test_")]:
    if _name not in MutationReplayDuplicateTargetsTest.__dict__:
        setattr(MutationReplayDuplicateTargetsTest, _name, None)
