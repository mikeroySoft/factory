"""Promotion requires numeric author and current needs-triage actor authorization."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, lifecycle, triage
from factory.config import LABEL_AGENT, LABEL_HUMAN, LABEL_TRIAGE, Config


class TriageAuthorization(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.enterContext(patch.dict(triage.__dict__))
        self.cfg = Config(root=self.root, repo="local/triage", lock=self.root / "lock")
        self.cfg.triage_allowed_user_ids = [101]
        self.cfg.factory.mkdir()
        triage.configure(self.cfg)
        self.authors = {1: {"id": 202, "login": "owner"}, 2: {"id": 101, "login": "renamed-owner"}}
        self.labels = {n: {LABEL_TRIAGE} for n in self.authors}
        self.timeline = {n: [[self.event("labeled", author)]] for n, author in self.authors.items()}
        self.api_failure = False

    @staticmethod
    def event(kind, actor):
        return {"event": kind, "label": {"name": LABEL_TRIAGE}, "actor": actor}

    def gh(self, *args, execution=None):
        if args[0] == "api":
            if self.api_failure:
                raise SystemExit(1)
            n = int(args[1].split("/")[4])
            if args[1].endswith("/timeline"):
                return json.dumps(self.timeline[n])
            return json.dumps({"user": self.authors[n]})
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n} for n in self.labels if LABEL_TRIAGE in self.labels[n]])
        n = int(args[2])
        if args[:2] == ("issue", "view"):
            return json.dumps({
                "number": n, "title": "Fix parser", "state": "OPEN", "comments": [],
                "author": {"id": "MDQ6VXNlcjEwMQ==", "login": self.authors[n].get("login")},
                "body": "Reject empty parser input. " * 5 + "\nAcceptance: python -m unittest passes.",
                "labels": [{"name": name} for name in self.labels[n]],
            })
        if args[:2] == ("issue", "edit"):
            self.labels[n].discard(args[args.index("--remove-label") + 1])
            self.labels[n].add(args[args.index("--add-label") + 1])
        return ""

    def run_pass(self, *, dry_run=False):
        reply = json.dumps({"decision": LABEL_AGENT, "rationale": "Specified", "brief": "Fix it"})
        with patch.object(triage, "gh", side_effect=self.gh), \
                patch.object(triage, "call_llm", return_value=reply), \
                contextlib.redirect_stdout(io.StringIO()), \
                lifecycle.scope(self.cfg.factory / "events.jsonl", "triage", lazy=True) as execution:
            return triage.execute(argparse.Namespace(issue=None, replay=None, dry_run=dry_run), execution)

    def test_template_outsider_is_human_and_allowlisted_author_is_agent(self):
        self.assertEqual(self.run_pass(), 0)
        self.assertEqual(self.labels, {1: {LABEL_HUMAN}, 2: {LABEL_AGENT}})
        decisions = {row["ticket"]: row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl")
                     if row.get("event") == "decision" and row.get("stage") == "triage"}
        self.assertIn("author", decisions[1]["rationale"])
        self.assertEqual(decisions[1]["chosen"], LABEL_HUMAN)

    def test_latest_label_actor_must_be_allowed_across_pages(self):
        self.authors[1] = {"id": 101, "login": "owner"}
        self.timeline[1] = [[self.event("labeled", {"id": 101})], [
            self.event("unlabeled", {"id": 101}), self.event("labeled", {"id": 202, "login": "owner"}),
        ]]
        self.timeline[2] = [[self.event("labeled", {"id": 202})], [
            self.event("unlabeled", {"id": 202}), self.event("labeled", {"id": 101}),
        ]]
        self.run_pass()
        self.assertEqual(self.labels, {1: {LABEL_HUMAN}, 2: {LABEL_AGENT}})
        rows = lifecycle.read_events(self.cfg.factory / "events.jsonl")
        self.assertTrue(any(row.get("ticket") == 1 and "label actor" in row.get("rationale", "")
                            for row in rows))

    def test_empty_allowlist_promotes_nobody(self):
        self.cfg.triage_allowed_user_ids = []
        self.run_pass()
        self.assertEqual(self.labels, {1: {LABEL_HUMAN}, 2: {LABEL_HUMAN}})

    def test_missing_or_non_numeric_identity_never_promotes(self):
        for author in ({}, {"id": "101"}, {"id": True}):
            with self.subTest(author=author):
                self.authors[2] = author
                self.labels[2] = {LABEL_TRIAGE}
                self.run_pass()
                self.assertEqual(self.labels[2], {LABEL_HUMAN})

    def test_missing_or_removed_label_event_never_promotes(self):
        for pages in ([], [[]], [[self.event("labeled", None)]], [
                [self.event("labeled", {"id": 101}), self.event("unlabeled", {"id": 101})]]):
            with self.subTest(pages=pages):
                self.timeline[2] = pages
                self.labels[2] = {LABEL_TRIAGE}
                self.run_pass()
                self.assertEqual(self.labels[2], {LABEL_HUMAN})

    def test_api_failure_routes_to_human(self):
        self.api_failure = True
        self.run_pass()
        self.assertEqual(self.labels, {1: {LABEL_HUMAN}, 2: {LABEL_HUMAN}})

    def test_missing_or_non_numeric_label_actor_never_promotes(self):
        for actor in ({}, {"id": "101"}, {"id": True}):
            with self.subTest(actor=actor):
                self.timeline[2] = [[self.event("labeled", actor)]]
                self.labels[2] = {LABEL_TRIAGE}
                self.run_pass()
                self.assertEqual(self.labels[2], {LABEL_HUMAN})

    def test_watch_decision_cannot_bypass_authorization(self):
        with patch.object(triage, "watch_filed", return_value=True):
            self.run_pass()
        self.assertEqual(self.labels, {1: {LABEL_HUMAN}, 2: {LABEL_AGENT}})

    def test_direct_promotion_without_issue_snapshot_still_checks_author(self):
        decision = {"decision": LABEL_AGENT, "rationale": "Clear", "brief": "", "question": ""}
        with patch.object(triage, "gh", side_effect=self.gh):
            self.assertEqual(triage.apply_decision(1, decision, False), LABEL_HUMAN)
        self.assertEqual(self.labels[1], {LABEL_HUMAN})

    def test_dry_run_does_not_change_labels(self):
        self.run_pass(dry_run=True)
        self.assertEqual(self.labels, {1: {LABEL_TRIAGE}, 2: {LABEL_TRIAGE}})


class TriageAllowlistConfig(unittest.TestCase):
    def load(self, setting):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / ".factory.toml").write_text('[repo]\nslug = "local/triage"\n[triage]\n' + setting)
        with patch.object(config, "repo_root", return_value=root), \
                patch.object(config, "host_config", return_value={}):
            return config.load(root)

    def test_missing_and_empty_allowlist_are_closed(self):
        for setting in ("", "allowed_user_ids = []"):
            with self.subTest(setting=setting):
                self.assertEqual(self.load(setting).triage_allowed_user_ids, [])

    def test_numeric_allowlist_loads(self):
        self.assertEqual(self.load("allowed_user_ids = [101, 202]").triage_allowed_user_ids, [101, 202])

    def test_invalid_allowlist_is_rejected(self):
        for value in ('["owner"]', '["101"]', '[true]', '[0]', '[-1]', '101'):
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                self.load("allowed_user_ids = " + value)
