"""#828: provider rejection can leave a small, nonzero GraphQL budget."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gh_reads, lifecycle, triage, watch
from tests import test_gh_reads as read_fixture


class LowQuotaPathTest(unittest.TestCase):
    def test_dispatch_and_triage_reads_persist_nonzero_quota_reset(self):
        for path in ("run", "gh_json", "triage"):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                cfg = config.Config(Path(directory), "acme/widgets")
                dispatch.configure(cfg)
                triage.configure(cfg)
                journal = cfg.factory / "events.jsonl"
                calls = []

                def command(cmd, *args, **kwargs):
                    calls.append(cmd)
                    if cmd[:3] == ["gh", "api", "rate_limit"]:
                        return subprocess.CompletedProcess(cmd, 0, json.dumps({"resources": {
                            "core": {"remaining": 5000, "reset": 4102444900},
                            "graphql": {"remaining": 6, "reset": 4102444800},
                        }}), "")
                    result = subprocess.CompletedProcess(cmd, 1, "", "GraphQL: API rate limit exceeded")
                    if path != "triage":
                        result.check_returncode()
                    return result

                with (
                    mock.patch.object(dispatch, "_run", side_effect=command),
                    mock.patch("subprocess.run", side_effect=command),
                    gh_reads.scope(journal, path), gh_reads.item(7),
                ):
                    if path == "run":
                        dispatch.run(["gh", "issue", "view", "7", "--json", "state"])
                    elif path == "gh_json":
                        dispatch.gh_json(["pr", "list", "--repo", cfg.repo, "--limit", "1000", "--json", "number"])
                    else:
                        triage.gh("issue", "view", "7", "--json", "state")
                    self.fail("failed read must not return partial data")
                rows = lifecycle.read_events(journal)
                [skip] = [row for row in rows if row.get("event") == "gh-read-skipped"]
                self.assertEqual((skip["code"], skip["reset"], skip["returncode"]),
                                 ("rate_limited", 4102444800, 1))
                self.assertEqual(skip["rate_limit"]["graphql"]["remaining"], 6)
                self.assertEqual([cmd[:3] for cmd in calls][1:], [["gh", "api", "rate_limit"]])
                with gh_reads.scope(journal, path) as next_pass:
                    self.assertTrue(next_pass.stopped)
                    self.assertEqual(next_pass.reset, 4102444800)


class WatchLowQuotaTest(unittest.TestCase):
    setUp = read_fixture.PassReadTest.setUp
    probe = read_fixture.PassReadTest.probe
    command = read_fixture.PassReadTest.command
    rows = read_fixture.PassReadTest.rows

    def test_watch_read_with_six_units_preserves_ticket_and_defers_next_pass(self):
        self.remaining = 6
        self.stderr = "GraphQL: API rate limit exceeded"
        watch.run_pass(self.cfg)
        [skip] = self.rows("gh-read-skipped")
        self.assertEqual((skip["code"], skip["reset"]), ("rate_limited", self.reset))
        self.assertEqual(self.issues[2], self.before[2])
        self.assertFalse(any(cmd[:4] == ["gh", "issue", "view", "3"] for cmd in self.calls))
        self.calls.clear()
        self.assertEqual(watch.run_pass(self.cfg), [])
        self.assertEqual(self.calls, [])
