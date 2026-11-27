"""Replay recorded survivors after unrelated source lines move."""
from __future__ import annotations

from tests import test_mutation_replay as replay_tests


class MutationReplayRelocationTest(replay_tests.MutationReplayTest):
    def test_inserted_and_deleted_lines_replay_the_unchanged_survivor(self):
        original = self.full_proof()
        for source, line in (("# added\n# before\nvalue = 1\n", 3), ("value = 1\n", 1)):
            with self.subTest(line=line):
                (self.root / "factory/example.py").write_text(source)
                code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
                self.assertEqual(code, 0, report)
                self.assertIn(f"factory/example.py:{line}: survived", report)
                self.assertNotIn("no longer applies", report)
                self.assertEqual(self.audit.read_text().splitlines(), ["related", "unrelated"])
                self.audit.unlink()
                self.assertFalse(self.output_proof.exists())
                self.assertEqual(self.proof.read_bytes(), original)

    def test_moved_survivor_is_killed_by_the_current_related_test(self):
        self.full_proof()
        (self.root / "factory/example.py").write_text("other = 1\n# before\nvalue = 1\n")
        test = self.root / "tests/test_example.py"
        test.write_text(test.read_text() + "        self.assertEqual(example.value, 1)\n")
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 0, report)
        self.assertIn("factory/example.py:3: killed", report)
        self.assertIn("replayed 1 of 1 applicable survivors", report)
        self.assertEqual(self.audit.read_text().splitlines(), ["related"])
        self.assertNotIn("baseline:", report)
        self.assertFalse(self.output_proof.exists())

    def test_duplicate_identical_targets_fail_without_testing_the_wrong_occurrence(self):
        self.full_proof()
        (self.root / "factory/example.py").write_text("# before\nvalue = 1\nvalue = 1\n")
        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)
        self.assertEqual(code, 1, report)
        self.assertIn("ambiguous survivor", report)
        self.assertNotIn("no longer applies", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())


for _name in [name for name in dir(replay_tests.MutationReplayTest) if name.startswith("test_")]:
    if _name not in MutationReplayRelocationTest.__dict__:
        setattr(MutationReplayRelocationTest, _name, None)
