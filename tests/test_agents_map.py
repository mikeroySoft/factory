"""AGENTS.md's feature map names every factory module and only files that exist."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mapped() -> list[str]:
    section = (ROOT / "AGENTS.md").read_text().split("\n## Feature map\n", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"`([^`\s]+)`", section)


class FeatureMapTest(unittest.TestCase):
    def test_every_factory_module_is_mapped(self) -> None:
        named = set(mapped())
        modules = (p.relative_to(ROOT).as_posix() for p in sorted(ROOT.glob("factory/**/*.py")))
        self.assertEqual([m for m in modules if m not in named], [])

    def test_every_mapped_path_exists(self) -> None:
        self.assertEqual([p for p in mapped() if "/" in p and not (ROOT / p).exists()], [])


if __name__ == "__main__":
    unittest.main()
