"""Deterministic behavior checks for the bounded, pure Flow projection."""
from __future__ import annotations

import copy
import unittest

from factory import evidence, flow

AT = "2026-10-09T12:00:00Z"
LATER = "2026-10-09T12:10:00Z"


def case(number: int, stage: str, **fields) -> dict:
    return {
        "number": number,
        "title": f"Case {number}",
        "stage": stage,
        "url": f"https://example.test/issues/{number}",
        "updated_at": AT,
        **fields,
    }


def execution(execution_id: str, ticket: int | None, *, state: str = "completed",
              entered_at: str | None = AT, ended_at: str | None = LATER,
              attempt: int | None = 1, review_round: int | None = None,
              stage: str = "worker", wait: dict | None = None) -> dict:
    return {
        "dispatcher_run_id": "dispatch-1",
        "root_execution_id": f"root-{execution_id}",
        "execution_id": execution_id,
        "parent_execution_id": None,
        "ticket": ticket,
        "attempt": attempt,
        "review_round": review_round,
        "stage": stage,
        "state": state,
        "entered_at": entered_at,
        "ended_at": ended_at,
        "wait": wait,
        "observed_at": LATER,
    }


def event(source: dict, event_id: str, sequence: int, kind: str, at: str, **fields) -> dict:
    return {
        **{name: source.get(name) for name in flow.IDENTITY},
        "event_id": event_id,
        "sequence": sequence,
        "kind": kind,
        "at": at,
        **fields,
    }


def observation(cases: list[dict], status: str = "bounded") -> dict:
    return {
        "observed_at": "2026-10-09T12:09:00Z",
        "coverage": {"status": status, "notices": []},
        "cases": cases,
    }


def runtime(executions: list[dict], events: list[dict] | None = None, *, complete: bool = True,
            truncated: bool = False, resources: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "generated_at": LATER,
        "executions": executions,
        "events": events or [],
        "resources": resources or [],
        "history": {
            "status": "available" if executions or events else "empty",
            "complete": complete,
            "truncated": truncated,
            "gaps": [] if complete else ["event_limit"],
            "start_at": AT,
            "end_at": LATER,
            "byte_limit": 1024 * 1024,
            "event_limit": 512,
        },
        "errors": [],
    }


