"""`factory stats` on the runner (#313): parallel per-ticket reads, the dispatcher's root
from any cwd, missing fix commits in --escapes, and worker tokens from OMP transcripts."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
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


class PostFixOutcomeTest(unittest.TestCase):
    START = datetime(2026, 10, 20, tzinfo=UTC)
    END = datetime(2026, 10, 27, tzinfo=UTC)

    @staticmethod
    def event(at: str, event: str, ticket: int, **fields: object) -> dict:
        return {"at": f"2026-10-{at}T00:00:00Z", "event": event, "ticket": ticket, **fields}

    def outcomes(self, rows: list[dict]) -> list[dict]:
        return stats.weekly_metrics(rows, self.START, self.END, None)["post_fix_outcomes"]["by_worker_model"]

    def test_tracks_first_claim_and_each_interleaved_fix_round_to_its_first_outcome(self) -> None:
        rows = [
            self.event("21", "manage", 1, decision="FIX"),
            self.event("21", "manage", 2, decision="FIX"),
            self.event("21", "claimed", 1, worker_model="model/a"),
            self.event("21", "claimed", 1, worker_model="model/ignored"),
            self.event("21", "claimed", 2, worker_model="model/b"),
            self.event("22", "merged", 1),
            self.event("22", "escalate", 2),
            self.event("23", "manage", 1, decision="FIX"),
            self.event("23", "claimed", 1, worker_model="model/b"),
            self.event("24", "escalate", 1),
            self.event("25", "merged", 1),  # the round already ended at re-escalation
            self.event("25", "manage", 3, decision="FIX"),
            self.event("25", "claimed", 3, worker_model="model/a"),
        ]

        self.assertEqual(self.outcomes(rows), [
            {"worker_model": "model/a", "claim_status": "claimed", "merged": 1,
             "re_escalated": 0, "pending": 1, "resolved_merge_rate": 1.0},
            {"worker_model": "model/b", "claim_status": "claimed", "merged": 0,
             "re_escalated": 2, "pending": 0, "resolved_merge_rate": 0.0},
        ])

    def test_distinguishes_historical_claim_without_model_from_no_claim(self) -> None:
        rows = [
            self.event("21", "manage", 1, decision="FIX"),
            self.event("21", "claimed", 1),
            self.event("22", "merged", 1),
            self.event("23", "manage", 2, decision="FIX"),
            self.event("24", "escalate", 2),
        ]

        self.assertEqual(self.outcomes(rows), [
            {"worker_model": None, "claim_status": "model_not_recorded", "merged": 1,
             "re_escalated": 0, "pending": 0, "resolved_merge_rate": 1.0},
            {"worker_model": None, "claim_status": "no_claim", "merged": 0,
             "re_escalated": 1, "pending": 0, "resolved_merge_rate": 0.0},
        ])

    def test_only_fix_rows_in_the_inclusive_window_start_episodes(self) -> None:
        rows = [
            self.event("19", "manage", 1, decision="FIX"),
            self.event("20", "claimed", 1, worker_model="outside/fix"),
            self.event("21", "merged", 1),
            self.event("20", "manage", 2, decision="FIX"),
            self.event("20", "claimed", 2, worker_model="boundary"),
            self.event("27", "merged", 2),
            self.event("26", "manage", 3, decision="FIX"),
            self.event("26", "claimed", 3, worker_model="pending"),
            self.event("28", "merged", 3),
            self.event("28", "manage", 4, decision="FIX"),
        ]

        windowed = stats.window_rows(rows, self.START, self.END)
        self.assertEqual(self.outcomes(windowed), [
            {"worker_model": "boundary", "claim_status": "claimed", "merged": 1,
             "re_escalated": 0, "pending": 0, "resolved_merge_rate": 1.0},
            {"worker_model": "pending", "claim_status": "claimed", "merged": 0,
             "re_escalated": 0, "pending": 1, "resolved_merge_rate": None},
        ])

    def test_week_text_and_json_include_the_post_fix_report_and_noise_caveat(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        cfg = config.Config(root, "acme/widgets")
        cfg.factory.mkdir()
        rows = [
            self.event("21", "manage", 1, decision="FIX"),
            self.event("21", "claimed", 1, worker_model="model/a"),
            self.event("22", "merged", 1),
        ]
        (cfg.factory / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))

        with mock.patch.object(stats, "resolve", return_value=cfg), \
                mock.patch.object(stats, "_now", return_value=self.END), \
                mock.patch.object(stats, "stuck_over_days", return_value=[]):
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                stats.main(["--week"])
            encoded = io.StringIO()
            with contextlib.redirect_stdout(encoded):
                stats.main(["--week", "--json"])

        self.assertIn("Post-FIX outcomes by next claim worker_model", text.getvalue())
        self.assertIn("model/a", text.getvalue())
        self.assertIn("Small samples are noisy", text.getvalue())
        report = json.loads(encoded.getvalue())["post_fix_outcomes"]
        self.assertEqual(report["by_worker_model"][0]["worker_model"], "model/a")
        self.assertIn("Small samples are noisy", report["noise_caveat"])


if __name__ == "__main__":
    unittest.main()
