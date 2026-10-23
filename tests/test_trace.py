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

    def test_builder_normalizes_controls_before_scrubbing_secrets(self):
        credential = "Bearer\x01ABCDEFGHIJKLMN"
        row = decision(
            7,
            "triage",
            "ready-for-agent",
            "2026-10-23T10:00:00Z",
            rationale=credential,
            inputs=[{"kind": "issue", "ref": ".factory/events.jsonl", "summary": credential}],
        )
        self.assertIs(trace.validate(row), row)
        self.assertNotIn("ABCDEFGHIJKLMN", json.dumps(row))

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

    def test_worker_round_does_not_repeat_claim_time_worker_selection(self):
        def run(cmd, **_kwargs):
            stdout = "head\n" if cmd[:3] == ["git", "rev-parse", "HEAD"] else ""
            return mock.Mock(stdout=stdout, returncode=0)

        with lifecycle.scope(self.events, "ticket", ticket=7):
            dispatch.record_worker_selection(7, {"needs-gpu"})
            with mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                    mock.patch.object(dispatch, "run_worker", return_value=(0, None)), \
                    mock.patch.object(dispatch, "worker_usage", return_value={
                        "model": None, "prompt_tokens": None, "completion_tokens": None,
                        "cached_tokens": None, "cost": None,
                    }), mock.patch.object(dispatch, "commit_leftovers"), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")), \
                    mock.patch("factory.results.source_metadata", return_value={}):
                dispatch.worker_round(7, self.root, {"needs-gpu"}, "ticket", "", 1, float("inf"))

        self.assertEqual(len(self.decisions("worker_select")), 1)

    def test_worker_prompts_do_not_repeat_claim_time_binding_decision(self):
        issue = {"title": "unbound", "body": "unbound", "comments": []}
        with lifecycle.scope(self.events, "ticket", ticket=7), \
                mock.patch.object(dispatch, "gh_json", return_value=issue), \
                mock.patch.object(dispatch, "resume_context", return_value=None), \
                mock.patch.object(dispatch.brief, "ensure", return_value=None):
            self.assertIsNone(dispatch.admit_plan(7, issue))
            dispatch.build_prompt(7, self.root)
            dispatch.build_prompt(7, self.root)

        self.assertEqual(len(self.decisions("binding")), 1)

    def test_large_admission_record_stays_below_runtime_row_limit(self):
        from factory import runtime_events

        options = [
            {"option": f"ticket:{number}", "eligible": number == 1, "reason": "r" * 256}
            for number in range(1, trace.OPTION_LIMIT + 1)
        ]
        payload = trace.decision(
            ticket=1,
            stage="admission",
            inputs=[{"kind": "capacity", "ref": ".factory/events.jsonl", "summary": "1"}],
            options=options,
            chosen="ticket:1",
            rationale="selected",
            causes=[],
            cost=None,
            actor=ACTOR,
        )
        with lifecycle.scope(self.events, "dispatch"):
            dispatch.record("decision", **payload)

        errors = {error["code"] for error in runtime_events.project(self.events)["errors"]}
        self.assertNotIn("row_limit", errors)
        [row] = self.decisions("admission")
        self.assertIn("ticket:1", {option["option"] for option in row["options"]})

    def test_malformed_binding_writes_refused_decision(self):
        from factory import binding

        body = "Initiative: nope"
        with lifecycle.scope(self.events, "ticket", ticket=7), \
                self.assertRaisesRegex(binding.BindingError, "malformed Initiative declaration"):
            dispatch.admit_plan(7, {"body": body})

        [row] = self.decisions("binding")
        self.assertEqual(row["chosen"], "refused")
        self.assertIn("malformed Initiative declaration", row["rationale"])

    def test_previously_bound_ticket_without_link_explains_unbound_rejection(self):
        from factory import binding

        with lifecycle.scope(self.events, "ticket", ticket=7), \
                mock.patch.object(binding, "accepted", return_value={"sha256": "old"}), \
                self.assertRaisesRegex(binding.BindingError, "missing baseline"):
            dispatch.admit_plan(7, {"body": "Initiative removed"})

        [row] = self.decisions("binding")
        options = {option["option"]: option for option in row["options"]}
        self.assertEqual(row["inputs"][0]["summary"], "no Initiative link")
        self.assertIn("previously bound ticket", options["unbound"]["reason"])



