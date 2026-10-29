"""Throttle mode state, presets, and admission policy (#442)."""
from __future__ import annotations

import copy
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from factory import binding, config, dispatch, lifecycle, schedule, throttle

REPO = "example/project"
INITIATIVE = """**Status**
underway

**Outcome**
A shared result.

**Owner**
@alice

**Areas**
factory/

**Boundaries**
No execution authority.

**Plan**
NOW #3

**Open decisions**
None.

**Success evidence**
Tests.

**Implementation links**
#3
"""
PARENT = {"number": 50, "title": "Roadmap", "body": INITIATIVE, "labels": [{"name": "initiative"}]}
BOUND = binding.render(binding.from_issue(config.Config(Path("/nonexistent"), REPO), 50, PARENT))


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def make_repo(tmp: Path, toml: str = "") -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "remote", "add", "origin", "git@github.com:example/project.git")
    (repo / "README.md").write_text("hello\n")
    if toml:
        (repo / config.CONFIG_NAME).write_text(toml)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def ticket(
    number: int,
    created: str,
    *,
    body: str = BOUND,
    labels: tuple[str, ...] = ("ready-for-agent",),
) -> dict:
    return {
        "number": number,
        "title": f"t{number}",
        "body": body,
        "createdAt": created,
        "labels": [{"name": name} for name in labels],
        "assignees": [],
    }


class ConfigTest(unittest.TestCase):
    def test_presets_default_to_existing_knobs_and_accept_bounded_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(
                Path(d),
                "[dispatch]\nmax_active = 7\nmax_attempts = 6\nreview_rounds = 4\nbudget_min = 120\n"
                "[manager]\nrounds = 2\n"
                "[throttle]\nmaintenance_label = \"maintenance\"\npace_low = 0\npace_high = 4\n"
                "aggression_high = { review_rounds = 8, manager_rounds = 7, max_attempts = 5, budget_min = 180 }\n",
            )
            cfg = config.load(repo)
        self.assertEqual(cfg.throttle_pace, {"low": 0, "normal": 7, "high": 4})
        self.assertEqual(
            cfg.throttle_aggression["normal"],
            {"review_rounds": 4, "manager_rounds": 2, "max_attempts": 6, "budget_min": 120},
        )
        self.assertEqual(cfg.throttle_aggression["high"]["budget_min"], 180)
        self.assertEqual(cfg.throttle_maintenance_label, "maintenance")
        self.assertEqual(config.unknown_keys({"throttle": {"pace_low": 1, "surprise": 2}}), ["throttle.surprise"])

    def test_invalid_throttle_values_are_rejected(self):
        bad = (
            "pace_low = -1\n",
            "maintenance_label = \"\"\n",
            "aggression_high = { review_rounds = -1, manager_rounds = 1, max_attempts = 1, budget_min = 1 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 0, budget_min = 1 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 1, budget_min = 0 }\n",
            "aggression_high = { review_rounds = 1, manager_rounds = 1, max_attempts = 1, budget_min = 1, extra = 2 }\n",
        )
        for value in bad:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as d:
                repo = make_repo(Path(d), f"[throttle]\n{value}")
                with self.assertRaises(config.ConfigError):
                    config.load(repo)


class StateAndCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(Path(temp.name), REPO)

    def test_mode_changes_are_atomic_idempotent_and_journaled_once(self):
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        self.assertTrue(throttle.set_mode(self.cfg, throttle.Mode("maintenance"), who="michael"))
        self.assertFalse(throttle.set_mode(self.cfg, throttle.Mode("maintenance"), who="michael"))
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("maintenance"))
        [row] = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row["event"] == "mode"]
        self.assertEqual((row["who"], row["mode"], row["feature"]), ("michael", "maintenance", None))
        self.assertIn("at", row)

    def test_feature_drive_command_validates_the_current_repository_issue(self):
        valid = {"state": "OPEN", "labels": [{"name": config.LABEL_AGENT}], "url": f"https://github.com/{REPO}/issues/7"}
        cases = (
            ({**valid, "state": "CLOSED"}, "closed"),
            ({**valid, "labels": []}, "ready-for-agent"),
            ({**valid, "url": "https://github.com/other/repo/issues/7"}, "another repository"),
        )
        for issue, message in cases:
            with self.subTest(message=message), mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                    mock.patch.object(throttle, "issue", return_value=issue), redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(throttle.main(["feature-drive", "7"]), 2)
                self.assertIn(message, stderr.getvalue())
                self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                mock.patch.object(throttle, "issue", return_value=valid), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(throttle.main(["feature-drive", "7"]), 0)
            self.assertEqual(throttle.read(self.cfg), throttle.Mode("feature-drive", 7))
            self.assertIn("feature-drive #7", stdout.getvalue())
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(throttle.main([]), 0)
            self.assertEqual(stdout.getvalue().strip(), "feature-drive #7")

    def test_feature_target_repository_match_is_case_insensitive(self):
        issue = {
            "state": "OPEN",
            "labels": [{"name": config.LABEL_AGENT}],
            "url": "https://github.com/Example/Project/issues/7",
        }
        self.assertIsNone(throttle.feature_error(self.cfg, 7, issue))

    def test_open_initiative_is_a_valid_feature_target(self):
        initiative = {"state": "OPEN", "labels": [{"name": config.LABEL_INITIATIVE}], "url": f"https://github.com/{REPO}/issues/9"}
        with mock.patch.object(throttle.config, "load", return_value=self.cfg), \
                mock.patch.object(throttle, "issue", return_value=initiative), redirect_stdout(io.StringIO()):
            self.assertEqual(throttle.main(["feature-drive", "9"]), 0)

    def test_completed_feature_tree_returns_to_normal_once(self):
        throttle.set_mode(self.cfg, throttle.Mode("feature-drive", 7), who="michael")
        parents = {8: 7, 9: 8}
        self.assertFalse(throttle.auto_reset(self.cfg, parents, lambda number: "OPEN" if number == 9 else "CLOSED"))
        self.assertTrue(throttle.auto_reset(self.cfg, parents, lambda _number: "CLOSED"))
        self.assertFalse(throttle.auto_reset(self.cfg, parents, lambda _number: "CLOSED"))
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("normal"))
        rows = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row["event"] == "mode"]
        self.assertEqual(rows[-1]["who"], "auto")

    def test_auto_reset_does_not_overwrite_a_newer_operator_mode(self):
        original = throttle.Mode("feature-drive", 7)
        throttle.set_mode(self.cfg, original, who="michael")

        def closed(_number):
            throttle.set_mode(self.cfg, throttle.Mode("maintenance"), who="michael")
            return "CLOSED"

        self.assertFalse(throttle.auto_reset(self.cfg, {}, closed))
        self.assertEqual(throttle.read(self.cfg), throttle.Mode("maintenance"))
        rows = [row for row in lifecycle.read_events(self.cfg.factory / "events.jsonl") if row["event"] == "mode"]
        self.assertEqual([(row["who"], row["mode"]) for row in rows], [
            ("michael", "feature-drive"),
            ("michael", "maintenance"),
        ])


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.Config(Path("/nonexistent"), REPO, max_active=2, max_attempts=3, review_rounds=3,
                                 manager_rounds=3, budget_min=90)
        self.cfg.throttle_maintenance_label = "maintenance"
        self.cfg.throttle_pace = {"low": 1, "normal": 2, "high": 3}
        self.cfg.throttle_aggression = {
            "low": {"review_rounds": 3, "manager_rounds": 3, "max_attempts": 3, "budget_min": 90},
            "normal": {"review_rounds": 3, "manager_rounds": 3, "max_attempts": 3, "budget_min": 90},
            "high": {"review_rounds": 5, "manager_rounds": 5, "max_attempts": 4, "budget_min": 150},
        }

    def test_normal_mode_preserves_the_existing_plan_output(self):
        rows = [ticket(3, "2026-01-03T00:00:00Z"), ticket(1, "2026-01-01T00:00:00Z", body="unbound")]
        before = schedule.plan(rows, 2, repo=REPO, streak=0, experiments=0)
        after = schedule.plan(rows, 2, repo=REPO, streak=0, experiments=0, mode=throttle.Mode("normal"))
        self.assertEqual(after["selected"], before["selected"])
        self.assertEqual(after["explained"], before["explained"])

    def test_maintenance_holds_nonmaintenance_work_without_mutating_labels(self):
        rows = [
            ticket(1, "2026-01-01T00:00:00Z", body="unbound", labels=("ready-for-agent", "bug")),
            ticket(2, "2026-01-02T00:00:00Z", body="unbound", labels=("ready-for-agent", "chore")),
            ticket(3, "2026-01-03T00:00:00Z", body="unbound", labels=("ready-for-agent", "maintenance")),
            ticket(4, "2026-01-04T00:00:00Z", body="unbound", labels=("ready-for-agent", "enhancement")),
        ]
        original = copy.deepcopy(rows)
        decided = schedule.plan(rows, 4, repo=REPO, streak=0, experiments=0,
                                mode=throttle.Mode("maintenance"), maintenance_label="maintenance")
        self.assertEqual([row["number"] for row in decided["selected"]], [1, 2, 3])
        held = next(row for row in decided["explained"] if row["ticket"] == 4)
        self.assertEqual(held, {"ticket": 4, "decision": "held", "reason": "maintenance mode: not a bug/chore ticket"})
        self.assertEqual(rows, original)

    def test_feature_and_descendant_take_the_reserved_slot_without_advancing_fairness(self):
        now = ticket(3, "2026-01-03T00:00:00Z")
        unknown = ticket(1, "2026-01-01T00:00:00Z", body="unbound")
        feature = ticket(7, "2026-01-04T00:00:00Z", body="unbound")
        decided = schedule.plan([unknown, now, feature], 2, repo=REPO, streak=2, experiments=0,
                                mode=throttle.Mode("feature-drive", 7))
        self.assertEqual([row["number"] for row in decided["selected"]], [7, 3])
        child = ticket(8, "2026-01-05T00:00:00Z", body="unbound")
        decided = schedule.plan([unknown, now, child], 2, repo=REPO, streak=0, experiments=0, parents={8: 7},
                                mode=throttle.Mode("feature-drive", 7))
        self.assertEqual([row["number"] for row in decided["selected"]], [8, 3])

    def test_feature_drive_claims_do_not_advance_the_cross_pass_fairness_streak(self):
        events = [
            {"event": "claimed", "ticket": 3, "priority": "NOW"},
            {"event": "claimed", "ticket": 7, "priority": "NOW", "feature_drive": True},
        ]
        self.assertEqual(schedule.explicit_streak(events), 1)

    def test_mode_capacity_is_clamped_and_only_feature_tree_gets_high_aggression(self):
        self.cfg.manager_max_active_cap = 2
        self.assertEqual(throttle.capacity(self.cfg, throttle.Mode("maintenance")), 1)
        self.cfg.throttle_pace["low"] = 5
        self.assertEqual(throttle.capacity(self.cfg, throttle.Mode("maintenance")), 2)
        parents = {8: 7, 9: 8}
        mode = throttle.Mode("feature-drive", 7)
        self.assertEqual(throttle.limits(self.cfg, mode, 7, parents)["review_rounds"], 5)
        self.assertEqual(throttle.limits(self.cfg, mode, 9, parents)["manager_rounds"], 5)
        self.assertEqual(throttle.limits(self.cfg, mode, 6, parents), self.cfg.throttle_aggression["normal"])

    def test_dispatch_uses_mode_capacity_and_journals_mode_with_each_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), REPO, max_active=4)
            dispatch.configure(cfg)
            throttle.set_mode(cfg, throttle.Mode("maintenance"), who="michael")
            rows = [
                ticket(1, "2026-01-01T00:00:00Z", body="unbound", labels=("ready-for-agent", "bug")),
                ticket(2, "2026-01-02T00:00:00Z", body="unbound", labels=("ready-for-agent", "enhancement")),
            ]
            with mock.patch.object(dispatch, "frontier", return_value=rows), \
                    mock.patch.object(dispatch, "process_ticket") as process:
                dispatch.ticket_pass(cfg.budget_min, False)
            self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [1])
            scheduling = [
                row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                if row.get("event") == "scheduling"
            ]
            self.assertEqual(
                [(row["mode"], row["feature"], row["capacity"]) for row in scheduling],
                [("maintenance", None, 1)],
            )

    def test_scheduled_budget_override_replaces_the_mode_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), REPO, max_active=1, budget_min=90)
            dispatch.configure(cfg)
            rows = [ticket(1, "2026-01-01T00:00:00Z", body="unbound")]
            with mock.patch.object(dispatch, "frontier", return_value=rows), \
                    mock.patch.object(dispatch, "process_ticket") as process:
                dispatch.ticket_pass(17, False, budget_override=True)
        self.assertEqual(process.call_args.args[0]["_factory_limits"]["budget_min"], 17)

    def test_dispatch_cli_forwards_explicit_budget_to_scheduled_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), REPO)
            with mock.patch.object(dispatch.config, "load", return_value=cfg), \
                    mock.patch.object(dispatch, "land_pass"), \
                    mock.patch.object(dispatch, "review_intake_pass"), \
                    mock.patch.object(dispatch, "ticket_pass") as scheduled, \
                    mock.patch("factory.learn.learn_pass"), \
                    mock.patch("factory.manage.manage_pass"):
                self.assertEqual(dispatch.main(["--budget-min", "17", "--dry-run"]), 0)
        scheduled.assert_called_once_with(17, True, budget_override=True)

    def test_only_reserved_feature_claim_is_exempt_from_cross_pass_fairness(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), REPO, max_active=2)
            dispatch.configure(cfg)
            throttle.set_mode(cfg, throttle.Mode("feature-drive", 7), who="michael")
            parent = {
                **PARENT,
                "body": INITIATIVE.replace("NOW #3", "NOW #3 and #4"),
            }
            bound = binding.render(binding.from_issue(cfg, 50, parent))
            rows = [
                ticket(3, "2026-01-01T00:00:00Z", body=bound),
                ticket(4, "2026-01-02T00:00:00Z", body=bound),
                ticket(1, "2026-01-03T00:00:00Z", body="unbound"),
            ]
            for number in (10, 11):
                dispatch.record("claimed", ticket=number, priority="NOW")

            claims = []

            def claim(issue, _budget_min, _dry_run):
                priority, _ = schedule.priority(issue["number"], issue["body"], REPO)
                claims.append((issue["number"], issue["_factory_feature_drive"]))
                dispatch.record(
                    "claimed",
                    ticket=issue["number"],
                    priority=priority,
                    feature_drive=issue["_factory_feature_drive"],
                )

            patches = (
                mock.patch.object(dispatch, "frontier", return_value=rows),
                mock.patch.object(dispatch, "_parents", return_value={3: 7, 4: 7}),
                mock.patch.object(throttle, "auto_reset", return_value=False),
            )
            with patches[0], patches[1], patches[2], mock.patch.object(
                dispatch, "process_ticket", side_effect=claim,
            ):
                dispatch.ticket_pass(cfg.budget_min, False)

            self.assertEqual(claims, [(3, True), (4, False)])
            self.assertEqual(
                schedule.explicit_streak(lifecycle.read_events(cfg.factory / "events.jsonl")),
                3,
            )

            with patches[0], patches[1], patches[2], mock.patch.object(
                dispatch, "process_ticket",
            ) as process:
                dispatch.ticket_pass(cfg.budget_min, False)
            self.assertEqual([call.args[0]["number"] for call in process.call_args_list], [3, 1])


if __name__ == "__main__":
    unittest.main()
