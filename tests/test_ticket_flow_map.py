"""#168: docs/ticket-flow.md is an index of factory/, not a freehand sketch.

Label transitions (`gh ... --add-label/--remove-label`, `gh ... create --label`) and
escalate reasons (`escalate`, `sync_escalate`, `withdraw`, `record("escalate", reason=)`)
are extracted from the AST of factory/*.py and compared with the map's tables, both ways.
Every `module.symbol` the map cites must exist in factory/.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config

MAP = ROOT / "docs" / "ticket-flow.md"
PACKAGE = ROOT / "factory"
LABELS = {name: value for name, value in vars(config).items() if name.startswith("LABEL_") and isinstance(value, str)}
LABELS["FACTORY_APPROVED"] = config.LABEL_APPROVED
FLAGS = {"--add-label": "add", "--remove-label": "remove", "--label": "add"}
ESCALATORS = {"escalate": 1, "sync_escalate": 1, "withdraw": 0}  # name -> reason argument index


def label_of(node: ast.expr | None) -> str:
    """A label constant's value; `*` when the label is data-driven."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return LABELS.get(name, "*")


def templates(node: ast.expr, assigns: dict, seen: frozenset = frozenset()) -> set[str]:
    """Reason strings with every runtime value as `{}`; a local name expands to its nearest earlier assignment."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value} if node.value else set()  # "" means "no reason", never escalated
    if isinstance(node, ast.IfExp):
        return templates(node.body, assigns, seen) | templates(node.orelse, assigns, seen)
    if isinstance(node, ast.Name) and node.id not in seen:
        earlier = [value for value in assigns.get(node.id, []) if value.lineno < node.lineno]
        if earlier:
            return templates(max(earlier, key=lambda value: value.lineno), assigns, seen | {node.id})
    if isinstance(node, ast.JoinedStr):
        out = {""}
        for part in node.values:
            if isinstance(part, ast.Constant):
                pieces = {part.value}
            elif isinstance(part.value, ast.Name):
                pieces = templates(part.value, assigns, seen)
            else:
                pieces = {"{}"}
            out = {a + b for a in out for b in pieces}
        return out
    return {"{}"}


def scan_code() -> tuple[dict[str, tuple[set, set]], set[tuple[str, str]]]:
    transitions: dict[str, tuple[set, set]] = {}
    reasons: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE.glob("*.py")):
        for fn in ast.parse(path.read_text()).body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            where = f"{path.stem}.{fn.name}"
            assigns: dict[str, list] = {}
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    assigns.setdefault(node.targets[0].id, []).append(node.value)

            stack: list[tuple[ast.AST, bool]] = [(fn, False)]
            while stack:
                node, create = stack.pop()
                seq = node.elts if isinstance(node, (ast.List, ast.Tuple)) else node.args if isinstance(node, ast.Call) else None
                if seq is not None:
                    # `--label` on `gh ... create` labels a new issue/PR; elsewhere it filters a query.
                    create = create or any(isinstance(e, ast.Constant) and e.value == "create" for e in seq)
                    for i, element in enumerate(seq):
                        if isinstance(element, ast.Constant) and element.value in FLAGS and (element.value != "--label" or create):
                            following = seq[i + 1] if i + 1 < len(seq) else None
                            removes, adds = transitions.setdefault(where, (set(), set()))
                            (adds if FLAGS[element.value] == "add" else removes).add(label_of(following))
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    found: set[str] = set()
                    if name in ESCALATORS and len(node.args) > ESCALATORS[name]:
                        found = templates(node.args[ESCALATORS[name]], assigns)
                    elif name == "record" and node.args and getattr(node.args[0], "value", None) == "escalate":
                        found = set().union(*(templates(k.value, assigns) for k in node.keywords if k.arg == "reason"))
                    # A bare pass-through (`{}`) is the caller's reason, listed at the caller.
                    reasons.update((where, t) for t in found if t.replace("{}", "").strip())
                stack.extend((child, create) for child in ast.iter_child_nodes(node))
    return transitions, reasons


def section_rows(text: str, heading: str) -> list[list[str]]:
    body = text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = [line.strip().strip("|").split("|") for line in body.splitlines() if line.startswith("| `")]
    return [[cell.strip() for cell in row] for row in rows]


def code_spans(cell: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", cell))


def scan_map() -> tuple[dict[str, tuple[set, set]], set[tuple[str, str]]]:
    text = MAP.read_text()
    transitions = {}
    for row in section_rows(text, "Label transitions"):
        (where,) = code_spans(row[0])
        transitions[where] = (code_spans(row[1]), code_spans(row[2]))
    reasons = set()
    for row in section_rows(text, "Escalate reasons"):
        (where,) = code_spans(row[0])
        reasons.add((where, re.fullmatch(r"``(.+)``", row[1]).group(1)))
    return transitions, reasons


class TicketFlowMapTest(unittest.TestCase):
    def setUp(self):
        self.code_transitions, self.code_reasons = scan_code()
        self.map_transitions, self.map_reasons = scan_map()

    def test_label_transitions_match_code(self):
        for where, (removes, adds) in self.code_transitions.items():
            self.assertIn(where, self.map_transitions, f"{where} edits labels; add it to the map")
            self.assertEqual((removes, adds), self.map_transitions[where], f"{where}: (removes, adds)")
        for where in self.map_transitions.keys() - self.code_transitions.keys():
            self.fail(f"map names a label transition in {where} that the code does not have")

    def test_escalate_reasons_match_code(self):
        self.assertEqual(set(), self.code_reasons - self.map_reasons, "escalate reasons missing from the map")
        self.assertEqual(set(), self.map_reasons - self.code_reasons, "map names escalate reasons the code does not have")

    def test_every_cited_symbol_exists(self):
        defined: dict[str, set[str]] = {}
        for path in PACKAGE.glob("*.py"):
            names = set()
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Assign):
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            defined[path.stem] = names
        modules = "|".join(sorted(defined))
        cited = set(re.findall(rf"\b({modules})\.(\w+)\b", MAP.read_text()))
        self.assertTrue(cited, "the map cites no factory symbols")
        missing = {f"{m}.{s}" for m, s in cited if s not in {"py", "md", "html"} and s not in defined[m]}
        self.assertEqual(set(), missing, "map cites symbols that do not exist in factory/")

    def test_extractor_sees_known_transitions(self):
        # Guards the extractor itself: if it silently found nothing, both directions would pass.
        self.assertEqual(({config.LABEL_AGENT}, {config.LABEL_HUMAN}), self.code_transitions["dispatch.escalate"])
        self.assertIn(("dispatch.process_ticket", "worker edited protected paths"), self.code_reasons)


if __name__ == "__main__":
    unittest.main()
