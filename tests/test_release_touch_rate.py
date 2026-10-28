"""Human-touch measurement and deduplicated release-bar holds."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, lifecycle, release


class ReleaseTouchRateTest(unittest.TestCase):
    def test_measure_counts_each_human_touch_without_a_cap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            path = cfg.factory / "events.jsonl"
            for ticket in (1, 2):
                lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "merged", "ticket": ticket})
            lifecycle.append(path, {"at": "2099-01-01T00:00:01Z", "event": "manage", "ticket": 1,
                                    "decision": "HUMAN"})
            lifecycle.append(path, {"at": "2099-01-01T00:00:02Z", "event": "manage", "ticket": 1,
                                    "decision": "HUMAN"})
            lifecycle.append(path, {"at": "2099-01-01T00:00:03Z", "event": "human-decision", "ticket": 2})
            measured = release.measure(cfg, "2098-01-01T00:00:00Z")
            self.assertEqual(measured["human_rate"], 1.5)
            self.assertEqual(measured["human_touches"], 3)
            self.assertEqual(measured["human_ticket_rate"], 0.5)

    def test_measure_counts_repeated_manager_touches_without_human_decision_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            path = cfg.factory / "events.jsonl"
            for ticket in (1, 2):
                lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "merged", "ticket": ticket})
            for second in range(1, 4):
                lifecycle.append(path, {"at": f"2099-01-01T00:00:0{second}Z", "event": "manage",
                                        "ticket": 1, "decision": "HUMAN"})
            measured = release.measure(cfg, "2098-01-01T00:00:00Z")
            self.assertEqual(measured["human_rate"], 1.5)
            self.assertEqual(measured["human_touches"], 3)

    def test_measure_counts_one_touch_per_human_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="acme/widgets")
            path = cfg.factory / "events.jsonl"
            lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "merged", "ticket": 1})
            for second, status in enumerate(("prepared", "started", "applied"), 1):
                lifecycle.append(path, {
                    "at": f"2099-01-01T00:00:0{second}Z",
                    "event": "human-decision",
                    "ticket": 1,
                    "decision_id": "decision-1",
                    "status": status,
                })
            measured = release.measure(cfg, "2098-01-01T00:00:00Z")
            self.assertEqual((measured["human_rate"], measured["human_touches"]), (1.0, 1))

    def test_identical_bar_hold_is_journaled_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            origin = base / "origin.git"
            repo = base / "repo"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
            subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Release Test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "release@example.invalid"], check=True)
            (repo / "factory").mkdir()
            (repo / "factory" / "__init__.py").write_text('__version__ = "0.0.1"\n')
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
            bar = {
                "human_rate": 0.1,
                "first_pass": 0.9,
                "tickets": 20,
                "pending_tickets": 0,
                "pending_human_touches": 0,
                "pending_first_passes": 0,
                "pending_attempts": 0,
            }
            message = json.dumps(bar, separators=(",", ":"))
            subprocess.run(["git", "-C", str(repo), "tag", "-a", "v0.0.1", "-m", f"Release\n\n```json\n{message}\n```"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main", "v0.0.1"], check=True)
            (repo / "change").write_text("change\n")
            subprocess.run(["git", "-C", str(repo), "add", "change"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change"], check=True)
            subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
            cfg = config.Config(root=repo, repo="acme/widgets", release_min_tickets=1)
            path = cfg.factory / "events.jsonl"
            lifecycle.append(path, {"at": "2099-01-01T00:00:00Z", "event": "merged", "ticket": 1})
            lifecycle.append(path, {"at": "2099-01-01T00:00:01Z", "event": "attempt", "ticket": 1,
                                    "attempt": 1, "gate": "PASS"})
            lifecycle.append(path, {"at": "2099-01-01T00:00:02Z", "event": "manage", "ticket": 1,
                                    "decision": "HUMAN"})
            with mock.patch.object(release, "_pulls", return_value=[]):
                first = release.run(cfg)
                second = release.run(cfg)
            self.assertIn("human_rate 1.0 is worse than 0.1", first)
            self.assertEqual(second, first)
            held = [row for row in lifecycle.read_events(path)
                    if row.get("event") == "release" and row.get("outcome") == "held"]
            self.assertEqual(len(held), 1)
            self.assertEqual(held[0]["bar"], bar)


if __name__ == "__main__":
    unittest.main()
