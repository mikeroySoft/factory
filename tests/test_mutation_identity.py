"""Reviewed function identity survives line drift, not identity changes."""
from __future__ import annotations

import json
import unittest

import test_mutation_relocation as relocation

SOURCE = (
    "def example():\n"
    "    # before 1\n    # before 2\n    # before 3\n"
    "    unused = 1\n"
    "    # after 1\n    # after 2\n    # after 3\n"
)


class MutationIdentityTest(unittest.TestCase):
    def setUp(self):
        self.fixture = relocation.MutationRelocationTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.review(SOURCE)

    def review(self, source):
        self.fixture.change(source)
        original = next(line for line in source.splitlines() if line.strip() == "unused = 1")
        for entry in self.fixture.entries:
            entry.update(
                line=source.splitlines().index(original) + 1,
                original=original,
                mutated=original.replace("1", entry["mutated"][-1]),
            )
        (self.fixture.root / "reviewed.json").write_text(json.dumps(self.fixture.entries))
        self.fixture.git("add", "reviewed.json")
        self.fixture.git("commit", "-m", "review function identity")

    def test_inserted_code_inside_context_preserves_equivalent(self):
        registry = (self.fixture.root / "reviewed.json").read_bytes()
        self.fixture.change(SOURCE.replace("    unused", "    marker = None\n    unused"))
        status, report = self.fixture.cli()
        self.assertEqual(status, 0, report)
        self.assertEqual(report.count("accepted equivalent:"), 2, report)
        self.assertIn("factory/example.py:5 -> 6", report)
        self.assertEqual((self.fixture.root / "reviewed.json").read_bytes(), registry)

    def test_attempt_gate_accepts_function_identity_after_context_edit(self):
        self.fixture.change(SOURCE.replace("    unused", "    # inserted\n    unused"))
        status, report = self.fixture.attempt_gate()
        self.assertEqual(status, 0, report)
        self.assertIn("mutation-equivalents: PASS", report)
        events = (self.fixture.root / ".factory/events.jsonl").read_text()
        self.assertIn("factory/example.py:5 -> 6", events)

    def test_changed_operator_or_mutated_text_remains_a_survivor(self):
        for field, value in (("operator", "core/ObsoleteReplacer"),
                             ("mutated", "    unused = 99")):
            with self.subTest(field=field):
                entries = [{**self.fixture.entries[0], field: value}]
                (self.fixture.root / "reviewed.json").write_text(json.dumps(entries))
                status, report = self.fixture.cli()
                self.assertEqual(status, 1, report)
                self.assertIn("2 survived", report)
                self.assertNotIn("accepted equivalent:", report)

    def test_changed_original_or_enclosing_function_fails_closed(self):
        for source in (SOURCE.replace("unused = 1", "unused = 3"),
                       SOURCE.replace("def example", "def other")):
            with self.subTest(source=source):
                self.fixture.change(source)
                status, report = self.fixture.cli()
                self.assertEqual(status, 1, report)
                self.assertIn("stale equivalent:", report)
                self.assertNotIn("accepted equivalent:", report)

    def test_identical_source_in_another_function_is_not_ambiguous(self):
        self.fixture.change(SOURCE.replace("def example", "def other") + "\n" + SOURCE)
        status, report = self.fixture.cli("--check-equivalents")
        self.assertEqual(status, 0, report)
        self.assertIn("factory/example.py:5 -> 14", report)

    def test_duplicate_source_in_same_function_fails_closed(self):
        self.fixture.change(SOURCE + SOURCE.split("\n", 1)[1])
        status, report = self.fixture.cli("--check-equivalents")
        self.assertEqual(status, 1, report)
        self.assertIn("stale equivalent:", report)

    def test_class_and_outer_function_are_part_of_identity(self):
        for source, renamed in (
            ("class First:\n" + "\n".join("    " + line for line in SOURCE.splitlines()) + "\n",
             "class Second:"),
            ("def outer():\n    # outer scope\n"
             + "\n".join("    " + line for line in SOURCE.splitlines()) + "\n",
             "def other():"),
        ):
            with self.subTest(scope=source.splitlines()[0]):
                self.review(source)
                self.fixture.change(source.replace(source.splitlines()[0], renamed))
                status, report = self.fixture.cli("--check-equivalents")
                self.assertEqual(status, 1, report)
                self.assertIn("stale equivalent:", report)


if __name__ == "__main__":
    unittest.main()
