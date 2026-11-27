"""Replay refuses truncated identities and unrecorded sibling source targets."""
from __future__ import annotations

import json

from tests import test_mutation_replay as replay_tests


class MutationReplayProvenanceTest(replay_tests.MutationReplayTest):
    def test_lone_unrecorded_sibling_is_not_reported_killed(self):
        source = (
            "# before\nvalue = 1\n\n"
            "def recorded():\n    return 1\n\n"
            "def sibling():\n    return 1\n"
        )
        target = self.root / "factory/example.py"
        target.write_text(source)
        test = self.root / "tests/test_example.py"
        test.write_text(
            test.read_text()
            + "        self.assertEqual(example.value, 1)\n"
            + "        self.assertEqual(example.sibling(), 1)\n"
            + "        self.assertNotEqual(example.recorded(), 0)\n"
        )
        self.git("add", ".")
        self.git("commit", "-m", "only the recorded function survives")
        self.full_proof()
        target.write_text(source.replace("def recorded():\n    return 1", "def recorded():\n    return 3"))

        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)

        self.assertEqual(code, 1, report)
        self.assertIn("ambiguous survivor", report)
        self.assertNotIn(": killed ", report)
        self.assertNotIn(": survived ", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())

    def test_truncated_producer_record_cannot_replay_as_zero_survivors(self):
        target = self.root / "factory/example.py"
        target.write_text("# before\nvalue = 1  # " + "x" * (64 * 1024) + "\n")
        self.git("add", "factory/example.py")
        self.git("commit", "-m", "survivor source exceeds completion boundary")
        code, report = self.cli("--blocking", proof=self.proof)
        self.assertEqual(code, 1, report)
        raw = self.proof.read_bytes()
        record = json.loads(raw)
        self.assertLessEqual(len(raw), 64 * 1024)
        self.assertEqual(record["counts"]["survived"], 1)
        self.assertEqual(record["counts"]["blocking_survivors"], 1)
        self.assertIs(record.get("survivors_truncated"), True)
        self.assertNotIn("survivors", record)
        self.assertFalse(self.proof.with_suffix(".survivors.json").exists())
        self.audit.unlink()
        test = self.root / "tests/test_example.py"
        test.write_text(test.read_text() + "        self.assertEqual(example.value, 1)\n")

        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)

        self.assertEqual(code, 1, report)
        self.assertIn("invalid replay proof:", report)
        self.assertIn("truncated", report)
        self.assertNotIn(": killed ", report)
        self.assertNotIn("replayed 0", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())
        self.assertEqual(self.proof.read_bytes(), raw)

    def test_matching_source_without_proof_head_is_not_reported_killed(self):
        self.full_proof()
        record = json.loads(self.proof.read_text())
        del record["scope"]
        self.proof.write_text(json.dumps(record))
        test = self.root / "tests/test_example.py"
        test.write_text(test.read_text() + "        self.assertEqual(example.value, 1)\n")

        code, report = self.cli("--replay", str(self.proof), proof=self.output_proof)

        self.assertEqual(code, 1, report)
        self.assertIn("ambiguous survivor", report)
        self.assertNotIn(": killed ", report)
        self.assertFalse(self.audit.exists())
        self.assertFalse(self.output_proof.exists())


for _name in [name for name in dir(replay_tests.MutationReplayTest) if name.startswith("test_")]:
    if _name not in MutationReplayProvenanceTest.__dict__:
        setattr(MutationReplayProvenanceTest, _name, None)
