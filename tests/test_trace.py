"""Structured decision records and the read-only ticket explanation graph."""
from __future__ import annotations

import argparse
import contextlib
import gzip
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import cli, config, dispatch, lifecycle, trace, triage

ACTOR = {"kind": "deterministic", "name": "test", "model": None, "version": "1"}


def decision(ticket: int, stage: str, chosen: str, at: str, **kwargs) -> dict:
    return {
        "event": "decision",
        **trace.decision(
            ticket=ticket,
            stage=stage,
            inputs=kwargs.pop("inputs", [{"kind": "fixture", "ref": ".factory/events.jsonl", "summary": "fixture"}]),
            options=kwargs.pop("options", [{"option": chosen, "eligible": True, "reason": "selected"}]),
            chosen=chosen,
            rationale=kwargs.pop("rationale", "selected"),
            causes=kwargs.pop("causes", []),
            cost=kwargs.pop("cost", None),
            actor=kwargs.pop("actor", ACTOR),
            at=at,
            **kwargs,
        ),
    }


class TraceSchemaTest(unittest.TestCase):
    def test_builder_scrubs_and_validator_accepts_the_documented_schema(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz"
        row = decision(
            7,
            "triage",
            "ready-for-agent",
            "2026-10-23T10:00:00Z",
            rationale=f"model chose it with {token}",
            inputs=[{"kind": "issue", "ref": "https://github.com/acme/widgets/issues/7", "summary": token}],
            cost={"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 4, "seconds": 0.5},
            actor={"kind": "model", "name": "triage", "model": "local", "version": None},
        )
        self.assertIs(trace.validate(row), row)
        self.assertEqual(
            set(row) - {"event"},
            {"schema_version", "decision_id", "ticket", "stage", "at", "inputs", "options", "chosen",
             "rationale", "causes", "cost", "actor"},
        )
        self.assertNotIn(token, json.dumps(row))
        self.assertIn("credential withheld", json.dumps(row))

    def test_explain_builds_decision_and_cross_ticket_edges(self):
        triage_row = decision(7, "triage", "ready-for-agent", "2026-10-23T10:00:00Z")
        admission = decision(7, "admission", "ticket:7", "2026-10-23T10:01:00Z")
        worker = decision(7, "worker_select", "default", "2026-10-23T10:02:00Z")
        claim = decision(
            7,
            "claim",
            "claimed",
            "2026-10-23T10:03:00Z",
            causes=[{"decision_id": admission["decision_id"], "relation": "follows"},
                    {"ticket": 3, "relation": "split_from"}],
        )
        graph = trace.explain([triage_row, admission, worker, claim], 7)
        follows = {(edge["from"], edge["to"], edge["relation"]) for edge in graph["edges"]}
        self.assertIn((triage_row["decision_id"], admission["decision_id"], "follows"), follows)
        self.assertIn((admission["decision_id"], worker["decision_id"], "follows"), follows)
        self.assertIn((worker["decision_id"], claim["decision_id"], "follows"), follows)
        self.assertIn(("ticket:3", claim["decision_id"], "split_from"), follows)
        self.assertEqual(graph["coverage"], ["binding"])
        self.assertEqual(graph["frontier"], claim["decision_id"])

    def test_legacy_rows_are_synthesized_and_report_missing_coverage(self):
        rows = [
            {"event": "claimed", "ticket": 8, "at": "2026-10-23T10:00:00Z", "worker": "default"},
            {"event": "attempt", "ticket": 8, "at": "2026-10-23T10:01:00Z", "attempt": 1, "gate": "PASS"},
            {"event": "merged", "ticket": 8, "at": "2026-10-23T10:02:00Z", "pr": 10},
        ]
        graph = trace.explain(rows, 8)
        self.assertTrue(all(node["synthesized"] for node in graph["nodes"]))
        self.assertEqual(graph["coverage"], list(trace.STAGES))
        self.assertIsNone(graph["frontier"])

    def test_cli_reads_rotated_and_live_rows_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            cfg.factory.mkdir()
            archived = decision(7, "triage", "ready-for-agent", "2026-10-23T10:00:00Z")
            live = decision(7, "admission", "ticket:7", "2026-10-23T10:01:00Z")
            with gzip.open(cfg.factory / "events.jsonl.1.gz", "wt") as segment:
                segment.write(json.dumps(archived) + "\n")
            lifecycle.append(cfg.factory / "events.jsonl", live)
            output = io.StringIO()
            with mock.patch.object(trace.config, "load", return_value=cfg), contextlib.redirect_stdout(output):
                self.assertEqual(trace.main(["7", "--json", "--since", "2026-10-23T09:00:00Z"]), 0)
        graph = json.loads(output.getvalue())
        self.assertEqual([node["stage"] for node in graph["nodes"]], ["triage", "admission"])
        self.assertEqual(cli.COMMANDS["explain"][:2], ("trace", "main"))


class TraceEmissionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.root = Path(temp.name)
        self.cfg = config.Config(root=self.root, repo="acme/widgets", max_active=1,
                                 workers={"gpu": ["gpu-worker"], "default": ["worker"]},
                                 worker_select_label={"gpu": "needs-gpu"},
                                 worker_version={"gpu": "2.0", "default": "1.0"})
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        dispatch.configure(self.cfg)
        triage.configure(self.cfg)

    def decisions(self, stage: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events)
                if row.get("event") == "decision" and row.get("stage") == stage]

    def test_fake_model_triage_pass_writes_one_decision_with_all_routes(self):
        issue = {"number": 7, "title": "Fix parser", "body": "Scope: reject empty parser input without changing valid parsing behavior. Acceptance criteria: add a regression test and verify with python -m unittest tests.test_parser.",
                 "comments": [], "labels": [{"name": config.LABEL_TRIAGE}], "state": "OPEN"}

        def gh(*args, execution=None):
            if args[:2] == ("issue", "view"):
                return json.dumps(issue)
            return ""

        reply = json.dumps({"decision": config.LABEL_AGENT, "rationale": "complete", "question": "", "brief": "run tests"})
        args = argparse.Namespace(issue=7, replay=None, dry_run=False)
        with mock.patch.object(triage, "gh", gh), mock.patch.object(triage, "call_llm", return_value=reply), \
                lifecycle.scope(self.events, "triage", lazy=True) as execution:
            self.assertEqual(triage.execute(args, execution), 0)
        [row] = self.decisions("triage")
        self.assertEqual(row["chosen"], config.LABEL_AGENT)
        self.assertEqual({option["option"] for option in row["options"]}, set(triage.DECISIONS))
        self.assertTrue(all(option["reason"] for option in row["options"] if option["option"] != row["chosen"]))

    def test_schedule_capacity_one_writes_one_admission_decision(self):
        tickets = [
            {"number": n, "title": f"t{n}", "body": "", "createdAt": f"2026-10-0{n}T00:00:00Z",
             "labels": [{"name": config.LABEL_AGENT}], "assignees": []}
            for n in (1, 2, 3)
        ]
        with mock.patch.object(dispatch, "frontier", return_value=tickets), \
                mock.patch.object(dispatch, "process_ticket") as process:
            dispatch.ticket_pass(1, False)
        [row] = self.decisions("admission")
        self.assertEqual(row["chosen"], "ticket:1")
        self.assertEqual(len(row["options"]), 3)
        self.assertTrue(all(option["reason"] for option in row["options"] if option["option"] != row["chosen"]))
        process.assert_called_once()

    def test_direct_admission_and_two_profile_worker_selection_each_write_once(self):
        lifecycle.append(self.events, {"event": "scheduling", "at": "2026-10-23T09:00:00Z",
                                       "explained": [{"ticket": 9, "decision": "selected", "reason": "highest priority"}]})
        issue = {"number": 7, "title": "GPU work", "body": "(split from #3)",
                 "labels": [{"name": "needs-gpu"}], "assignees": [], "state": "OPEN"}
        with lifecycle.scope(self.events, "ticket", ticket=7):
            admission_id = dispatch.record_direct_admission(issue)
            dispatch.record_worker_selection(7, {"needs-gpu"}, cause=admission_id)
        [admission] = self.decisions("admission")
        [worker] = self.decisions("worker_select")
        self.assertEqual(admission["chosen"], "operator_direct")
        self.assertIn("ticket:9", {option["option"] for option in admission["options"]})
        self.assertEqual(admission["causes"], [{"ticket": 3, "relation": "split_from"}])
        self.assertEqual(worker["chosen"], "gpu")
        self.assertEqual({option["option"] for option in worker["options"]}, {"gpu", "default"})
        self.assertIn("worker version 2.0", " ".join(value["summary"] for value in worker["inputs"]))

    def test_binding_and_claim_records_are_valid_and_claim_follows_admission(self):
        with lifecycle.scope(self.events, "ticket", ticket=7):
            self.assertIsNone(dispatch.admit_plan(7, {"body": "unbound"}))
            admission_id = dispatch.record_direct_admission({"number": 7, "title": "t", "body": "", "labels": []})
            dispatch.record_claim_decision(7, True, admission_id)
        [binding] = self.decisions("binding")
        [claim] = self.decisions("claim")
        self.assertEqual(binding["chosen"], "unbound")
        self.assertEqual(claim["causes"], [{"decision_id": admission_id, "relation": "follows"}])
        self.assertEqual(claim["chosen"], "claimed")


if __name__ == "__main__":
    unittest.main()
