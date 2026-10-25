"""Per-attempt worker plan artifacts, review drift, and trace rendering (#385)."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, trace


ACTOR = {"kind": "deterministic", "name": "test", "model": None, "version": "1"}


def decision(stage: str, chosen: str, at: str, *, actor: dict | None = None) -> dict:
    return {
        "event": "decision",
        **trace.decision(
            ticket=7,
            stage=stage,
            inputs=[{"kind": "fixture", "ref": ".factory/events.jsonl", "summary": "fixture"}],
            options=[{"option": chosen, "eligible": True, "reason": "selected"}],
            chosen=chosen,
            rationale="selected",
            causes=[],
            cost=None,
            actor=actor or ACTOR,
            at=at,
        ),
    }


class WorkerPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()
        self.wt = self.root / "worktree"
        self.wt.mkdir()
        (self.wt / ".factory").mkdir()
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            workers={"default": ["worker"], "chore": ["chore-worker"]},
            reviewer=["reviewer", "{prompt}"],
            reviewer_json_mode=False,
        )
        self.cfg.factory.mkdir()
        (self.cfg.factory / "logs").mkdir()
        dispatch.configure(self.cfg)

    def events(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def test_worker_prompt_requires_short_versioned_plan_but_chore_skips_it(self) -> None:
        issue = {"title": "change", "body": "acceptance", "comments": []}
        with mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(dispatch, "admit_plan", return_value=None), \
                mock.patch.object(dispatch.brief, "ensure", return_value=""):
            prompt = dispatch.build_prompt(7, self.wt, attempt=2, labels=set())
            chore = dispatch.build_prompt(7, self.wt, attempt=2, labels={config.LABEL_CHORE})

        self.assertIn("## Attempt plan", prompt)
        self.assertIn("`.factory/plans/attempt-2/1.md`", prompt)
        self.assertIn("30 lines or fewer", prompt)
        self.assertLess(prompt.index("## Attempt plan"), prompt.index("- Implement exactly"))
        self.assertNotIn("## Attempt plan", chore)

    def test_plan_revisions_are_copied_next_to_transcripts_and_each_journaled(self) -> None:
        source = dispatch.attempt_plan_dir(self.wt, 1)
        source.mkdir(parents=True)
        plans = ["Files: `a.py`\nProof: `python -m unittest`\n", "Files: `a.py`, `b.py`\nProof: `python -m unittest`\n"]
        for version, text in enumerate(plans, 1):
            (source / f"{version}.md").write_text(text)

        dispatch.journal_attempt_plan(self.wt, 7, 1, set(), "default", "1.2.3")

        rows = self.events("plan")
        self.assertEqual([(row["status"], row["version"]) for row in rows], [("recorded", 1), ("recorded", 2)])
        for row, text in zip(rows, plans, strict=True):
            artifact = Path(row["path"])
            self.assertEqual(artifact.parent, self.cfg.factory / "transcripts" / "7")
            self.assertEqual(artifact.read_text(), text)
            self.assertEqual(row["sha256"], hashlib.sha256(text.encode()).hexdigest())
        stages = [row for row in self.events("decision") if row.get("stage") == "plan"]
        self.assertEqual([row["chosen"] for row in stages], ["recorded", "recorded"])
        self.assertTrue(all(row["actor"] == {"kind": "worker", "name": "default", "model": None, "version": "1.2.3"} for row in stages))

    def test_missing_plan_is_recorded_without_blocking_gate_or_second_worker_run(self) -> None:
        def run(command: list[str], **_kwargs: object) -> mock.Mock:
            stdout = "head\n" if command[:3] == ["git", "rev-parse", "HEAD"] else ""
            return mock.Mock(stdout=stdout, returncode=0)

        with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                mock.patch.object(dispatch, "run_worker", return_value=(0, None)) as worker, \
                mock.patch.object(dispatch, "worker_usage", return_value={
                    "model": None, "prompt_tokens": None, "completion_tokens": None,
                    "cached_tokens": None, "cost": None,
                }), mock.patch.object(dispatch, "commit_leftovers"), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")) as gate, \
                mock.patch("factory.results.source_metadata", return_value={}):
            ok, report, _log, _head = dispatch.worker_round(
                7, self.wt, set(), "ticket", "", 1, float("inf")
            )

        self.assertEqual((ok, report), (True, "PASS"))
        worker.assert_called_once()
        gate.assert_called_once()
        [row] = self.events("plan")
        self.assertEqual(
            {key: row[key] for key in ("plan", "status", "path", "sha256", "attempt")},
            {"plan": "missing", "status": "missing", "path": None, "sha256": None, "attempt": 1},
        )

    def test_chore_attempt_journals_skip_without_plan_artifact(self) -> None:
        dispatch.journal_attempt_plan(self.wt, 7, 1, {config.LABEL_CHORE}, "chore", None)

        [row] = self.events("plan")
        self.assertEqual((row["plan"], row["status"], row["path"], row["sha256"]),
                         ("skipped", "skipped", None, None))
        [stage] = [row for row in self.events("decision") if row.get("stage") == "plan"]
        self.assertEqual((stage["chosen"], stage["actor"]["kind"]), ("skipped", "worker"))
        self.assertFalse((self.cfg.factory / "transcripts" / "7").exists())

    def test_review_receives_latest_plan_and_can_report_nonblocking_file_drift(self) -> None:
        source = dispatch.attempt_plan_dir(self.wt, 1)
        source.mkdir(parents=True)
        (source / "1.md").write_text("## Files\n- `planned.py`\n\n## Proof\n- `python -m unittest`\n")
        dispatch.journal_attempt_plan(self.wt, 7, 1, set(), "default", None)
        head = "a" * 40
        diff = "diff --git a/unplanned.py b/unplanned.py\n--- /dev/null\n+++ b/unplanned.py\n@@ -0,0 +1 @@\n+new\n"
        reply = "## Optional suggestions\n- unplanned.py:1 — Plan drift: changed but not named in the worker plan.\n\nVERDICT: APPROVE"
        prompts: list[str] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            if command[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, head + "\n", "")
            if command[:2] == ["git", "diff"]:
                return subprocess.CompletedProcess(command, 0, diff, "")
            raise AssertionError(command)

        def reviewer(command: list[str], *_args: object) -> subprocess.CompletedProcess:
            prompts.append(command[-1])
            return subprocess.CompletedProcess(command, 0, reply, "")

        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "_run_reviewer", side_effect=reviewer):
            verdict, findings = dispatch.review(self.wt, 7, "- test: PASS", head)

        self.assertEqual(verdict, "APPROVE")
        self.assertIn("Plan drift: changed but not named", findings)
        [prompt] = prompts
        self.assertIn("`planned.py`", prompt)
        self.assertIn("files changed that the plan did not name", prompt)
        self.assertIn("planned proof missing from the gate report", prompt)
        self.assertIn("Plan drift alone does not require REVISE", prompt)


class WorkerPlanTraceTest(unittest.TestCase):
    def test_plan_stage_sits_between_claim_and_gate_and_is_the_claim_frontier(self) -> None:
        claim = decision("claim", "claimed", "2026-10-24T10:00:00Z")
        worker = {"kind": "worker", "name": "default", "model": None, "version": None}
        plan = decision("plan", "recorded", "2026-10-24T10:01:00Z", actor=worker)
        gate = decision("gate", "pass", "2026-10-24T10:02:00Z")

        claim_graph = trace.explain([claim], 7)
        graph = trace.explain([claim, plan, gate], 7)

        self.assertEqual(claim_graph["frontier_stage"], "plan")
        self.assertEqual([node["stage"] for node in graph["nodes"]], ["claim", "plan", "gate"])
        self.assertIs(trace.validate(plan), plan)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the Decisions asset smoke test")
    def test_decisions_view_orders_plan_between_claim_and_gate(self) -> None:
        graph = trace.explain([
            decision("claim", "claimed", "2026-10-24T10:00:00Z"),
            decision("plan", "recorded", "2026-10-24T10:01:00Z",
                     actor={"kind": "worker", "name": "default", "model": None, "version": None}),
            decision("gate", "pass", "2026-10-24T10:02:00Z"),
        ], 7)
        asset = Path(__file__).resolve().parents[1] / "factory" / "workspace-trace.js"
        script = """
const fs = require('fs');
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
process.stdout.write(window.WorkspaceTrace.markup(JSON.parse(fs.readFileSync(0, 'utf8'))));
"""
        rendered = subprocess.run(
            ["node", "-e", script, str(asset)], input=json.dumps(graph), capture_output=True,
            text=True, timeout=10, check=True,
        ).stdout

        order = [rendered.index(f'data-stage="{stage}"') for stage in ("claim", "plan", "gate")]
        self.assertEqual(order, sorted(order))
        self.assertIn("worker · default", rendered)


if __name__ == "__main__":
    unittest.main()
