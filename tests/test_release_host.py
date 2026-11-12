"""#723: release_host and triage_host gates for review-intake, manager and learn."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, manage
from tests import test_factory
from tests import test_host_config as host_fixture


class ReleaseHostConfigTest(unittest.TestCase):
    def test_host_layer_override_and_doctor(self) -> None:
        host = {
            "defaults": {"host_name": "ultron", "release_host": "default"},
            "repo": {"acme/widgets": {"release_host": "tank"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            cfg = host_fixture.HostConfigTest.load(self, Path(directory), host)
            self.assertEqual(cfg.release_host, "tank")
            rows = host_fixture.HostConfigTest.doctor_rows(self, cfg, host)
        self.assertEqual(
            rows["release_host"],
            {"label": "release_host", "status": "INFO", "detail": "tank"},
        )

    def test_invalid_release_host_is_rejected(self) -> None:
        for value in ("", "tank two", "-tank", "a" * 46, 1, False):
            with (
                self.subTest(value=value),
                tempfile.TemporaryDirectory() as directory,
                self.assertRaisesRegex(config.ConfigError, "release_host"),
            ):
                host_fixture.HostConfigTest.load(
                    self, Path(directory), {"defaults": {"release_host": value}}
                )

    def test_release_host_is_host_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            config.ConfigError, "host-only"
        ):
            host_fixture.HostConfigTest.load(
                self,
                Path(directory),
                {},
                'release_host = "tank"\n[repo]\nslug = "acme/widgets"\n',
            )


class HostGatedLoopsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.repo = test_factory.make_repo(Path(temp.name))
        self.cfg = config.load(self.repo)
        self.cfg.manager = ["true"]
        dispatch.configure(self.cfg)
        self.logs: list[str] = []
        self.enterContext(
            mock.patch.object(dispatch, "log", side_effect=lambda msg: self.logs.append(msg))
        )

    def _fail_github(self, *args, **kwargs):
        self.fail(f"unexpected GitHub or run call: args={args!r} kwargs={kwargs!r}")

    def test_non_release_host_skips_release_without_git_or_github(self) -> None:
        self.cfg.release_auto = True
        self.cfg.host_name = "ultron"
        self.cfg.release_host = "tank"
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "land_pass"),
            mock.patch.object(dispatch, "review_intake_pass"),
            mock.patch.object(dispatch, "frontier", return_value=[]),
            mock.patch.object(manage, "manage_pass"),
            mock.patch.object(learn, "learn_pass"),
            mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github),
            mock.patch.object(dispatch, "run", side_effect=self._fail_github),
            mock.patch("factory.release.run") as release_run,
        ):
            self.assertEqual(dispatch.main([]), 0)
        release_run.assert_not_called()
        self.assertTrue(any("release: skipped, release host is tank" in msg for msg in self.logs))

    def test_release_host_runs_and_single_host_path_is_unchanged(self) -> None:
        self.cfg.release_auto = True
        for host_name, release_host in (("tank", "tank"), (None, "tank"), (None, None)):
            with self.subTest(host_name=host_name, release_host=release_host):
                self.cfg.host_name = host_name
                self.cfg.release_host = release_host
                self.logs.clear()
                with (
                    mock.patch.object(config, "load", return_value=self.cfg),
                    mock.patch.object(dispatch, "pin_root", return_value=[]),
                    mock.patch.object(dispatch, "land_pass"),
                    mock.patch.object(dispatch, "review_intake_pass"),
                    mock.patch.object(dispatch, "frontier", return_value=[]),
                    mock.patch.object(manage, "manage_pass"),
                    mock.patch.object(learn, "learn_pass"),
                    mock.patch("factory.release.run", return_value="ok") as release_run,
                ):
                    self.assertEqual(dispatch.main([]), 0)
                self.assertEqual(release_run.call_count, 2)  # start and end of the pass
                self.assertFalse(any("release: skipped" in msg for msg in self.logs))

    def test_non_triage_host_skips_review_intake_without_github(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_not_called()
        self.assertEqual(self.logs, ["review-intake: skipped, triage host is tank"])

    def test_triage_host_reviews_intake(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with mock.patch.object(dispatch, "gh_json", return_value=[]) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_called()
        self.assertFalse(any("review-intake: skipped" in msg for msg in self.logs))

    def test_non_triage_host_skips_viability_and_frontier_but_still_runs_escalations_and_handoffs(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_not_called()
        frontier.assert_not_called()
        escalation.assert_called_once_with(False)
        handoff_pass.assert_called_once_with(False)
        self.assertEqual(self.logs, ["manage: viability and frontier skipped, triage host is tank"])

    def _escalation(self, *, journal: bool) -> Path:
        state = self.repo / ".factory"
        (state / "escalations").mkdir(parents=True)
        packet = state / "escalations/7.md"
        packet.write_text("gate failed")
        row = {"event": "escalate", "ticket": 7, "at": "2026-01-01T00:00:00Z", "round": 1, "packet": str(packet)}
        receipt = {"event": "comment", "ticket": 7, "at": row["at"], "kind": "escalation", "round": 1, "comment": 100}
        (state / "events.jsonl").write_text(
            json.dumps(row) + "\n" + json.dumps(receipt) + "\n" if journal else ""
        )
        return packet

    def _run_manage_pass(self, order: list[str]) -> mock.Mock:
        self.cfg.manager = ["printf", "DECISION: HUMAN\nneeds a person"]
        issues = [{"number": 7, "title": "Fix gate", "body": "Original body"}]
        with (
            mock.patch.object(dispatch, "gh_json", return_value=issues),
            mock.patch.object(manage, "human_activity", return_value=False),
            mock.patch.object(manage, "viability_pass", side_effect=lambda _: order.append("viability")),
            mock.patch.object(manage, "frontier_pass", side_effect=lambda _: order.append("frontier")),
            mock.patch.object(manage, "apply", side_effect=lambda *a: order.append("round")) as apply,
            mock.patch.object(manage.handoff, "handoff_pass", side_effect=lambda _: order.append("handoff")),
        ):
            manage.manage_pass(False)
        return apply

    def test_non_triage_host_gives_its_own_escalation_a_round_then_handoff(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        self._escalation(journal=True)
        order: list[str] = []
        apply = self._run_manage_pass(order)
        apply.assert_called_once()
        self.assertEqual(apply.call_args.args[2], "HUMAN")
        self.assertEqual(order, ["round", "handoff"])

    def test_non_triage_host_still_skips_viability_and_frontier(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        self._escalation(journal=True)
        order: list[str] = []
        self._run_manage_pass(order)
        self.assertNotIn("viability", order)
        self.assertNotIn("frontier", order)
        self.cfg.host_name = "tank"
        order.clear()
        self._run_manage_pass(order)
        self.assertEqual(order[:2], ["viability", "frontier"])

    def test_escalation_only_in_the_other_hosts_journal_gets_no_round(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        self._escalation(journal=False)
        order: list[str] = []
        apply = self._run_manage_pass(order)
        apply.assert_not_called()
        self.assertEqual(order, ["handoff"])

    def test_triage_host_runs_manager(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_called_once_with(False)
        frontier.assert_called_once_with(False)
        escalation.assert_called_once_with(False)
        handoff_pass.assert_called_once_with(False)

    def test_non_triage_host_skips_learn_without_side_effects(self) -> None:
        self.cfg.host_name = "ultron"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_not_called()
        auto_learn.assert_not_called()
        decide.assert_not_called()
        self.assertEqual(self.logs, ["learn: skipped, triage host is tank"])

    def test_triage_host_runs_learn(self) -> None:
        self.cfg.host_name = "tank"
        self.cfg.triage_host = "tank"
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_called_once()
        auto_learn.assert_called_once()
        decide.assert_called_once()

    def test_single_host_without_host_name_still_runs_gated_loops(self) -> None:
        self.cfg.host_name = None
        self.cfg.triage_host = "tank"
        self.cfg.release_host = "tank"
        with mock.patch.object(dispatch, "gh_json", return_value=[]) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_called()
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage.handoff, "handoff_pass"),
            mock.patch.object(manage, "frontier_pass"),
            mock.patch.object(manage, "escalation_pass"),
        ):
            manage.manage_pass(False)
        viability.assert_called_once()
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn"),
            mock.patch.object(learn, "decide_promotions"),
        ):
            learn.learn_pass(False)
        score.assert_called_once()

    def _runtime_name(self, value: str) -> str:
        built = json.loads(json.dumps(value))
        self.assertEqual(built, value)
        self.assertIsNot(built, value)
        return built

    def test_review_intake_gate_kills_value_and_identity_mutants(self) -> None:
        below, above = self._runtime_name("tank"), self._runtime_name("ultron")
        self.assertLess(below, above)
        self.cfg.host_name = below
        self.cfg.triage_host = above
        with mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_not_called()
        self.assertEqual(self.logs, ["review-intake: skipped, triage host is ultron"])

        host, triage = self._runtime_name("tank"), self._runtime_name("tank")
        self.assertEqual(host, triage)
        self.assertIsNot(host, triage)
        self.cfg.host_name = host
        self.cfg.triage_host = triage
        self.logs.clear()
        with mock.patch.object(dispatch, "gh_json", return_value=[]) as gh:
            dispatch.review_intake_pass(False)
        gh.assert_called()
        self.assertFalse(any("review-intake: skipped" in msg for msg in self.logs))

    def test_release_step_gate_kills_value_and_identity_mutants(self) -> None:
        self.cfg.release_auto = True
        below, above = self._runtime_name("tank"), self._runtime_name("ultron")
        self.assertLess(below, above)
        self.cfg.host_name = below
        self.cfg.release_host = above
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "land_pass"),
            mock.patch.object(dispatch, "review_intake_pass"),
            mock.patch.object(dispatch, "frontier", return_value=[]),
            mock.patch.object(manage, "manage_pass"),
            mock.patch.object(learn, "learn_pass"),
            mock.patch.object(dispatch, "gh_json", side_effect=self._fail_github),
            mock.patch.object(dispatch, "run", side_effect=self._fail_github),
            mock.patch("factory.release.run") as release_run,
        ):
            self.assertEqual(dispatch.main([]), 0)
        release_run.assert_not_called()
        self.assertTrue(any("release: skipped, release host is ultron" in msg for msg in self.logs))

        host, release_host = self._runtime_name("tank"), self._runtime_name("tank")
        self.assertEqual(host, release_host)
        self.assertIsNot(host, release_host)
        self.cfg.host_name = host
        self.cfg.release_host = release_host
        self.logs.clear()
        with (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(dispatch, "pin_root", return_value=[]),
            mock.patch.object(dispatch, "land_pass"),
            mock.patch.object(dispatch, "review_intake_pass"),
            mock.patch.object(dispatch, "frontier", return_value=[]),
            mock.patch.object(manage, "manage_pass"),
            mock.patch.object(learn, "learn_pass"),
            mock.patch("factory.release.run", return_value="ok") as release_run,
        ):
            self.assertEqual(dispatch.main([]), 0)
        self.assertEqual(release_run.call_count, 2)
        self.assertFalse(any("release: skipped" in msg for msg in self.logs))

    def test_manage_pass_gate_kills_value_and_identity_mutants(self) -> None:
        below, above = self._runtime_name("tank"), self._runtime_name("ultron")
        self.assertLess(below, above)
        self.cfg.host_name = below
        self.cfg.triage_host = above
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_not_called()
        frontier.assert_not_called()
        escalation.assert_called_once_with(False)
        handoff_pass.assert_called_once_with(False)
        self.assertEqual(self.logs, ["manage: viability and frontier skipped, triage host is ultron"])

        host, triage = self._runtime_name("tank"), self._runtime_name("tank")
        self.assertEqual(host, triage)
        self.assertIsNot(host, triage)
        self.cfg.host_name = host
        self.cfg.triage_host = triage
        self.logs.clear()
        with (
            mock.patch.object(manage, "viability_pass") as viability,
            mock.patch.object(manage, "frontier_pass") as frontier,
            mock.patch.object(manage, "escalation_pass") as escalation,
            mock.patch.object(manage.handoff, "handoff_pass") as handoff_pass,
        ):
            manage.manage_pass(False)
        viability.assert_called_once_with(False)
        frontier.assert_called_once_with(False)
        escalation.assert_called_once_with(False)
        handoff_pass.assert_called_once_with(False)
        self.assertFalse(any("viability and frontier skipped" in msg for msg in self.logs))

    def test_learn_pass_gate_kills_value_and_identity_mutants(self) -> None:
        below, above = self._runtime_name("tank"), self._runtime_name("ultron")
        self.assertLess(below, above)
        self.cfg.host_name = below
        self.cfg.triage_host = above
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_not_called()
        auto_learn.assert_not_called()
        decide.assert_not_called()
        self.assertEqual(self.logs, ["learn: skipped, triage host is ultron"])

        host, triage = self._runtime_name("tank"), self._runtime_name("tank")
        self.assertEqual(host, triage)
        self.assertIsNot(host, triage)
        self.cfg.host_name = host
        self.cfg.triage_host = triage
        self.logs.clear()
        with (
            mock.patch.object(learn.score, "run") as score,
            mock.patch.object(learn, "auto_learn") as auto_learn,
            mock.patch.object(learn, "decide_promotions") as decide,
        ):
            learn.learn_pass(False)
        score.assert_called_once()
        auto_learn.assert_called_once()
        decide.assert_called_once()
        self.assertFalse(any("learn: skipped" in msg for msg in self.logs))



if __name__ == "__main__":
    unittest.main()
