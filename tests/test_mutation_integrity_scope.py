"""Regression coverage for mutation scope diff integrity (#725)."""
from __future__ import annotations

from factory import gate, mutation_scope
from tests.test_mutation_proof import RunnerHarness
from tests.test_mutation_scope import Fixture, build_repo, commit, git

ATTACK_BASE = "first = 0\nsecond = 0\nkeep_3 = 3\nkeep_4 = 4\nkeep_5 = 5\ntail = 0\n"
ATTACK_HEAD = (
    "first = 1\nsecond = 1\nkeep_3 = 3\nkeep_4 = 4\nkeep_5 = 5\ntail = 0\n"
    'payload = """\n++ b/factory/example.py\n"""\n'
)
ATTACK_LINES = {"factory/example.py": {1, 2, 7, 8, 9}}


class MaliciousHeaderIntegrityTest(RunnerHarness):
    def test_added_header_text_survives_compute_runner_and_reader(self) -> None:
        git(self.root, "checkout", "-q", "--detach", self.base)
        self.base = commit(self.root, {"factory/example.py": ATTACK_BASE}, "integrity base")
        git(self.root, "update-ref", "refs/remotes/origin/main", self.base)
        self.head = commit(self.root, {"factory/example.py": ATTACK_HEAD}, "integrity head")

        expected = mutation_scope.compute(self.base)
        code, output, _ = self.runner(["--blocking"], sampled=([], 0))
        record = self.record()
        verdict = gate.read_mutation_proof(self.proof.read_bytes(), code, expected)

        self.assertEqual(expected.lines, ATTACK_LINES)
        self.assertEqual((code, "no mutable files" in output), (0, False))
        self.assertEqual(
            (record["status"], record["reason"], record["files"], record["scope"]["lines"]),
            ("complete", "no-mutation-candidates", ["factory/example.py"], 5),
        )
        self.assertEqual(verdict[0], "PASS")

    def test_non_lf_separators_cannot_erase_last_hunk_scope(self) -> None:
        for payload, lines in (
            ('payload = """a\f+b\f+c\n++ b/factory/example.py\n"""\n', {1, 2, 7, 8, 9}),
            ('payload = """\u2028+++ b/factory/example.py\u2028"""\n', {1, 2, 7}),
        ):
            with self.subTest(payload=payload):
                git(self.root, "checkout", "-q", "--detach", self.base)
                self.base = commit(self.root, {"factory/example.py": ATTACK_BASE}, "separator base")
                source = ATTACK_HEAD[:ATTACK_HEAD.index("payload")] + payload
                compile(source, "factory/example.py", "exec")
                self.head = commit(self.root, {"factory/example.py": source}, "separator head")

                expected = mutation_scope.compute(self.base)
                code, output, _ = self.runner(["--blocking"], sampled=([], 0))
                record = self.record()

                self.assertEqual(expected.lines, {"factory/example.py": lines})
                self.assertNotIn("no mutable files", output)
                self.assertEqual(
                    (code, record["status"], record["reason"], record["scope"]["lines"]),
                    (0, "complete", "no-mutation-candidates", len(lines)),
                )
                self.assertEqual(
                    gate.read_mutation_proof(self.proof.read_bytes(), code, expected)[0], "PASS",
                )

    def test_git_decoding_preserves_carriage_returns_inside_source(self) -> None:
        git(self.root, "checkout", "-q", "--detach", self.base)
        self.base = commit(self.root, {"factory/example.py": ATTACK_BASE}, "raw base")
        source = ATTACK_HEAD.replace('payload = """', 'payload = """a\r+b\r+c')
        compile(source, "factory/example.py", "exec")
        self.head = commit(self.root, {"factory/example.py": source}, "raw head")

        expected = mutation_scope.compute(self.base)
        code, output, _ = self.runner(["--blocking"], sampled=([], 0))

        self.assertEqual(expected.lines, ATTACK_LINES)
        self.assertNotIn("no mutable files", output)
        self.assertEqual(gate.read_mutation_proof(self.proof.read_bytes(), code, expected)[0], "PASS")


class RawDiffIntegrityTest(Fixture):
    def test_diff_attribute_cannot_hide_python_change(self) -> None:
        scope = self.scope(
            {".gitattributes": "factory/example.py -diff\n", "factory/example.py": "x = 1\ny = 2\n"},
            {"factory/example.py": "x = 1\ny = 3\n"},
        )
        self.assertEqual(scope.lines, {"factory/example.py": {2}})

    def test_custom_textconv_cannot_hide_python_change(self) -> None:
        self.base, self.head = build_repo(
            self.root,
            {".gitattributes": "factory/example.py diff=hide\n", "factory/example.py": "x = 1\n"},
            {"factory/example.py": "x = 2\n"},
        )
        git(self.root, "config", "diff.hide.textconv", "true")
        scope = mutation_scope.compute("origin/main", self.root)
        self.assertEqual(scope.lines, {"factory/example.py": {1}})

    def test_plain_multi_hunk_diff_scope_is_unchanged(self) -> None:
        scope = self.scope(
            {
                "factory/a.py": "a = 0\nb = 0\nkeep_3 = 3\nkeep_4 = 4\nkeep_5 = 5\nkeep_6 = 6\nz = 0\n",
                "factory/b.py": "value = 0\n",
            },
            {
                "factory/a.py": "a = 1\nb = 1\nkeep_3 = 3\nkeep_4 = 4\nkeep_5 = 5\nkeep_6 = 6\nz = 1\n",
                "factory/b.py": "value = 1\n",
            },
        )
        self.assertEqual(scope.lines, {"factory/a.py": {1, 2, 7}, "factory/b.py": {1}})

