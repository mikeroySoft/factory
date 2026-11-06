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


class MutationAnnotationsTest(unittest.TestCase):
    def sample(self, source):
        with tempfile.TemporaryDirectory(prefix="mutation-annotations-") as directory:
            root = Path(directory)
            (root / "example.py").write_text(source)
            result = subprocess.run(
                ["uv", "run", "--project", str(ROOT), "--frozen", "--no-managed-python",
                 "--group", "mutation", "python", "-c",
                 ("import json, sys; from pathlib import Path; "
                  "sys.path.insert(0, sys.argv[1]); from scripts.mutation import sample; "
                  "items, hot = sample({'example.py': set(range(1, 100))}, Path('.')); "
                  "print(json.dumps([[m.start_pos, m.end_pos] for m in items]))"), str(ROOT)],
                cwd=root, env=os.environ | {"TMPDIR": directory},
                capture_output=True, text=True, check=True, timeout=60,
            )
            return {tuple(start) for start, end in json.loads(result.stdout.splitlines()[-1])}

    def test_signature_excludes_annotations_but_keeps_body_and_defaults(self):
        source = (
            "from __future__ import annotations\n"
            "def f(x: str | None = None) -> int | None:\n"
            "    return x + x\n"
            "def g(x: int | None = 1 + 2) -> int | None: return x + x\n"
        )
        sites = self.sample(source)
        expected = {(3, 13)}
        line = source.splitlines()[3]
        expected.update((4, column) for column in (line.index("1"), line.index("2"),
                                                 line.index("+"), line.rindex("+")))
        self.assertEqual(sites, expected)

    def test_variable_annotations_and_explicit_aliases_keep_executable_values(self):
        source = (
            "from __future__ import annotations\n"
            "import typing as t\n"
            "from typing import TypeAlias as TA\n"
            "from typing_extensions import TypeAlias\n"
            "value: list[str | None] = choose(1 + 2)\n"
            "Alias: TA = str | None; live = 3 + 4\n"
            "Other: t.TypeAlias = list[int | None]\n"
            "Third: TypeAlias = tuple[str | None, int | None]\n"
        )
        expected = set()
        for number, digits in ((5, ("1", "2")), (6, ("3", "4"))):
            line = source.splitlines()[number - 1]
            expected.update((number, line.index(token)) for token in (*digits, "+"))
        self.assertEqual(self.sample(source), expected)

    def test_multiline_async_annotations_preserve_keyword_defaults_and_calls(self):
        source = (
            "from __future__ import annotations\n"
            "async def f(\n"
            "    x: str | None, *args: int | None,\n"
            "    y: tuple[str | None, int | None] = choose(1 + 2),\n"
            "    **kwargs: str | None,\n"
            ") -> (int | None):\n"
            "    local: int | None = choose(3 + 4)\n"
            "    return x + y\n"
        )
        expected = {(8, 13)}
        for number, digits in ((4, ("1", "2")), (7, ("3", "4"))):
            line = source.splitlines()[number - 1]
            expected.update((number, line.index(token)) for token in (*digits, "+"))
        self.assertEqual(self.sample(source), expected)

    @unittest.skipUnless(sys.version_info >= (3, 12), "type statements require Python 3.12")
    def test_type_statements_exclude_bounds_and_alias_value(self):
        source = (
            "type Alias[T: str | None] = list[T | None]\n"
            "type Other = int | None; live = 1 + 2\n"
            + ("type Default[T = str | None] = T | None\n" if sys.version_info >= (3, 13) else "")
        )
        line = source.splitlines()[1]
        self.assertEqual(self.sample(source), {(2, line.index(token)) for token in ("1", "+", "2")})

    def test_non_ascii_prefix_does_not_hide_executable_sites(self):
        source = "def café(x: str | None = 1 + 2) -> int | None: return x + x\n"
        expected = {(1, source.index(token)) for token in ("1", "+", "2")}
        expected.add((1, source.rindex("+")))
        self.assertEqual(self.sample(source), expected)

    def test_annotation_only_cli_reports_exclusions_without_survivors_or_errors(self):
        with tempfile.TemporaryDirectory(prefix="mutation-annotations-cli-") as directory, \
                tempfile.TemporaryDirectory(prefix="mutation-annotations-tmp-") as tmp:
            root = Path(directory)
            for name in ("factory", "scripts", "tests"):
                (root / name).mkdir()
                (root / name / "__init__.py").touch()
            shutil.copyfile(ROOT / "scripts/mutation.py", root / "scripts/mutation.py")
            path = root / "factory/example.py"
            path.write_text("from __future__ import annotations\ndef f(x: str):\n    return x\n")
            (root / "tests/test_example.py").write_text("import factory.example\n")
            (root / "AGENTS.md").write_text("`factory/example.py` `tests/test_example.py`\n")
            (root / ".factory.toml").write_text("[gate]\ntimeout = 120\n")

            def git(*args):
                subprocess.run(
                    ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                    cwd=root, capture_output=True, text=True, check=True,
                )

            git("init", "-b", "main")
            git("add", "factory", "scripts", "tests", "AGENTS.md", ".factory.toml")
            git("commit", "-m", "fixture baseline")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            path.write_text("from __future__ import annotations\ndef f(x: str | None):\n    return x\n")
            git("add", "factory/example.py")
            git("commit", "-m", "annotation-only change")
            result = subprocess.run(
                ["uv", "run", "--project", str(ROOT), "--frozen", "--no-managed-python",
                 "--group", "mutation", "python", "scripts/mutation.py", "--blocking"],
                cwd=root, env=os.environ | {"TMPDIR": tmp}, capture_output=True,
                text=True, check=False, timeout=90,
            )
            report = result.stdout + result.stderr
            print(f"annotation-only mutation CLI: exit {result.returncode}\n{report}")
            self.assertEqual(result.returncode, 0, report)
            self.assertIn("annotation sites excluded: 1 (11 mutants)", report)
            self.assertIn("ran 0 of 0 mutants", report)
            self.assertIn("0 survived", report)
            self.assertNotIn("incompetent", report)
            self.assertNotIn("mutation check broke", report)
            self.assertNotIn("incomplete:", report)


if __name__ == "__main__":
    unittest.main()
