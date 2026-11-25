from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import timedelta

from factory import stats
from tests.test_post_fix_models import NOW, START, at


def journal(rows: list[dict]) -> list[dict]:
    """Round-trip rows the same way events.jsonl is decoded."""
    return json.loads(json.dumps(rows))


class PostFixBoundaryTest(unittest.TestCase):
    def test_invalid_and_boolean_tickets_do_not_interrupt_or_alias_integer_ticket(self) -> None:
        rows = journal([
            {"at": at(timedelta(hours=-6)), "event": "manage", "ticket": 1, "decision": "FIX"},
            {"at": at(timedelta(hours=-5)), "event": "merged", "ticket": "irrelevant"},
            {"at": at(timedelta(hours=-4)), "event": "merged", "ticket": True},
            {"at": at(timedelta(hours=-3)), "event": "claimed", "ticket": 1,
             "worker_model": "model/one"},
            {"at": at(timedelta(hours=-2)), "event": "merged", "ticket": 1},
        ])

        result = stats.post_fix_metrics(rows, START, NOW)

        self.assertEqual((result["cohorts"], result["merged"], result["pending"]), (1, 1, 0))
        self.assertEqual(list(result["by_worker_model"]), ["model/one"])

    def test_event_and_decision_names_are_exact(self) -> None:
        rows = journal([
            {"at": at(timedelta(hours=-12)), "event": "manage ", "ticket": 2, "decision": "FIX"},
            {"at": at(timedelta(hours=-11)), "event": "manage", "ticket": 3, "decision": "FIX "},
            *(
                {"at": at(timedelta(hours=-10 + ticket)), "event": "manage", "ticket": ticket,
                 "decision": decision}
                for ticket, decision in enumerate(("HUMAN", "CLOSE", "RETRY"), start=4)
            ),
            {"at": at(timedelta(hours=-3)), "event": "manage", "ticket": 1, "decision": "FIX"},
            {"at": at(timedelta(hours=-2, minutes=-45)), "event": "merged ", "ticket": 1},
            {"at": at(timedelta(hours=-2, minutes=-30)), "event": "escalate ", "ticket": 1},
            {"at": at(timedelta(hours=-2)), "event": "claimed", "ticket": 1,
             "worker_model": "model/exact"},
            {"at": at(timedelta(hours=-1)), "event": "merged", "ticket": 1},
        ])

        result = stats.post_fix_metrics(rows, START, NOW)

        self.assertEqual((result["cohorts"], result["merged"], result["re_escalated"]), (1, 1, 0))
        self.assertEqual(list(result["by_worker_model"]), ["model/exact"])

    def test_only_claim_and_worker_execution_evidence_assign_models(self) -> None:
        rows = journal([
            {"at": at(timedelta(hours=-12)), "event": "manage", "ticket": 10, "decision": "FIX"},
            {"at": at(timedelta(hours=-11)), "event": "queued", "ticket": 10,
             "worker_model": "model/not-admission"},
            {"at": at(timedelta(hours=-10)), "event": "merged", "ticket": 10},
            {"at": at(timedelta(hours=-9)), "event": "manage", "ticket": 11, "decision": "FIX"},
            {"at": at(timedelta(hours=-8, minutes=-50)), "event": "claimed ", "ticket": 11,
             "worker_model": "model/not-claimed"},
            {"at": at(timedelta(hours=-8, minutes=-40)), "event": "claimed", "ticket": 11,
             "worker_model": "model/admission"},
            {"at": at(timedelta(hours=-8, minutes=-30)), "event": "llm-usage", "ticket": 11,
             "stage": "triage", "worker_model": "model/not-worker"},
            {"at": at(timedelta(hours=-8, minutes=-20)), "event": "llm-usage", "ticket": 11,
             "stage": "worker ", "worker_model": "model/not-worker-stage"},
            {"at": at(timedelta(hours=-8)), "event": "attempt", "ticket": 11,
             "worker_model": "model/first-attempt"},
            {"at": at(timedelta(hours=-7)), "event": "attempt", "ticket": 11,
             "worker_model": "model/retry"},
            {"at": at(timedelta(hours=-6)), "event": "merged", "ticket": 11},
            {"at": at(timedelta(hours=-5)), "event": "manage", "ticket": 12, "decision": "FIX"},
            {"at": at(timedelta(hours=-4)), "event": "claimed", "ticket": 12,
             "worker_model": "model/admission-two"},
            {"at": at(timedelta(hours=-3)), "event": "llm-usage", "ticket": 12,
             "stage": "worker", "worker_model": "model/worker-usage"},
            {"at": at(timedelta(hours=-2)), "event": "attempt", "ticket": 12,
             "worker_model": "model/later-attempt"},
            {"at": at(timedelta(hours=-1)), "event": "merged", "ticket": 12},
        ])

        result = stats.post_fix_metrics(rows, START, NOW)

        self.assertEqual(result["cohorts"], 3)
        self.assertEqual(
            {model: counts["merged"] for model, counts in result["by_worker_model"].items()},
            {
                stats.NO_WORKER_CLAIM: 1,
                "model/first-attempt": 1,
                "model/worker-usage": 1,
            },
        )

    def test_pending_cohorts_and_resolved_rates_are_reported_distinctly(self) -> None:
        rows = journal([
            {"at": at(timedelta(hours=-9)), "event": "manage", "ticket": 20, "decision": "FIX"},
            {"at": at(timedelta(hours=-8)), "event": "claimed", "ticket": 20,
             "worker_model": "model/pending"},
            {"at": at(timedelta(hours=-7)), "event": "manage", "ticket": 21, "decision": "FIX"},
            {"at": at(timedelta(hours=-6)), "event": "claimed", "ticket": 21,
             "worker_model": "model/resolved"},
            {"at": at(timedelta(hours=-5)), "event": "merged", "ticket": 21},
            {"at": at(timedelta(hours=-4)), "event": "manage", "ticket": 22, "decision": "FIX"},
            {"at": at(timedelta(hours=-3)), "event": "claimed", "ticket": 22,
             "worker_model": "model/resolved"},
            {"at": at(timedelta(hours=-2)), "event": "escalate", "ticket": 22},
        ])
        result = stats.post_fix_metrics(rows, START, NOW)

        self.assertEqual(
            (result["cohorts"], result["pending"], result["resolved"], result["merge_rate"]),
            (3, 1, 2, 0.5),
        )
        self.assertIsNone(result["by_worker_model"]["model/pending"]["merge_rate"])
        self.assertEqual(result["by_worker_model"]["model/resolved"]["merge_rate"], 0.5)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stats.print_post_fix(result)
        lines = output.getvalue().splitlines()
        pending = next(line for line in lines if line.startswith("model/pending"))
        resolved = next(line for line in lines if line.startswith("model/resolved"))

        self.assertTrue(pending.endswith("n/a"))
        self.assertTrue(resolved.endswith("0.5"))
        self.assertEqual(output.getvalue().count("n/a"), 1)
        self.assertIn("resolved merge rate 0.5 (1/2)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
