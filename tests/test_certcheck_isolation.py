from __future__ import annotations

import ast
import unittest
from pathlib import Path

from factory import certcheck

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "factory.dispatch",
    "factory.manage",
    "factory.results",
    "factory.decisions",
    "factory.lifecycle",
    "factory.trace",
}


class CertificateCheckerIsolationTest(unittest.TestCase):
    def test_checker_does_not_import_dispatcher_evidence_code(self) -> None:
        tree = ast.parse((ROOT / "factory" / "certcheck.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                imported.update(f"{module}.{alias.name}" for alias in node.names)
        self.assertEqual(sorted(imported & FORBIDDEN), [])

    def test_canonical_json_is_stable_and_contains_no_wall_clock(self) -> None:
        first = certcheck.canonical({"premises": {"gate": {"at": "source-row-time"}}, "ticket": 7})
        second = certcheck.canonical({"ticket": 7, "premises": {"gate": {"at": "source-row-time"}}})
        self.assertEqual(first, second)
        self.assertEqual(first, b'{"premises":{"gate":{"at":"source-row-time"}},"ticket":7}\n')


if __name__ == "__main__":
    unittest.main()
