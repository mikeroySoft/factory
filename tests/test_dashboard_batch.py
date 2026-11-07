"""One-second gh reads must overlap without changing the snapshot contract."""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from factory import config, dashboard, dispatch, feedback
from tests.test_feedback import AT, H, ISSUE, PR, REPO, Provider

FIXTURE = Path(__file__).parent / "fixtures" / "dashboard-batch.json"
FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
f = json.loads(Path(os.environ["BATCH_FIXTURE"]).read_text())
query = next((a[6:] for a in args if a.startswith("query=")), "")
kind = "snapshot"
if "closingIssuesReferences" in query:
    kind = "head"
elif "reviewThreads" in query:
    kind = "threads"
elif "reviews(first:50,after:" in query:
    kind = "reviews"
elif "check-runs?" in args[-1]:
    kind = "checks"
elif "statuses?" in args[-1]:
    kind = "statuses"
fd = os.open(os.environ["BATCH_CALLS"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
def log(event):
    os.write(fd, (json.dumps([event, os.getpid(), time.monotonic(), kind]) + "\\n").encode())
log("start")
time.sleep(float(os.environ.get("BATCH_DELAY", "0")))
value = f["responses"][kind]
if kind in ("head", "reviews", "threads"):
    number = int(next(a[7:] for a in args if a.startswith("number=")))
    value = json.loads(json.dumps(value).replace('PR_80', 'PR_' + str(number)))
    if kind == "head":
        value["repository"]["pullRequest"]["number"] = number
        issue = value["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"][0]
        issue.update(id="I_" + str(number - 1), number=number - 1,
                     url="https://github.com/example/project/issues/" + str(number - 1))
log("end")
print(json.dumps(value if kind in ("checks", "statuses") else {"data": value}))
'''


def responses():
    provider = Provider()
    result = {
        "head": provider(query=feedback.HEAD_QUERY),
        "reviews": provider(query=feedback.REVIEW_QUERY),
        "threads": provider(query=feedback.THREAD_QUERY),
        "checks": provider(endpoint="/check-runs?"),
        "statuses": provider(endpoint="/statuses?"),
    }
    issues, prs = [], []
    for n in [79, 81, *range(100, 106)]:
        issues.append({**ISSUE, "id": f"I_{n}", "number": n,
                       "url": f"https://github.com/example/project/issues/{n}",
                       "title": "Feedback contract", "state": "OPEN", "body": "Keep constraint",
                       "createdAt": AT, "updatedAt": AT, "closedAt": None,
                       "labels": {"nodes": [{"name": "ready-for-human"}]}, "assignees": {"nodes": []}})
        prs.append({**PR, "id": f"PR_{n + 1}", "number": n + 1,
                    "url": f"https://github.com/example/project/pull/{n + 1}",
                    "title": "Feedback", "headRefName": f"agent/{n}", "headRefOid": H,
                    "state": "OPEN" if n < 100 else "CLOSED", "isDraft": False,
                    "createdAt": AT, "closedAt": None, "mergedAt": None,
                    "additions": 1, "deletions": 0, "changedFiles": 1, "body": "",
                    "reviewDecision": "CHANGES_REQUESTED", "labels": {"nodes": []},
                    "comments": {"nodes": []}, "commits": {"nodes": []}})
    result["snapshot"] = {"repository": {"id": REPO["id"], "issues": {"nodes": issues},
                                        "pullRequests": {"nodes": prs}}}
    return result


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat(AT).astimezone(tz)


@contextlib.contextmanager
def harness(delay=0):
    with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
        root = Path(directory)
        (root / "gh").write_text(FAKE_GH)
        (root / "gh").chmod(0o755)
        cfg = config.Config(root, REPO["slug"], lock=root / "gpu.lock")
        cfg.factory.mkdir()
        (cfg.factory / "events.jsonl").write_text("")
        calls = root / "calls.jsonl"
        stack.enter_context(patch.dict(os.environ, {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "BATCH_FIXTURE": str(FIXTURE), "BATCH_CALLS": str(calls), "BATCH_DELAY": str(delay),
        }))
        stack.enter_context(patch.dict(dashboard.__dict__))
        stack.enter_context(patch.dict(dispatch.__dict__))
        stack.enter_context(patch.object(config, "load", return_value=cfg))
        dashboard.configure(cfg)
        for name, value in {"dispatcher": {}, "upstream_state": {}, "triage_llm_online": False,
                            "sh": "", "iso": AT}.items():
            stack.enter_context(patch.object(dashboard, name, return_value=value))
        stack.enter_context(patch.object(config, "engine_commit", return_value=None))
        stack.enter_context(patch.object(config, "engine_drift", return_value=None))
        stack.enter_context(patch.object(feedback, "datetime", FixedDatetime))
        yield cfg, calls


def normalized(value, root):
    value = json.loads(json.dumps(value).replace(str(root), "$ROOT"))
    for row in value["resources"]:
        row["resource"]["id"] = row["resource"]["lock"]["path"]
        row["observed_at"] = AT
    return value


class DashboardBatchTest(unittest.TestCase):
    def assert_bounds(self, calls, elapsed, expected, limit):
        rows = [json.loads(line) for line in calls.read_text().splitlines()]
        starts = [row for row in rows if row[0] == "start"]
        self.assertEqual(len(starts), expected)
        active = peak = 0
        for event, _, _, _ in sorted(rows, key=lambda row: row[2]):
            active += 1 if event == "start" else -1
            peak = max(peak, active)
        self.assertLessEqual(peak, 8)
        self.assertLess(elapsed, limit, f"{expected} one-second gh calls serialized: {elapsed:.2f}s")

    def test_slow_gh_dashboard_matches_legacy_fixture(self):
        with harness(delay=1) as (cfg, calls):
            started = time.monotonic()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(dashboard.main(["--json"]), 0)
            elapsed = time.monotonic() - started
            result = normalized(json.loads(output.getvalue()), cfg.root)
            self.assertEqual(result, json.loads(FIXTURE.read_text())["snapshot"])
            self.assert_bounds(calls, elapsed, expected=13, limit=5)
            measurements = [json.loads(line) for line in
                            (cfg.factory / "dashboard-reads.jsonl").read_text().splitlines()]
            self.assertEqual(measurements[-1]["github_calls"], 13)
            self.assertLessEqual(measurements[-1]["wall_seconds"], elapsed)

    def test_slow_gh_feedback_matches_legacy_fixture(self):
        with harness(delay=1) as (_, calls):
            started = time.monotonic()
            result = feedback.collect(dashboard.github, repository=REPO, pr=PR, issue=ISSUE,
                                      observed_at=AT)
            elapsed = time.monotonic() - started
            self.assertEqual(result, json.loads(FIXTURE.read_text())["feedback"])
            self.assert_bounds(calls, elapsed, expected=6, limit=4)

    def test_eight_open_prs_share_transport_bound(self):
        fixture = json.loads(FIXTURE.read_text())
        for pr in fixture["responses"]["snapshot"]["repository"]["pullRequests"]["nodes"][:8]:
            pr["state"] = "OPEN"
        with harness(delay=0.05) as (cfg, calls):
            alternate = cfg.root / "fixture.json"
            alternate.write_text(json.dumps(fixture))
            with patch.dict(os.environ, {"BATCH_FIXTURE": str(alternate)}):
                result = dashboard.snapshot()
            self.assert_bounds(calls, 0, expected=49, limit=1)
            for ticket in result["tickets"]:
                if ticket["number"] in (79, 81, *range(100, 106)):
                    self.assertEqual(ticket["pr"]["feedback"]["coverage"]["checks"]["status"], "complete")



class FeedbackBatchDeadlineTest(unittest.TestCase):
    def test_independent_sources_fit_short_shared_deadline(self):
        provider = Provider()

        def slow_read(**kwargs):
            time.sleep(0.1)
            return provider(**kwargs)

        with patch.object(feedback, "DETAIL_TIMEOUT", 0.5):
            result = feedback.collect(slow_read, repository=REPO, pr=PR, issue=ISSUE,
                                      events=[{"event": "claimed", "ticket": 79}], observed_at=AT)
        self.assertTrue(all(c["status"] == "complete" for c in result["coverage"].values()))
        self.assertEqual({i["kind"] for i in result["items"]}, {"review", "review_comment", "check_run"})
        self.assertTrue(all(i["relevance"] == "current_head" for i in result["items"]))

    def test_expired_deadline_never_starts_provider_reads(self):
        provider = Provider()
        with patch.object(feedback, "DETAIL_TIMEOUT", 0), \
             patch.object(feedback.time, "monotonic", return_value=1):
            result = feedback.collect(provider, repository=REPO, pr=PR, issue=ISSUE, observed_at=AT)
        self.assertEqual(provider.calls, [])
        self.assertTrue(all(c["status"] == "unavailable" for c in result["coverage"].values()))
        self.assertEqual(result["items"], [])

    def test_slow_source_does_not_erase_completed_independent_evidence(self):
        provider = Provider()

        def slow_threads(**kwargs):
            if kwargs.get("query") == feedback.THREAD_QUERY:
                time.sleep(0.2)
                raise TimeoutError
            return provider(**kwargs)

        with patch.object(feedback, "DETAIL_TIMEOUT", 0.1):
            result = feedback.collect(slow_threads, repository=REPO, pr=PR, issue=ISSUE,
                                      events=[{"event": "claimed", "ticket": 79}], observed_at=AT)
        self.assertEqual({i["kind"] for i in result["items"]}, {"review", "check_run"})
        self.assertEqual(result["coverage"]["threads"]["status"], "unavailable")
        self.assertTrue(all(i["relevance"] == "unknown" for i in result["items"]))


if __name__ == "__main__":
    unittest.main()

