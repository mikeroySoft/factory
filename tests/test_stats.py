"""`factory stats` on the runner (#313): parallel per-ticket reads, the dispatcher's root
from any cwd, missing fix commits in --escapes, and worker tokens from OMP transcripts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, stats


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def repo(root: Path, slug: str = "acme/widgets") -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", f"https://github.com/{slug}.git")
    return root


class ParallelReadsTest(unittest.TestCase):
    def test_per_ticket_reads_overlap_but_never_exceed_the_worker_bound(self) -> None:
        tickets = range(1, 41)
        lock, calls = threading.Lock(), []
        inflight = peak = 0

        def gh(*args: str):
            nonlocal inflight, peak
            if args[:2] in (("pr", "list"), ("issue", "list")):
                calls.append(args[:2])
                return []
            with lock:
                calls.append(args[:2])
                inflight += 1
                peak = max(peak, inflight)
            time.sleep(0.02)
            with lock:
                inflight -= 1
            if args[:2] == ("issue", "view"):
                return {"number": int(args[2]), "title": "t", "state": "CLOSED",
                        "createdAt": "2026-10-01T00:00:00Z", "closedAt": None, "comments": []}
            return [[]]

        with tempfile.TemporaryDirectory() as d:
            stats.configure(config.Config(Path(d), "acme/widgets"))
            stats.cfg.factory.mkdir()
            (stats.cfg.factory / "events.jsonl").write_text(
                "".join(json.dumps({"event": "claimed", "ticket": n}) + "\n" for n in tickets))
            err = io.StringIO()
            with mock.patch.object(stats, "gh", side_effect=gh), \
                 mock.patch.object(stats, "dispatcher_login", return_value=""), \
                 contextlib.redirect_stderr(err):
                rows = stats.collect_rows(progress=True)

        self.assertEqual([row["ticket"] for row in rows], list(tickets))
        self.assertTrue(2 <= peak <= stats.WORKERS, peak)
        # Each ticket is read once (issue + timeline), plus three list calls.
        self.assertEqual(len(calls), 3 + 2 * len(tickets))
        self.assertTrue(err.getvalue().endswith(f"stats: {len(tickets)}/{len(tickets)} tickets\n"))


class RootResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = repo(tmp / "root")
        (self.root / ".factory").mkdir()
        (self.root / ".factory" / "events.jsonl").write_text(
            json.dumps({"event": "attempt", "ticket": 5, "attempt": 1, "gate": "PASS", "brief": True}) + "\n")
        self.checkout = repo(tmp / "dev")  # same repository, no .factory/
        self.elsewhere = tmp / "elsewhere"
        self.elsewhere.mkdir()
        self.host = tmp / "xdg"
        (self.host / "factory").mkdir(parents=True)
        self.enterContext(mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.host)}))

    def register(self, slug: str = "acme/widgets") -> None:
        (self.host / "factory" / "config.toml").write_text(f'[repo."{slug}"]\npath = "{self.root}"\n')

    def run_stats(self, cwd: Path, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.chdir(cwd), contextlib.redirect_stdout(out):
            stats.main(["--by-brief", *argv])
        return out.getvalue()

    def test_checkout_without_factory_reads_the_registered_root(self) -> None:
        self.register()
        lines = self.run_stats(self.checkout).splitlines()
        self.assertIn(["with", "brief", "100.0%", "1"], [line.split() for line in lines])
        self.assertEqual(stats.cfg.root.resolve(), self.root.resolve())

    def test_repo_flag_works_outside_any_checkout(self) -> None:
        self.register()
        self.assertIn("100.0%", self.run_stats(self.elsewhere, "--repo", "acme/widgets"))

    def test_unregistered_checkout_fails_naming_the_journal_path(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self.run_stats(self.checkout)
        self.assertIn(str(self.checkout.resolve() / ".factory" / "events.jsonl"), str(raised.exception))

    def test_unregistered_repo_flag_fails_naming_the_host_config(self) -> None:
        self.register()
        with self.assertRaises(SystemExit) as raised:
            self.run_stats(self.elsewhere, "--repo", "acme/other")
        self.assertIn('[repo."acme/other"].path', str(raised.exception))
        self.assertIn(str(self.host / "factory" / "config.toml"), str(raised.exception))


class MissingFixCommitTest(unittest.TestCase):
    def test_fix_is_fetched_from_origin_or_skipped_and_counted(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        origin = tmp / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "t@example.com")
        git(origin, "config", "user.name", "T")
        (origin / "f").write_text("a\nb\n")
        git(origin, "add", "f")
        git(origin, "commit", "-q", "-m", "agent/5: add f")
        clone = tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        (origin / "f").write_text("a\nB\n")
        git(origin, "commit", "-q", "-am", "Fix b (#40)")  # only on origin, not yet fetched
        fetched, missing = git(origin, "rev-parse", "HEAD"), "1" * 40
        bugs = [{"number": n, "title": f"bug {n}", "body": "", "closedAt": "2026-10-10T00:00:00Z"} for n in (9, 10)]

        def gh(*args: str):
            if args[:2] == ("issue", "list"):
                return bugs
            return [{"mergeCommit": {"oid": fetched}, "closingIssuesReferences": [{"number": 9}]},
                    {"mergeCommit": {"oid": missing}, "closingIssuesReferences": [{"number": 10}]}]

        stats.configure(config.Config(clone, "acme/widgets"))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(stats, "gh", side_effect=gh), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rows = {row["bug"]: row for row in stats.escapes()}
            stats.print_escapes(list(rows.values()), {5})

        self.assertEqual(([c["ticket"] for c in rows[9]["candidates"]], rows[9]["skipped"]), ([5], []))
        self.assertEqual((rows[10]["status"], rows[10]["skipped"], rows[10]["candidates"]), ("skipped", [missing], []))
        self.assertIn("skipped fix 1111111 of bug #10", err.getvalue())
        self.assertIn("Skipped fixes: 1 not in this clone or on origin (1111111)", out.getvalue())


class WorkerUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(dispatch, "cfg", config.Config(self.tmp, "acme/widgets"), create=True))
        self.log = self.tmp / "attempt.log"
        self.log.write_text('{"type": "result", "p": 4, "c": 1}\n')
        self.transcript = self.tmp / "session.jsonl"
        message = lambda role, **fields: json.dumps({"type": "message", "message": {"role": role, **fields}})
        self.transcript.write_text("\n".join([
            json.dumps({"type": "session", "version": 3}),
            message("user", content="do it"),
            message("assistant", model="m1", usage={"input": 10, "output": 5, "cacheRead": 100, "cacheWrite": 20}),
            message("assistant", model="m1", usage={"input": 3, "output": 2}),
            message("assistant", model="m1", usage={"input": -1, "output": 2}),  # malformed: skipped
            json.dumps({"type": "model_usage", "usage": {"input": 999, "output": 999}}),  # side model, not the worker
            "not json",
        ]) + "\n")

    def test_omp_transcript_totals_fill_unreported_worker_tokens(self) -> None:
        self.assertEqual(dispatch.worker_usage(self.log, None, str(self.transcript)), {
            "model": "m1", "prompt_tokens": 133, "completion_tokens": 7, "cached_tokens": 100, "cost": None})
        self.assertEqual(dispatch.worker_usage(self.log, None), dict.fromkeys(("model", *config.USAGE_FIELDS)))

    def test_log_reported_tokens_win_over_the_transcript(self) -> None:
        spec = {"match": {"type": "result"}, "model": None, "prompt_tokens": ["p"], "completion_tokens": ["c"],
                "cached_tokens": [], "cost": []}
        usage = dispatch.worker_usage(self.log, spec, str(self.transcript))
        self.assertEqual((usage["prompt_tokens"], usage["completion_tokens"]), (4, 1))

    def test_stats_shows_worker_tokens_or_not_reported(self) -> None:
        def row(ticket: int, events: list[dict]) -> dict:
            return {"ticket": ticket, "title": "t", "state": "merged", "attempts": 1, "review_rounds": 0,
                    "merge_hours": None, "escalation_count": 0, "manager_failures": 0, "resolutions": [],
                    "ready_for_human_minutes": 0.0, "requeue_count": 0, "llm_usage": stats.llm_usage(events)}

        usage = dispatch.worker_usage(self.log, None, str(self.transcript))
        reported = row(7, [{"event": "llm-usage", "stage": "worker", **usage}])
        silent = row(8, [{"event": "llm-usage", "stage": "worker", **dict.fromkeys(("model", *config.USAGE_FIELDS))}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            stats.print_table([reported, silent])
        header, _, first, second = out.getvalue().splitlines()
        self.assertIn("133", first)
        self.assertEqual(second.count(stats.NOT_REPORTED), 3)  # prompt, completion, cached
        self.assertNotIn("$", header)  # no prices and no reported cost: no column of blanks
        self.assertEqual(stats.worker_tokens([reported, silent]), "133 prompt, 7 completion (1 of 2 tickets reported)")
        self.assertEqual(stats.worker_tokens([silent]), stats.NOT_REPORTED)


if __name__ == "__main__":
    unittest.main()
