"""Decision breadboard graph layout behavior against the packaged browser asset."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "factory" / "workspace-trace.js"


@unittest.skipUnless(shutil.which("node"), "Node is required for trace graph tests")
class WorkspaceTraceGraphTest(unittest.TestCase):
    def evaluate(self, expression: str, value: dict) -> object:
        script = f"""
const fs = require('fs');
global.window = {{}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const value = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify({expression}));
"""
        result = subprocess.run(
            ["node", "-e", script, str(ASSET)],
            input=json.dumps(value),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_relation_types_choose_distinct_classes_colours_and_dashes(self) -> None:
        relations = [
            "follows",
            "retries",
            "blocked_by",
            "split_from",
            "unblocked_by",
            "overrides",
        ]

        types = self.evaluate(
            "value.relations.map(window.WorkspaceTrace.edgeType)",
            {"relations": relations},
        )

        self.assertEqual(
            types,
            [
                {"className": "relation-follows", "color": "var(--info)", "dash": "none"},
                {"className": "relation-retries", "color": "var(--warn)", "dash": "8 5"},
                {"className": "relation-blocked-by", "color": "var(--bad)", "dash": "3 4"},
                {"className": "relation-split-from", "color": "var(--accent)", "dash": "10 4"},
                {"className": "relation-unblocked-by", "color": "var(--good)", "dash": "10 3 2 3"},
                {"className": "relation-overrides", "color": "var(--muted)", "dash": "2 3"},
            ],
        )

    def test_only_edges_spanning_multiple_columns_join_bundles(self) -> None:
        graph = {
            "nodes": [
                {"decision_id": "a", "stage": "ticket"},
                {"decision_id": "b", "stage": "triage"},
                {"decision_id": "c", "stage": "binding"},
                {"decision_id": "d", "stage": "admission"},
            ],
            "edges": [
                {"from": "a", "to": "b", "relation": "follows"},
                {"from": "a", "to": "c", "relation": "split_from"},
                {"from": "a", "to": "d", "relation": "blocked_by"},
                {"from": "c", "to": "a", "relation": "retries"},
            ],
        }

        bundled = self.evaluate(
            "window.WorkspaceTrace.bundleEdges(value.nodes, value.edges)", graph
        )

        self.assertEqual(bundled, [1, 2, 3])

    def test_barycenter_order_is_deterministic_and_reduces_crossings(self) -> None:
        graph = {
            "nodes": [
                {"decision_id": "a1", "stage": "triage"},
                {"decision_id": "a2", "stage": "triage"},
                {"decision_id": "b1", "stage": "binding"},
                {"decision_id": "b2", "stage": "binding"},
            ],
            "edges": [
                {"from": "a1", "to": "b2", "relation": "follows"},
                {"from": "a2", "to": "b1", "relation": "follows"},
            ],
        }
        result = self.evaluate(
            "(() => { const ordered = window.WorkspaceTrace.orderNodes(value.nodes, value.edges); "
            "return { first: ordered.map(node => node.decision_id), "
            "second: window.WorkspaceTrace.orderNodes(value.nodes, value.edges).map(node => node.decision_id), "
            "before: window.WorkspaceTrace.crossingCount(value.nodes, value.edges), "
            "after: window.WorkspaceTrace.crossingCount(ordered, value.edges) }; })()",
            graph,
        )

        self.assertEqual(result["first"], result["second"])
        self.assertEqual(result["first"], ["a1", "a2", "b2", "b1"])
        self.assertLess(result["after"], result["before"])

    def test_large_bundle_stripes_stay_inside_sixteen_pixel_gutter(self) -> None:
        result = self.evaluate(
            "(() => { const groups = value.sizes.map(size => Array(size).fill(0)); "
            "const spacing = window.WorkspaceTrace.bundleSpacing(groups); "
            "return { stripeOffset: (Math.max(...value.sizes) - 1) / 2 * spacing.stripe, "
            "trackWidth: value.sizes.reduce((total, size) => total + size, 0) * spacing.track }; })()",
            {"sizes": [34, 10]},
        )

        self.assertLessEqual(result["stripeOffset"], 14)
        self.assertLessEqual(result["trackWidth"], 44)