if __name__ == "__main__":
    unittest.main()


class TracePartTwoTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.root = Path(temp.name)
        self.cfg = config.Config(
            root=self.root,
            repo="acme/widgets",
            workers={"gpu": ["gpu-worker"], "default": ["worker"]},
            worker_select_label={"gpu": "needs-gpu"},
        )
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        dispatch.configure(self.cfg)
        triage.configure(self.cfg)

    @staticmethod
    def cost(seconds):
        return {"seconds": seconds}

    def test_two_round_trace_folds_retry_and_sums_costs(self):
        first = decision(7, "worker_select", "default", "2026-10-23T10:00:00Z", cost=self.cost(1))
        gate_one = decision(7, "gate", "pass", "2026-10-23T10:01:00Z", cost=self.cost(2))
        revise = decision(
            7,
            "review",
            "REVISE",
            "2026-10-23T10:02:00Z",
            cost=self.cost(3),
            inputs=[
                {"kind": "finding", "ref": "factory/a.py:4", "summary": "fix A"},
                {"kind": "finding", "ref": "factory/b.py:8", "summary": "fix B"},
            ],
        )
        fixing_worker = decision(
            7,
            "worker_select",
            "default",
            "2026-10-23T10:03:00Z",
            cost=self.cost(4),
            causes=[{"decision_id": revise["decision_id"], "relation": "retries"}],
        )
        fixing_claim = decision(
            7,
            "claim",
            "claimed",
            "2026-10-23T10:03:01Z",
            causes=[{"decision_id": revise["decision_id"], "relation": "retries"}],
        )
        gate_two = decision(7, "gate", "pass", "2026-10-23T10:04:00Z", cost=self.cost(5))
        approve = decision(7, "review", "APPROVE", "2026-10-23T10:05:00Z", cost=self.cost(6))
        refresh = decision(
            7,
            "merge",
            "refresh branch",
            "2026-10-23T10:06:00Z",
            cost=self.cost(1),
            options=[
                {"option": "merge now", "eligible": False, "reason": "approval head is stale"},
                {"option": "refresh branch", "eligible": True, "reason": "head is behind main"},
            ],
        )
        reapprove = decision(7, "review", "APPROVE", "2026-10-23T10:07:00Z", cost=self.cost(2))
        merged = decision(7, "merge", "merge now", "2026-10-23T10:08:00Z", cost=self.cost(3))

        graph = trace.explain(
            [first, gate_one, revise, fixing_worker, fixing_claim, gate_two, approve, refresh, reapprove, merged],
            7,
        )

        self.assertEqual(len(graph["loops"]), 1)
        self.assertEqual(
            graph["loops"][0]["nodes"],
            [revise["decision_id"], fixing_worker["decision_id"], fixing_claim["decision_id"], gate_two["decision_id"]],
        )
        self.assertEqual(graph["loops"][0]["cost"]["seconds"], 12)
        self.assertEqual(graph["cost"]["seconds"], 27)
        retries = {(edge["from"], edge["to"]) for edge in graph["edges"] if edge["relation"] == "retries"}
        self.assertEqual(
            retries,
            {(revise["decision_id"], fixing_worker["decision_id"]),
             (revise["decision_id"], fixing_claim["decision_id"])},
        )
        refresh_options = {option["option"]: option for option in refresh["options"]}
        self.assertFalse(refresh_options["merge now"]["eligible"])
        self.assertTrue(refresh_options["refresh branch"]["eligible"])
        self.assertIsNone(graph["frontier"])

    def test_rejected_label_gated_route_records_ineligible_manager_option(self):
        from factory import manage

        output = 'DECISION: ROUTE\n{"add":["needs-gpu"],"guidance":"Use the GPU worker"}'
        chosen, body, data = manage.parse(output, {"default": ["worker"]})
        self.assertEqual(chosen, "HUMAN")
        manage.record_manager_decision(
            7, chosen, body, data, output, {"default": ["worker"]}, self.root / "packet.md"
        )

        [row] = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "manager"
        ]
        options = {option["option"]: option for option in row["options"]}
        self.assertFalse(options["ROUTE"]["eligible"])
        self.assertIn("worker_select_label", options["ROUTE"]["reason"])

    def test_malformed_route_body_still_records_human_fallback(self):
        from factory import manage

        for route in ('{"add": "gpu"}', '{"add": null}', '{"add": ["gpu"], "remove": "default"}'):
            with self.subTest(route=route):
                output = f"DECISION: ROUTE\n{route}"
                chosen, body, data = manage.parse(output, self.cfg.workers)
                self.assertEqual(chosen, "HUMAN")
                manage.record_manager_decision(
                    7, chosen, body, data, output, self.cfg.workers, self.root / "packet.md"
                )

        self.assertEqual(len([
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "manager"
        ]), 3)

    def test_learn_decision_cites_every_evidence_ticket(self):
        from factory import learn

        with mock.patch.object(learn, "evidence", return_value=([1, 2, 3], "evidence")), \
                mock.patch.object(learn, "propose", return_value=(["Run the gate."], None)):
            learn.run(self.cfg)

        [row] = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "learn"
        ]
        self.assertEqual(
            row["causes"],
            [{"ticket": 1, "relation": "follows"}, {"ticket": 2, "relation": "follows"},
             {"ticket": 3, "relation": "follows"}],
        )

    def test_triage_wontfix_is_terminal(self):
        issue = {"number": 7, "title": "Do not build", "body": "No longer needed"}
        route = {"decision": config.LABEL_WONTFIX, "rationale": "Superseded", "question": "", "brief": ""}
        with mock.patch.object(triage, "gh", return_value=""):
            triage.apply_decision(7, route, False, issue=issue)

        graph = trace.explain(lifecycle.read_events(self.events), 7)
        self.assertEqual(graph["nodes"][-1]["stage"], "wontfix")
        self.assertIsNone(graph["frontier"])

    def test_review_comment_and_merge_sha_are_decision_inputs(self):
        comment_url = "https://github.com/acme/widgets/pull/9#issuecomment-123"
        dispatch._PENDING_REVIEWS[7] = {
            "verdict": "APPROVE",
            "findings": "VERDICT: APPROVE",
            "head": "a" * 40,
            "parsed": True,
            "accepted": True,
            "usage": {"model": "reviewer", "prompt_tokens": 4, "completion_tokens": 1},
            "seconds": 2,
        }
        posted = mock.Mock(returncode=0, stdout=comment_url + "\n", stderr="")
        with mock.patch.object(dispatch, "run", return_value=posted):
            dispatch.pr_comment(7, "VERDICT: APPROVE")
        dispatch.record_merge_decision(
            7, 9, "merge now", "eligible", "a" * 40, "a" * 40, [],
            merge_sha="b" * 40,
        )

        rows = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision"
        ]
        review, merge = rows
        self.assertIn(comment_url, {value["ref"] for value in review["inputs"]})
        self.assertIn("b" * 40, {value["summary"].removeprefix("merge SHA ") for value in merge["inputs"]})

    def test_nonfinal_merge_decision_keeps_frontier_open(self):
        waiting = decision(7, "merge", "ci-infra", "2026-10-23T10:00:00Z")

        graph = trace.explain([waiting], 7)

        self.assertEqual(graph["frontier"], waiting["decision_id"])
        self.assertEqual(graph["frontier_stage"], "merge eligibility")

    def test_merge_record_carries_ci_run_and_stale_head_reason(self):
        reason = "approval head abc is stale because the branch is behind main"
        link = "https://github.com/acme/widgets/actions/runs/123/job/456"
        dispatch.record_merge_decision(
            7, 9, "refresh branch", reason, "a" * 40, "a" * 40,
            [{"name": "test", "bucket": "pass", "link": link}],
        )

        [row] = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "merge"
        ]
        options = {option["option"]: option for option in row["options"]}
        self.assertFalse(options["merge now"]["eligible"])
        self.assertEqual(options["merge now"]["reason"], reason)
        self.assertTrue(options["refresh branch"]["eligible"])
        self.assertIn(link, {value["ref"] for value in row["inputs"]})

    def test_manager_options_follow_parser_context(self):
        from factory import manage

        manage.record_manager_decision(
            7, "APPROVE", "approved", None, "DECISION: APPROVE\napproved", {},
            self.root / "approval.md", approval=True,
        )
        manage.record_manager_decision(
            7, "HUMAN", "needs a human", None, "DECISION: HUMAN\nneeds a human", {},
            self.root / "escalation.md",
        )
        manage.record_manager_decision(
            7, "BUILD", "useful", None, "useful [S1]\nVERDICT: BUILD", {},
            self.root / "viability.md", viability=True,
        )

        rows = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "manager"
        ]
        approval, escalation, viability = (
            {option["option"]: option for option in row["options"]}
            for row in rows
        )
        self.assertFalse(approval["RESOLVE"]["eligible"])
        self.assertFalse(approval["AUTHORIZE"]["eligible"])
        self.assertFalse(escalation["APPROVE"]["eligible"])
        self.assertTrue(all(not viability[option]["eligible"] for option in manage.MANAGER_DECISIONS))
        self.assertTrue(viability["BUILD"]["eligible"])

    def test_curate_records_each_paths_allowlist_verdict(self):
        from factory import learn

        worktree = self.root / "curate"
        worktree.mkdir()

        def run(args, **kwargs):
            if args[1] == "status":
                return mock.Mock(returncode=0, stdout=" M AGENTS.md\0 M .github/workflows/ci.yml\0")
            return mock.Mock(returncode=0, stdout="", stderr="")

        def invoke_edit(cfg, branch, edit, title, body):
            edit(worktree)

        with mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(learn, "chore_pr", side_effect=invoke_edit):
            result = learn.curate(self.cfg, "diff", [7], "notes")

        self.assertTrue(result.startswith("rejected:"))
        [row] = [
            row for row in lifecycle.read_events(self.events)
            if row.get("event") == "decision" and row.get("stage") == "curate"
        ]
        options = {option["option"]: option for option in row["options"]}
        self.assertTrue(options["AGENTS.md"]["eligible"])
        self.assertFalse(options[".github/workflows/ci.yml"]["eligible"])

    def test_waiting_on_existing_runner_outage_does_not_repeat_merge_decision(self):
        pr = {
            "number": 9,
            "headRefName": "agent/7",
            "headRefOid": "a" * 40,
            "baseRefName": "main",
            "isDraft": False,
            "labels": [{"name": dispatch.FACTORY_APPROVED}],
            "reviewDecision": "APPROVED",
        }
        checks = [{"name": "test", "bucket": "infra", "run": 123}]
        with mock.patch.object(dispatch, "gh_json", return_value=[pr]), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "pr_checks", return_value=checks), \
                mock.patch.object(dispatch, "triage_cancelled", return_value=checks), \
                mock.patch.object(dispatch, "runner_unavailable", return_value=False), \
                mock.patch.object(dispatch, "record_merge_decision") as record:
            dispatch.merge_pass_locked(False)

        record.assert_not_called()

    def test_waiting_on_runner_outage_during_final_check_does_not_repeat_merge_decision(self):
        head = "a" * 40
        pr = {
            "number": 9,
            "state": "OPEN",
            "headRefName": "agent/7",
            "headRefOid": head,
            "baseRefName": "main",
            "isDraft": False,
            "labels": [{"name": dispatch.FACTORY_APPROVED}],
            "reviewDecision": "APPROVED",
            "title": "change",
        }
        lifecycle.append(self.events, {
            "event": "approved",
            "ticket": 7,
            "pr": 9,
            "head": head,
            "gate_head": head,
            "review_head": head,
        })
        passing = [{"name": "test", "bucket": "pass"}]
        starved = [{"name": "test", "bucket": "infra", "run": 123}]

        def query(args):
            if args[:2] == ["pr", "list"]:
                return [pr]
            if args[:2] == ["pr", "view"]:
                return pr
            if args[0] == "api":
                return {"behind_by": 0}
            raise AssertionError(args)

        with mock.patch.object(dispatch, "gh_json", side_effect=query), \
                mock.patch.object(dispatch, "initiative_kind", return_value=None), \
                mock.patch.object(dispatch, "pr_checks", side_effect=[passing, starved]), \
                mock.patch.object(dispatch, "triage_cancelled", side_effect=lambda _, rows, *args: rows), \
                mock.patch.object(dispatch, "_head_evidence_matches", return_value=True), \
                mock.patch.object(dispatch, "run", return_value=mock.Mock(returncode=0, stdout="")), \
                mock.patch.object(dispatch, "runner_unavailable", return_value=False), \
                mock.patch.object(dispatch, "record_merge_decision") as record:
            dispatch.merge_pass_locked(False)

        record.assert_not_called()
