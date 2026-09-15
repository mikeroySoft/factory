"""Shared roadmap contracts over the existing plan, binding and routing producers."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import binding, config, lifecycle, plan, roadmap
from factory.evidence import EvidenceError

REPO = "example/project"
AT = "2026-09-14T12:00:00Z"


def initiative_body(plan_text: str, *, owner: str = "@alice", links: str = "#7") -> str:
    return f"""**Status**
underway

**Outcome**
A shared result.

**Owner**
{owner}

**Areas**
factory/plan.py and factory/roadmap.py

**Boundaries**
No execution authority.

**Plan**
{plan_text}

**Open decisions**
Which rollout window should the owner choose?

**Success evidence**
Owner-confirmed browser and CLI evidence.

**Implementation links**
{links}
"""


def issue(number: int, body: str, *, labels=(), title="Issue", state="open", assignees=()):
    return {
        "number": number, "title": title, "state": state,
        "body": body, "html_url": f"https://github.com/{REPO}/issues/{number}",
        "updated_at": AT, "labels": [{"name": value} for value in labels],
        "assignees": [{"login": value} for value in assignees],
    }


class RoadmapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root, REPO)
        self.calls = []

    def accepted(self, baseline: dict, ticket_body: str) -> None:
        lifecycle.append(self.cfg.factory / "events.jsonl", {
            "at": AT, "event": "plan-bound", "ticket": 7, "schema_version": 1,
            "baseline": baseline,
            "issue": {"title": "Assigned slice", "body": ticket_body, "comments": []},
        })

    def reader(self, responses: dict):
        def read(endpoint, deadline):
            self.calls.append(endpoint)
            value = responses.get(endpoint)
            if isinstance(value, Exception):
                raise value
            if value is None:
                raise EvidenceError("github_not_found", "missing", endpoint)
            return value, False
        return read

    def collect(self, responses: dict, number=None, runtime=None):
        read = self.reader(responses)
        if runtime is None:
            runtime = {"executions": [], "resources": [], "events": [],
                       "history": {"complete": True}, "errors": []}
        with patch("factory.plan.github_read", side_effect=read), \
             patch("factory.binding.github_read", side_effect=read), \
             patch("factory.roadmap.runtime_events.project", return_value=runtime):
            return roadmap.collect(self.cfg, number, deadline=time.monotonic() + 30)

    def test_full_revision_detects_suffix_drift_and_routes_attention_without_filtering_plan(self):
        prefix = "x" * (plan.SECTION_CAP + 1)
        accepted_issue = issue(50, initiative_body(prefix + "A"), labels=("initiative",), title="Shared roadmap")
        live_issue = issue(50, initiative_body(prefix + "B"), labels=("initiative",), title="Shared roadmap")
        baseline = binding.from_issue(self.cfg, 50, accepted_issue)
        ticket_body = (
            "Blocked by: #9\n\n" + binding.render(baseline)
            + "\n\n**Decision owner**\n@bob\n"
        )
        self.accepted(baseline, ticket_body)
        child = issue(7, ticket_body, labels=("ready-for-agent",), title="Assigned slice", assignees=("bob",))
        blocker = issue(9, "**Scope**\nPrerequisite", labels=("ready-for-agent",), title="Prerequisite")
        list_path = f"repos/{REPO}/issues?labels=initiative&state=all&sort=updated&direction=desc&per_page={plan.PAGE_SIZE}&page=1"
        responses = {
            list_path: [live_issue],
            f"repos/{REPO}/issues/50": live_issue,
            f"repos/{REPO}/issues/7": child,
            f"repos/{REPO}/issues/7/dependencies/blocked_by": EvidenceError("github_not_found", "unsupported"),
            f"repos/{REPO}/issues/9": blocker,
        }

        report = self.collect(responses)
        self.assertEqual(report["scope"], {"repository": REPO})
        self.assertEqual(len(report["plans"]), 1)
        shared = report["plans"][0]
        self.assertEqual(shared["revision"]["sha256"], binding.from_issue(self.cfg, 50, live_issue)["sha256"])
        self.assertNotEqual(shared["revision"]["sha256"], baseline["sha256"])
        self.assertEqual(len(shared["sections"]["Plan"]), plan.SECTION_CAP)
        self.assertEqual(shared["children"][0]["runnable"], False)
        self.assertIn("human takeover", shared["children"][0]["readiness_reason"])
        self.assertEqual([(row["number"], row["state"]) for row in shared["blockers"]], [(9, "OPEN")])
        self.assertEqual([(row["number"], row["state"]) for row in shared["children"][0]["blockers"]], [(9, "OPEN")])
        self.assertEqual(shared["drift"][0]["status"], "changed")
        self.assertNotIn("sections", shared["drift"][0]["baseline"])
        self.assertEqual(shared["delivery"]["status"], "unknown")
        by_kind = {row["kind"]: row for row in report["attention"]}
        self.assertEqual(by_kind["open_decisions"]["route"]["owner"], "alice")
        self.assertEqual(by_kind["human_takeover"]["route"]["owner"], "bob")
        self.assertEqual(by_kind["plan_drift"]["route"]["owner"], "bob")
        self.assertTrue(by_kind["open_decisions"]["sources"])
        self.assertEqual(shared["number"], 50)  # owner filtering is consumer-only

        detail = self.collect(responses, 50)
        drift = detail["plans"][0]["drift"][0]
        self.assertTrue(drift["baseline"]["sections"]["Plan"].endswith("A"))
        self.assertTrue(drift["observed"]["sections"]["Plan"].endswith("B"))

    def test_initiative_binding_routes_requirements_to_declared_parent_owner(self):
        parent = issue(50, initiative_body("Plan", links="#7"), labels=("initiative",), title="Parent")
        baseline = binding.from_issue(self.cfg, 50, parent)
        child = issue(7, binding.render(baseline), labels=("ready-for-agent",), title="Bound child")
        responses = {
            f"repos/{REPO}/issues/7": child,
            f"repos/{REPO}/issues/50": parent,
        }
        scratch = {"ok": True, "coverage": {"status": "bounded", "notices": []},
                   "sources": [], "errors": [], "route": None}
        read = self.reader(responses)
        with patch("factory.plan.github_read", side_effect=read):
            plan.Reader(self.cfg, scratch).route(7, "requirements", [])
        self.assertEqual((scratch["route"]["owner"], scratch["route"]["source"]), ("alice", "initiative"))

    def test_reader_refuses_a_truncated_complete_issue_read(self):
        parent = issue(50, initiative_body("Plan"), labels=("initiative",), title="Parent")
        scratch = {"ok": True, "coverage": {"status": "bounded", "notices": []},
                   "sources": [], "errors": [], "plan": None}
        with patch("factory.plan.github_read", return_value=(parent, True)), \
             self.assertRaises(EvidenceError) as caught:
            plan.Reader(self.cfg, scratch).inspect(50)
        self.assertEqual(caught.exception.code, "incomplete_source")
        self.assertTrue(scratch["sources"][0]["truncated"])

    def test_retained_accepted_plan_survives_live_failure_and_unbound_is_not_unchanged(self):
        parent = issue(50, initiative_body("Accepted plan " + "x" * plan.SECTION_CAP), labels=("initiative",), title="Parent")
        baseline = binding.from_issue(self.cfg, 50, parent)
        ticket_body = binding.render(baseline)
        self.accepted(baseline, ticket_body)
        child = issue(7, ticket_body, labels=("ready-for-agent",), title="Bound child")
        unavailable = EvidenceError("github_forbidden", "forbidden")
        responses = {
            f"repos/{REPO}/issues/50": unavailable,
            f"repos/{REPO}/issues/7": child,
            f"repos/{REPO}/issues/7/dependencies/blocked_by": EvidenceError("github_not_found", "unsupported"),
        }
        report = self.collect(responses, 50)
        self.assertFalse(report["ok"])
        self.assertEqual(report["coverage"]["status"], "partial")
        self.assertEqual(report["plans"][0]["revision"]["sha256"], baseline["sha256"])
        self.assertEqual(report["plans"][0]["drift"][0]["status"], "unavailable")
        self.assertEqual(report["plans"][0]["drift"][0]["baseline"]["sections"], baseline["sections"])
        retained = report["plans"][0]
        self.assertTrue(retained["historical"])
        self.assertEqual(len(retained["sections"]["Plan"]), plan.SECTION_CAP)
        self.assertTrue(any("truncated" in problem and "Plan" in problem for problem in retained["problems"]))

        self.cfg.factory.joinpath("events.jsonl").unlink()
        responses[f"repos/{REPO}/issues/50"] = parent
        list_path = f"repos/{REPO}/issues?labels=initiative&state=all&sort=updated&direction=desc&per_page={plan.PAGE_SIZE}&page=1"
        responses[list_path] = [parent]
        report = self.collect(responses)
        drift = report["plans"][0]["drift"][0]
        self.assertEqual(drift["status"], "unknown")
        self.assertIn("not unchanged", drift["reason"])

    def test_unknown_execution_is_not_asserted_as_human_takeover(self):
        parent = issue(50, initiative_body("Plan"), labels=("initiative",))
        baseline = binding.from_issue(self.cfg, 50, parent)
        body = binding.render(baseline) + "\n\n**Decision owner**\n@bob"
        self.accepted(baseline, body)
        responses = {
            f"repos/{REPO}/issues/50": parent,
            f"repos/{REPO}/issues/7": issue(7, body, labels=("ready-for-agent",), assignees=("bob",)),
            f"repos/{REPO}/issues/7/dependencies/blocked_by": [],
        }
        report = self.collect(responses, 50, runtime={
            "executions": [{"ticket": 7, "state": "unknown"}],
            "history": {"complete": True}, "errors": [],
        })
        self.assertIsNone(report["plans"][0]["children"][0]["runnable"])
        questions = [row for row in report["attention"] if row["ticket"] == 7]
        self.assertTrue(any(row["kind"] == "execution_unknown" for row in questions))
        self.assertFalse(any(row["kind"] == "human_takeover" for row in questions))

    def test_factory_claim_receipt_is_not_human_takeover_until_escalated(self):
        parent = issue(50, initiative_body("Plan"), labels=("initiative",))
        baseline = binding.from_issue(self.cfg, 50, parent)
        body = binding.render(baseline) + "\n\n**Decision owner**\n@bob"
        self.accepted(baseline, body)
        journal = self.cfg.factory / "events.jsonl"
        lifecycle.append(journal, {"at": AT, "event": "claimed", "ticket": 7})
        lifecycle.append(journal, {"at": AT, "event": "pr-opened", "ticket": 7, "pr": 12})
        responses = {
            f"repos/{REPO}/issues/50": parent,
            f"repos/{REPO}/issues/7": issue(7, body, labels=("ready-for-agent",), assignees=("factory-bot",)),
            f"repos/{REPO}/issues/7/dependencies/blocked_by": [],
        }
        report = self.collect(responses, 50)
        child = report["plans"][0]["children"][0]
        self.assertIsNone(child["runnable"])
        self.assertIn("PR #12", child["readiness_reason"])
        self.assertIn("not a human takeover", child["readiness_reason"])
        self.assertFalse([row for row in report["attention"] if row["kind"] in ("human_takeover", "execution_unknown")])

        lifecycle.append(journal, {"at": AT, "event": "escalate", "ticket": 7, "reason": "review bounced", "round": 1})
        responses[f"repos/{REPO}/issues/7"] = issue(7, body, labels=("needs-human",), assignees=("bob",))
        report = self.collect(responses, 50)
        self.assertIs(report["plans"][0]["children"][0]["runnable"], False)
        kinds = {row["kind"] for row in report["attention"] if row["ticket"] == 7}
        self.assertIn("human_takeover", kinds)

    def test_invalid_linked_baseline_is_not_reported_runnable(self):
        parent = issue(50, initiative_body("Current plan"), labels=("initiative",), title="Parent")
        child = issue(7, "Initiative: #50", labels=("ready-for-agent",), title="Invalid child")
        list_path = f"repos/{REPO}/issues?labels=initiative&state=all&sort=updated&direction=desc&per_page={plan.PAGE_SIZE}&page=1"
        responses = {
            list_path: [parent],
            f"repos/{REPO}/issues/50": parent,
            f"repos/{REPO}/issues/7": child,
            f"repos/{REPO}/issues/7/dependencies/blocked_by": EvidenceError("github_not_found", "unsupported"),
        }
        report = self.collect(responses)
        linked = report["plans"][0]["children"][0]
        self.assertIsNone(linked["runnable"])
        self.assertIn("no Plan baseline", linked["readiness_reason"])
        self.assertEqual(report["plans"][0]["drift"][0]["status"], "unknown")

    def test_historical_gap_is_retained_beside_a_healthy_live_plan(self):
        historical = issue(50, initiative_body("Historical plan"), labels=("initiative",), title="Historical")
        baseline = binding.from_issue(self.cfg, 50, historical)
        ticket_body = binding.render(baseline)
        self.accepted(baseline, ticket_body)
        live = issue(60, initiative_body("Live plan", links=""), labels=("initiative",), title="Live")
        child = issue(7, ticket_body, labels=("ready-for-agent",), title="Historical child")
        list_path = f"repos/{REPO}/issues?labels=initiative&state=all&sort=updated&direction=desc&per_page={plan.PAGE_SIZE}&page=1"
        responses = {
            list_path: [live],
            f"repos/{REPO}/issues/60": live,
            f"repos/{REPO}/issues/7": child,
            f"repos/{REPO}/issues/7/dependencies/blocked_by": EvidenceError("github_not_found", "unsupported"),
        }
        report = self.collect(responses)
        self.assertEqual({row["number"] for row in report["plans"]}, {50, 60})
        retained = next(row for row in report["plans"] if row["number"] == 50)
        self.assertEqual(retained["revision"]["sha256"], baseline["sha256"])
        self.assertEqual(retained["state"], "UNKNOWN")
        responses[f"repos/{REPO}/issues/50"] = issue(
            50, initiative_body("Revised live plan"), labels=("initiative",), title="Recovered",
        )
        recovered = next(row for row in self.collect(responses)["plans"] if row["number"] == 50)
        self.assertEqual(recovered["state"], "OPEN")
        self.assertEqual(recovered["sections"]["Plan"], "Revised live plan")
        self.assertEqual(recovered["drift"][0]["status"], "changed")

    def test_failed_list_is_unavailable_not_a_successful_empty_roadmap(self):
        list_path = f"repos/{REPO}/issues?labels=initiative&state=all&sort=updated&direction=desc&per_page={plan.PAGE_SIZE}&page=1"
        report = self.collect({list_path: EvidenceError("github_unavailable", "offline")})
        self.assertFalse(report["ok"])
        self.assertEqual(report["coverage"]["status"], "unavailable")
        self.assertEqual(report["plans"], [])
        self.assertEqual(report["errors"][0]["code"], "github_unavailable")


if __name__ == "__main__":
    unittest.main()
