"""Completion outages defer the batch without publishing a triage verdict."""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

from factory import config, lifecycle, triage


class TriageUnavailable(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.dict(triage.__dict__))
        self.cfg = config.Config(self.root, "local/triage")
        self.cfg.lock = self.root / "triage.lock"
        self.cfg.triage_retry_cap = 1.5
        triage.configure(self.cfg)
        self.issues = {n: {
            "number": n, "title": f"Reject empty parser input {n}",
            "body": "Change the parser so empty input is rejected.\n\nAcceptance: a regression test passes.",
            "comments": [], "labels": [{"name": config.LABEL_TRIAGE}], "state": "OPEN",
        } for n in (3, 2, 1)}
        self.writes = []
        self.views = []
        self.events = self.cfg.factory / "events.jsonl"

    def gh(self, *args, execution=None):
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n} for n in self.issues])
        if args[:2] == ("issue", "view"):
            self.views.append(int(args[2]))
            return json.dumps(self.issues[int(args[2])])
        self.writes.append(args)
        return ""

    def run_pass(self, transport):
        with patch.object(triage, "gh", self.gh), \
                patch.object(triage.urllib.request, "urlopen", side_effect=transport) as requests, \
                patch.object(triage.time, "sleep") as sleeps, \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), \
                lifecycle.scope(self.events, "triage", lazy=True) as execution:
            result = triage.execute(argparse.Namespace(issue=None, replay=None, dry_run=False), execution)
        return result, requests, sleeps

    def test_unavailable_batch_stays_eligible_without_failure_or_verdict(self):
        failures = [
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            ConnectionRefusedError(111, "Connection refused"),
            TimeoutError("read timed out"),
            urllib.error.URLError(TimeoutError("connect timed out")),
            *[urllib.error.HTTPError(triage.LLM_URL, code, "unavailable", {}, None)
              for code in (500, 503, 599)],
        ]
        original = copy.deepcopy(self.issues)
        for failure in failures:
            with self.subTest(failure=failure):
                before = len(lifecycle.read_events(self.events))
                self.views.clear()
                result, requests, sleeps = self.run_pass(failure)
                self.assertEqual(result, 0)
                self.assertEqual(self.issues, original)
                self.assertEqual(self.writes, [])
                self.assertEqual(self.views, [3])
                self.assertEqual(requests.call_count, 3)
                self.assertEqual(sleeps.call_args_list, [call(1), call(1.5)])
                for args, kwargs in requests.call_args_list:
                    self.assertEqual(args[0].get_method(), "POST")
                    self.assertEqual(args[0].full_url, triage.LLM_URL)
                    self.assertEqual(kwargs, {"timeout": self.cfg.triage_timeout})
                rows = lifecycle.read_events(self.events)[before:]
                unavailable = [row for row in rows if row.get("kind") == "try_later"]
                self.assertEqual([(row["endpoint"], row["attempts"], row["ticket"])
                                  for row in unavailable], [(triage.LLM_URL, 3, 3)])
                self.assertFalse(any(row.get("event") in {"decision", "triage_stalled"}
                                     for row in rows))
                self.assertEqual([(row["stage"], row["outcome"], row["reason"])
                                  for row in rows if row.get("kind") == "exit"], [
                    ("triage-ticket", "not_admitted", "triage_endpoint_unavailable"),
                    ("triage", "not_admitted", "triage_endpoint_unavailable"),
                ])

    def test_recovery_retries_same_completion_then_continues_serial_batch(self):
        answer = json.dumps({"choices": [{"message": {"content": json.dumps({
            "decision": config.LABEL_AGENT, "rationale": "clear", "question": "", "brief": "fix parser",
        })}}]}).encode()
        result, requests, sleeps = self.run_pass([
            urllib.error.HTTPError(triage.LLM_URL, 503, "loading", {}, None),
            io.BytesIO(answer), io.BytesIO(answer), io.BytesIO(answer),
        ])
        self.assertEqual(result, 0)
        self.assertEqual(self.views, [3, 2, 1])
        self.assertEqual([json.loads(args[0].data)["messages"][1]["content"].split(":")[0]
                          for args, _ in requests.call_args_list],
                         ["Issue #3", "Issue #3", "Issue #2", "Issue #1"])
        sleeps.assert_called_once_with(1)
        self.assertEqual([int(args[2]) for args in self.writes if args[:2] == ("issue", "comment")],
                         [3, 2, 1])
        self.assertFalse(any(row.get("kind") == "try_later" for row in lifecycle.read_events(self.events)))

    def test_guards_and_deterministic_route_do_not_contact_server(self):
        self.issues[3]["labels"].append({"name": config.LABEL_INITIATIVE})
        self.issues[2].update(comments=[{
            "body": "Triage: supply reproduction", "createdAt": "2026-11-01T00:00:00Z",
        }], updatedAt="2026-11-01T00:00:00Z")
        self.issues[1]["body"] = "too short"
        result, requests, sleeps = self.run_pass(AssertionError("network called before guards"))
        self.assertEqual(result, 0)
        requests.assert_not_called()
        sleeps.assert_not_called()
        self.assertEqual([args[2] for args in self.writes if args[:2] == ("issue", "comment")], ["1"])

    def test_client_error_is_not_retried_or_reported_as_try_later(self):
        with patch.object(triage.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(
                triage.LLM_URL, 400, "bad request", {}, None)) as requests, \
                patch.object(triage.time, "sleep") as sleeps, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            triage.call_llm([{"role": "user", "content": "hi"}])
        requests.assert_called_once()
        sleeps.assert_not_called()

    def test_retry_cap_configuration_rejects_unbounded_or_negative_delay(self):
        path = self.root / ".factory.toml"
        with patch.object(config, "host_config", return_value={}), \
                patch.object(config, "repo_root", return_value=self.root):
            for value in ("-1", "inf", "nan", "true", '"slow"'):
                with self.subTest(value=value):
                    path.write_text(f'[repo]\nslug = "local/triage"\n[triage]\nretry_cap = {value}\n')
                    with self.assertRaises(config.ConfigError):
                        config.load(self.root)
            for value in (0, 0.5, 8):
                path.write_text(f'[repo]\nslug = "local/triage"\n[triage]\nretry_cap = {value}\n')
                cfg = config.load(self.root)
                self.cfg.triage_retry_cap = cfg.triage_retry_cap
                _, requests, sleeps = self.run_pass(urllib.error.URLError(ConnectionRefusedError()))
                self.assertEqual(requests.call_count, 3)
                self.assertEqual(sleeps.call_args_list, [call(min(1, value)), call(min(2, value))])


if __name__ == "__main__":
    unittest.main()
