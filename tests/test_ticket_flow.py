"""Behavior checks for the canonical ticket-flow graph parser."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.workspace import parse_ticket_flow

ROOT = Path(__file__).resolve().parents[1]


class TicketFlowParserTest(unittest.TestCase):
    def test_canonical_map_preserves_every_group_node_edge_gap_and_citation(
        self,
    ) -> None:
        text = (ROOT / "docs/ticket-flow.md").read_text()
        graph = parse_ticket_flow(text)

        by_kind = {
            kind: {node["id"] for node in graph["nodes"] if node["kind"] == kind}
            for kind in ("view", "state", "action", "out")
        }
        self.assertEqual(
            by_kind,
            {
                "view": {"intake", "build", "approve", "recover"},
                "state": {"NV", "NT", "NI", "INIT", "RFA", "FA", "MERGED", "RFH", "WONT"},
                "action": {
                    "VIA",
                    "TRI",
                    "CLAIM",
                    "WR",
                    "PUSH",
                    "REV",
                    "MAPP",
                    "REF",
                    "MERGE",
                    "ESC",
                    "MGR",
                },
                "out": {"REPORTER", "HUMAN", "CI", "UPSTREAM", "EXTPR"},
            },
        )
        documented_edge_lines = [
            number
            for number, line in enumerate(text.splitlines(), 1)
            if "-->|" in line or "-.->|" in line
        ]
        self.assertEqual([edge["line"] for edge in graph["edges"]], documented_edge_lines)
        self.assertEqual(
            {
                (edge["from"], edge["to"], edge["line"])
                for edge in graph["edges"]
                if edge["gap"]
            },
            {("TRI", "HUMAN", 73), ("REPORTER", "NT", 75), ("MAPP", "ESC", 97)},
        )
        self.assertEqual(
            [node["label"] for node in graph["nodes"] if node["kind"] == "view"],
            [
                "View: Intake and triage",
                "View: Build",
                "View: Approval and merge",
                "View: Recovery",
            ],
        )
        queued = next(node for node in graph["nodes"] if node["id"] == "NV")
        self.assertEqual(queued["line"], 25)
        self.assertEqual(queued["group"], "Intake and triage")
        self.assertTrue(all(edge["citations"] for edge in graph["edges"]))
        self.assertTrue(
            all(
                citation["line"] > 0 and citation["url"] is None
                for edge in graph["edges"]
                for citation in edge["citations"]
            )
        )

    def test_supported_forms_keep_lines_and_resolve_symbols_to_pinned_links(self) -> None:
        markdown = """# Fixture
```mermaid
flowchart TB
  subgraph intake[\"View: Intake\"]
    A([\"queued\"])
    B[\"run alpha.go\"]
  end
  O{{\"Human\"}}
  A -->|\"alpha.go\"| B
  B -.->|\"manual: alpha.FLAG\"| O
  classDef state fill:#fff
  class A state
```
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "alpha.py").write_text("FLAG = True\n\n\ndef go():\n    pass\n")
            graph = parse_ticket_flow(markdown, code_root=root, revision="a" * 40)

        self.assertEqual(
            graph["nodes"],
            [
                {
                    "id": "intake",
                    "label": "View: Intake",
                    "kind": "view",
                    "group": "",
                    "line": 4,
                },
                {
                    "id": "A",
                    "label": "queued",
                    "kind": "state",
                    "group": "Intake",
                    "line": 5,
                },
                {
                    "id": "B",
                    "label": "run alpha.go",
                    "kind": "action",
                    "group": "Intake",
                    "line": 6,
                },
                {
                    "id": "O",
                    "label": "Human",
                    "kind": "out",
                    "group": "",
                    "line": 8,
                },
            ],
        )
        self.assertEqual(
            [
                (edge["from"], edge["to"], edge["gap"], edge["line"])
                for edge in graph["edges"]
            ],
            [("A", "B", False, 9), ("B", "O", True, 10)],
        )
        self.assertEqual(
            graph["edges"][0]["citations"][0],
            {
                "symbol": "alpha.go",
                "path": "factory/alpha.py",
                "line": 4,
                "url": (
                    "https://github.com/mikeroySoft/factory/blob/"
                    f"{'a' * 40}/factory/alpha.py#L4"
                ),
            },
        )
        self.assertEqual(graph["edges"][1]["citations"][0]["line"], 1)

    def test_graph_bearing_syntax_is_rejected_instead_of_omitted(self) -> None:
        for syntax in ('A --> B', 'A{"choice"}', 'click A "https://example.test"'):
            with self.subTest(syntax=syntax), self.assertRaisesRegex(
                RuntimeError, "unsupported Mermaid syntax"
            ):
                parse_ticket_flow(
                    f'```mermaid\nflowchart TB\nA["a"]\n{syntax}\n```\n'
                )

        with self.assertRaisesRegex(RuntimeError, "unknown node 'B'"):
            parse_ticket_flow(
                '```mermaid\nflowchart TB\nA["a"]\nA -->|"edge"| B\n```\n'
            )


if __name__ == "__main__":
    unittest.main()