class FlowProjectionTest(unittest.TestCase):
    def test_closed_and_merged_leave_flow_without_treating_execution_or_pr_state_as_outcome(self) -> None:
        observed = observation([
            case(1, "queued"),
            case(2, "closed"),
            case(3, "merged"),
            case(4, "pr-open", pr={"number": 14, "state": "CLOSED", "merged_at": None}),
            evidence.case_summary(case(5, "escalated", state="CLOSED", labels=["ready-for-human"])),
        ])
        projected = flow.project(observed, runtime([
            execution("closed-active", 2, state="active", ended_at=None),
            execution("merged-active", 3, state="active", ended_at=None),
            execution("pr-worker-done", 4),
            execution("closed-human-active", 5, state="active", ended_at=None),
        ]))

        self.assertEqual([row["number"] for row in projected["cases"]], [1, 4])
        by_number = {row["number"]: row for row in projected["cases"]}
        self.assertFalse(by_number[1]["known_started_wip"])
        self.assertTrue(by_number[4]["known_started_wip"])
        self.assertFalse(by_number[4]["active_execution"])
        self.assertEqual(projected["counts"]["selected_nonterminal_cases"], {"observed": 2, "total": 2})
        self.assertNotIn(2, {wait["number"] for group in projected["waits"].values() for wait in group})

    def test_cases_are_distinct_and_separate_selection_start_and_active_execution(self) -> None:
        duplicate = execution("run-a", 11, state="active", ended_at=None, attempt=1)
        projected = flow.project(observation([case(10, "queued"), case(11, "in-flight")]), runtime([
            duplicate,
            execution("run-b", 11, state="active", ended_at=None, attempt=1),
            {**duplicate, "attempt": 3},
            execution("missing-start", 12, state="unknown", entered_at=None, ended_at=None),
            execution("runtime-only", 13, state="unknown", ended_at=None, attempt=2),
        ]))

        self.assertEqual([row["number"] for row in projected["cases"]], [10, 11, 13])
        rows = {row["number"]: row for row in projected["cases"]}
        self.assertEqual((rows[10]["selected_nonterminal"], rows[10]["known_started_wip"],
                          rows[10]["active_execution"]), (True, False, False))
        self.assertEqual(rows[11]["execution_ids"], ["run-a", "run-b"])
        self.assertEqual(rows[11]["attempts"]["observed"], [1])
        self.assertFalse(rows[11]["attempts"]["rework_observed"])
        self.assertEqual(rows[13]["selection"], "runtime_only")
        self.assertTrue(rows[13]["attempts"]["rework_observed"])
        self.assertNotIn(12, rows, "a missing enter must not be promoted to known-started work")
        self.assertEqual(projected["counts"]["known_started_wip"]["observed"], 2)
        self.assertEqual(projected["counts"]["active_execution_cases"]["observed"], 1)
        self.assertEqual(projected["counts"]["active_executions"]["observed"], 2)

    def test_waits_require_exact_identity_and_expose_closure_duration_and_sources(self) -> None:
        resource = {
            "id": "resource-1", "scope": "host", "host_id": "host-1", "repository": None,
            "lock": {"path": "/lock", "device": 1, "inode": 2},
        }
        wait_value = {"reason": "exclusive_resource", "mode": "blocking",
                      "resource": resource, "details": {"active": 1, "max_active": 1}}
        done = execution("done", 20)
        active = execution(
            "active", 21, state="active", ended_at=None,
            wait={**wait_value, "event_id": "wait-current", "at": "2026-10-09T12:02:00Z"},
        )
        rotated = execution("rotated", 22, state="unknown", ended_at=None)
        foreign = execution("foreign", 99, state="completed")
        events = [
            event(done, "wait-completed", 2, "wait", "2026-10-09T12:00:00Z", wait=wait_value),
            event(done, "wait-end-completed", 3, "wait_end", "2026-10-09T12:00:30Z",
                  wait_event_id="wait-completed"),
            event(active, "wait-current", 2, "wait", "2026-10-09T12:02:00Z", wait=wait_value),
            event(rotated, "wait-rotated", 2, "wait", "2026-10-09T12:03:00Z", wait=wait_value),
            event(foreign, "wrong-execution-end", 3, "wait_end", "2026-10-09T12:04:00Z",
                  wait_event_id="wait-rotated"),
            event(rotated, "orphan-end", 4, "wait_end", "2026-10-09T12:05:00Z",
                  wait_event_id="missing-wait"),
        ]
        projected = flow.project(
            observation([case(20, "working"), case(21, "working"), case(22, "working")]),
            runtime([done, active, rotated, foreign], events, resources=[{
                "resource": resource, "event_id": "resource-observation", "observed_at": LATER,
                "observation": "fresh", "state": "held", "ownership": "confirmed",
            }]),
        )

        self.assertEqual([row["event_id"] for row in projected["waits"]["completed"]],
                         ["wait-completed"])
        self.assertEqual(projected["waits"]["completed"][0]["duration_seconds"], 30.0)
        self.assertEqual([row["event_id"] for row in projected["waits"]["current"]],
                         ["wait-current"])
        self.assertEqual(projected["waits"]["current"][0]["observed_for_seconds"], 480.0)
        self.assertEqual(projected["coverage"]["waits"], {
            "complete": False,
            "orphan_end_count": 2,
            "unpaired_start_count": 1,
            "current_without_event_count": 0,
        })
        [candidate] = projected["constraint_candidates"]
        self.assertEqual((candidate["observed_waits"], candidate["current_waits"],
                          candidate["completed_waits"]), (2, 1, 1))
        self.assertEqual(candidate["case_numbers"], [20, 21])
        self.assertEqual({source["kind"] for source in candidate["sources"]},
                         {"wait", "resource_observation", "case"})
        self.assertEqual({source.get("event_id") for source in candidate["sources"]
                          if source["kind"] == "wait"}, {"wait-completed", "wait-current"})

    def test_partial_sources_keep_totals_unknown_and_report_exact_denominator(self) -> None:
        observed = observation([case(30, "queued")], status="partial")
        running = execution("running", 31, state="active", ended_at=None)
        source_runtime = runtime([running], complete=False, truncated=True)
        before = copy.deepcopy((observed, source_runtime))

        first = flow.project(observed, source_runtime)
        second = flow.project(observed, source_runtime)

        self.assertEqual(first, second)
        self.assertEqual((observed, source_runtime), before)
        self.assertEqual(first["coverage"]["status"], "partial")
        for count in first["counts"].values():
            self.assertIsNone(count["total"])
        self.assertEqual(first["counts"]["selected_nonterminal_cases"]["observed"], 1)
        self.assertEqual(first["counts"]["known_started_wip"]["observed"], 1)
        self.assertEqual(first["denominator"], {
            "scope": "returned bounded case selection plus retained runtime journal",
            "selected_cases": 1,
            "selected_nonterminal_cases": 1,
            "runtime_executions": 1,
            "runtime_events": 0,
            "history_start_at": AT,
            "history_end_at": LATER,
            "history_complete": False,
            "history_truncated": True,
            "byte_limit": 1024 * 1024,
            "event_limit": 512,
        })
        self.assertIn("absence of start or activity is not evidence of absence",
                      " ".join(first["coverage"]["notices"]))

    def test_input_arrays_are_clipped_to_their_producer_bounds(self) -> None:
        observed = observation([case(number, "queued") for number in range(1, flow.CASE_LIMIT + 2)])
        executions = [execution(f"run-{number}", number, state="active", ended_at=None)
                      for number in range(1, flow.EVENT_LIMIT + 2)]
        projected = flow.project(observed, runtime(executions))

        self.assertTrue(projected["coverage"]["cases"]["input_clipped"])
        self.assertTrue(projected["coverage"]["runtime"]["input_clipped"])
        self.assertEqual(projected["coverage"]["cases"]["retained"], flow.CASE_LIMIT)
        self.assertEqual(projected["coverage"]["runtime"]["executions_retained"], flow.EVENT_LIMIT)
        self.assertLessEqual(len(projected["cases"]), flow.FLOW_CASE_LIMIT)
        self.assertIsNone(projected["counts"]["known_started_wip"]["total"])


if __name__ == "__main__":
    unittest.main()
