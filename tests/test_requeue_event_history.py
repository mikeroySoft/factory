"""Owner requeue can recover host pins without capturing unbounded issue history."""
import json
import subprocess
import sys
import unittest

from factory import decisions
from tests import test_actions


class RequeueEventHistoryTest(unittest.TestCase):
    run_command = staticmethod(decisions._run)
    via_mcp = test_actions.ActionPathsTest.via_mcp

    def setUp(self):
        self.events = []
        self.fail_page = None
        test_actions.ActionPathsTest.setUp(self)

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"] and any(arg.endswith("/events") for arg in argv):
            page = next((int(arg[5:]) for arg in argv if arg.startswith("page=")), 1)
            if page == self.fail_page:
                return subprocess.CompletedProcess(argv, 1, "", "provider unavailable")
            rows = self.events
            if "--paginate" not in argv and isinstance(rows, list):
                rows = rows[(page - 1) * 100:page * 100]
            if "--jq" in argv and isinstance(rows, list):
                # Model gh's projection before its stdout enters the bounded command runner.
                rows = [
                    {"event": row.get("event"), "label": row.get("label")}
                    if isinstance(row, dict) else row for row in rows
                ]
            if "--slurp" in argv:
                rows = [rows]
            response = self.root / "event-response.json"
            response.write_text(json.dumps(rows))
            return self.run_command([
                sys.executable, "-c",
                "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                str(response),
            ], **kwargs)
        return test_actions.ActionPathsTest.transport(self, argv, **kwargs)

    def preview_labels(self, intent="resume"):
        preview = self.via_mcp("requeue_agent", {"number": 7, "intent": intent})
        self.assertIn("proposal_id", preview, preview)
        return set(preview["requests"][0]["add"])

    def test_large_provider_events_requeue_both_intents_with_or_without_pin(self):
        for intent in ("resume", "fresh"):
            for pinned in (False, True):
                with self.subTest(intent=intent, pinned=pinned):
                    self.events = [
                        {"event": "commented", "actor": {"profile": "x" * 4096}}
                        for _ in range(200)
                    ] + [{"event": "labeled", "label": {"name": "ready-for-human"}}]
                    if pinned:
                        self.events.append({"event": "unlabeled", "label": {"name": "host:ultron"}})
                    self.assertGreater(len(json.dumps(self.events)), decisions.READ_CAP)
                    expected = {"ready-for-agent", *(["host:ultron"] if pinned else [])}
                    self.assertEqual(self.preview_labels(intent), expected)

    def test_projected_history_larger_than_read_cap_is_not_truncated(self):
        self.events = [{"event": "commented", "label": None}] * 7000 + [
            {"event": "labeled", "label": {"name": "ready-for-human"}},
            {"event": "unlabeled", "label": {"name": "host:ultron"}},
        ]
        self.assertGreater(len(json.dumps(self.events)), decisions.READ_CAP)
        self.assertEqual(self.preview_labels(), {"ready-for-agent", "host:ultron"})

    def test_reverse_history_respects_latest_transition_and_later_label_changes(self):
        self.events = [
            {"event": "labeled", "label": {"name": "ready-for-human"}},
            {"event": "unlabeled", "label": {"name": "host:old"}},
            {"event": "unlabeled", "label": {"name": "ready-for-human"}},
            {"event": "labeled", "label": {"name": "ready-for-agent"}},
            {"event": "labeled", "label": {"name": "ready-for-human"}},
            {"event": "unlabeled", "label": {"name": "host:ultron"}},
            {"event": "labeled", "label": {"name": "host:temporary"}},
            {"event": "unlabeled", "label": {"name": "host:temporary"}},
            {"event": "unlabeled", "label": {"name": "ready-for-human"}},
            {"event": "labeled", "label": {"name": "needs-info"}},
            {"event": "labeled", "label": {"name": "wontfix"}},
            {"event": "commented"},
        ]
        self.assertEqual(self.preview_labels(), {"ready-for-agent", "host:ultron"})

    def test_no_escalation_transition_does_not_restore_removed_host(self):
        self.events = [{"event": "unlabeled", "label": {"name": "host:ultron"}}]
        self.assertEqual(self.preview_labels(), {"ready-for-agent"})

    def test_unavailable_later_page_refuses_requeue(self):
        self.events = [{"event": "commented"}] * 100
        self.fail_page = 2
        refused = self.via_mcp("requeue_agent", {"number": 7})
        self.assertEqual(refused.get("error", {}).get("code"), "provider_unavailable", refused)
        self.assertEqual(self.mutations, [])

    def test_malformed_history_refuses_requeue(self):
        for events in ({}, [None], [{"event": "labeled"}],
                       [{"event": "unlabeled", "label": "host:ultron"}],
                       [{"event": "labeled", "label": {"name": 7}}]):
            with self.subTest(events=events):
                self.events = events
                refused = self.via_mcp("requeue_agent", {"number": 7})
                self.assertEqual(refused.get("error", {}).get("code"), "provider_unavailable", refused)
                self.assertEqual(self.mutations, [])
