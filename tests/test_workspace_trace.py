"""Rendered decision trace smoke checks against the packaged browser asset."""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from factory import trace

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "factory" / "workspace-trace.js"
ACTOR = {"kind": "deterministic", "name": "test", "model": None, "version": "1"}


def decision(stage: str, chosen: str, at: str, **kwargs: object) -> dict:
    return {
        "event": "decision",
        **trace.decision(
            ticket=7,
            stage=stage,
            inputs=kwargs.pop(
                "inputs",
                [{"kind": "fixture", "ref": ".factory/events.jsonl", "summary": "fixture"}],
            ),
            options=kwargs.pop(
                "options", [{"option": chosen, "eligible": True, "reason": "selected"}]
            ),
            chosen=chosen,
            rationale="selected",
            causes=kwargs.pop("causes", []),
            cost=kwargs.pop("cost", None),
            actor=ACTOR,
            at=at,
            **kwargs,
        ),
    }


def cost(value: int) -> dict:
    return {"prompt_tokens": value, "seconds": value}


@unittest.skipUnless(shutil.which("node"), "Node is required for the browser asset smoke test")
class WorkspaceTraceRenderTest(unittest.TestCase):
    def render(self, graph: dict) -> str:
        script = """
const fs = require('fs');
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const graph = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(window.WorkspaceTrace.markup(graph, {
  ticket: graph.ticket,
  repository: 'acme/widgets',
  recentTickets: [7, 8]
}));
"""
        result = subprocess.run(
            ["node", "-e", script, str(ASSET)],
            input=json.dumps(graph),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_two_round_trace_renders_nodes_loop_options_and_terminal(self) -> None:
        first = decision("worker_select", "default", "2026-10-23T10:00:00Z", cost=cost(1))
        gate_one = decision("gate", "pass", "2026-10-23T10:01:00Z", cost=cost(2))
        revise = decision("review", "REVISE", "2026-10-23T10:02:00Z", cost=cost(3))
        retry = decision(
            "worker_select",
            "default",
            "2026-10-23T10:03:00Z",
            cost=cost(4),
            causes=[{"decision_id": revise["decision_id"], "relation": "retries"}],
        )
        gate_two = decision("gate", "pass", "2026-10-23T10:04:00Z", cost=cost(5))
        approve = decision("review", "APPROVE", "2026-10-23T10:05:00Z", cost=cost(6))
        refresh = decision("merge", "refresh branch", "2026-10-23T10:06:00Z")
        reapprove = decision("review", "APPROVE", "2026-10-23T10:07:00Z")
        merged = decision(
            "merge",
            "merge now",
            "2026-10-23T10:08:00Z",
            options=[
                {"option": "merge now", "eligible": True, "reason": "selected"},
                {"option": "wait for CI", "eligible": False, "reason": "checks passed"},
            ],
        )
        graph = trace.explain(
            [first, gate_one, revise, retry, gate_two, approve, refresh, reapprove, merged], 7
        )

        html = self.render(graph)

        self.assertEqual(html.count('data-trace-node="'), len(graph["nodes"]))
        self.assertEqual(html.count('<details class="trace-loop"'), 1)
        self.assertIn("12 tokens · 12 seconds", html)
        self.assertIn('class="trace-option is-unchosen', html)
        self.assertIn('data-terminal="merged"', html)

    def test_legacy_trace_renders_as_dashed_linear_chain(self) -> None:
        graph = trace.explain(
            [
                {"event": "claimed", "ticket": 8, "at": "2026-10-23T10:00:00Z"},
                {"event": "attempt", "ticket": 8, "at": "2026-10-23T10:01:00Z"},
                {"event": "merged", "ticket": 8, "at": "2026-10-23T10:02:00Z"},
            ],
            8,
        )

        html = self.render(graph)

        self.assertEqual(html.count('data-trace-node="'), 3)
        self.assertEqual(html.count("is-synthesized"), 3)
        self.assertIn('data-terminal="merged"', html)

    def test_full_legacy_trace_keeps_pipeline_order(self) -> None:
        graph = trace.explain(
            [
                {"event": "claimed", "ticket": 8, "at": "2026-10-23T10:00:00Z"},
                {"event": "attempt", "ticket": 8, "at": "2026-10-23T10:01:00Z"},
                {"event": "review", "ticket": 8, "at": "2026-10-23T10:02:00Z"},
                {"event": "approved", "ticket": 8, "at": "2026-10-23T10:03:00Z"},
                {"event": "merged", "ticket": 8, "at": "2026-10-23T10:04:00Z"},
            ],
            8,
        )

        html = self.render(graph)
        order = [
            html.index(f'data-stage="{stage}"')
            for stage in ("claim", "attempt", "review", "approved", "merged")
        ]

        self.assertEqual(order, sorted(order))

    def test_escalated_terminal_shows_recorded_code(self) -> None:
        escalated = decision(
            "escalate",
            "protected_path",
            "2026-10-23T10:00:00Z",
        )
        graph = trace.explain(
            [
                escalated,
                {"event": "escalate", "ticket": 7, "at": "2026-10-23T10:01:00Z"},
            ],
            7,
        )

        html = self.render(graph)

        self.assertIn('data-terminal="escalated"', html)
        self.assertIn("<p>protected_path</p>", html)
