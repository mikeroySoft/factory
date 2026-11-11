"""#731: landing must not consume another host's journal or refresh worktree."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle
from tests import test_host_config as host_fixture
from tests import test_refresh_conflict as refresh_fixture

git = refresh_fixture.git


class LandHostConfigTest(unittest.TestCase):
    def test_host_layer_override_and_doctor(self):
        host = {"defaults": {"host_name": "ultron", "land_host": "default"},
                "repo": {"acme/widgets": {"land_host": "tank"}}}
        with tempfile.TemporaryDirectory() as directory:
            cfg = host_fixture.HostConfigTest.load(self, Path(directory), host)
            self.assertEqual(cfg.land_host, "tank")
            rows = host_fixture.HostConfigTest.doctor_rows(self, cfg, host)
        self.assertEqual(rows["land_host"], {"label": "land_host", "status": "INFO", "detail": "tank"})

    def test_invalid_land_host_is_rejected(self):
        for value in ("", "tank two", "-tank", "a" * 46, 1, False):
            with (self.subTest(value=value), tempfile.TemporaryDirectory() as directory,
                  self.assertRaisesRegex(config.ConfigError, "land_host")):
                host_fixture.HostConfigTest.load(self, Path(directory), {"defaults": {"land_host": value}})

    def test_land_host_is_host_only(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(config.ConfigError, "host-only"):
            host_fixture.HostConfigTest.load(self, Path(directory), {}, 'land_host = "tank"\n[repo]\nslug = "acme/widgets"\n')


class LandHostTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.enterContext(mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}))
        self.root, self.origin = refresh_fixture.build_fork(self.tmp)
        git(self.origin, "checkout", "-qb", "agent/9")
        (self.origin / "feature.txt").write_text("feature\n")
        git(self.origin, "add", "feature.txt")
        git(self.origin, "commit", "-qm", "agent change")
        self.old_head = git(self.origin, "rev-parse", "HEAD")
        git(self.origin, "checkout", "-q", "main")
        self.github = refresh_fixture.FakeGitHub(self.origin, dispatch.run)
        self.comments = []
        self.commands = {}
        self.reads = {}
        # Promotion has a separate contract; these fixtures contain ticket PRs only.
        self.enterContext(mock.patch("factory.learn.promote_pass"))

    def dispatcher(self, host):
        key = host or "single"
        root = self.tmp / key
        git(self.tmp, "clone", "-q", str(self.origin), str(root))
        git(root, "config", "user.name", key)
        git(root, "config", "user.email", f"{key}@example.com")
        spec = importlib.util.spec_from_file_location(f"factory._land_{key}", dispatch.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = config.Config(root=root, repo="acme/widgets", host_name=host,
                            reviewer=[sys.executable, "-c", "print('```json\\n{\"verdict\":\"APPROVE\",\"findings\":[]}\\n```\\nVERDICT: APPROVE')"])
        cfg.land_host = "tank"
        module.configure(cfg)
        self.commands[key], self.reads[key] = [], []

        def run(cmd, *args, **kwargs):
            self.commands[key].append(cmd)
            if cmd[:2] == ["git", "rebase"]:
                # Different hosts deterministically produce different refreshed commits.
                kwargs["env"] = {**os.environ, "GIT_COMMITTER_DATE":
                                 "2026-11-11T12:00:00+00:00" if key == "tank" else "2026-11-11T12:00:01+00:00"}
            return self.github.run(cmd, *args, **kwargs)

        def gh_json(args):
            self.reads[key].append(args)
            if args == ["api", "user"]:
                return {"login": "factory"}
            if args[:3] == ["api", "--paginate", "--slurp"]:
                return [self.comments.copy()]
            return self.github.gh_json(args)

        self.enterContext(mock.patch.object(module, "run", side_effect=run))
        self.enterContext(mock.patch.object(module, "gh_json", side_effect=gh_json))
        return module

    def claim(self, host, *, age=0):
        at = datetime.now(UTC) - timedelta(seconds=age)
        self.comments.append({"id": len(self.comments) + 1, "created_at": at.isoformat(),
                              "user": {"login": "factory"},
                              "body": f"Factory claim: host={host} nonce={host}-nonce at={at.isoformat()}"})

    def approve(self, module):
        head = self.github.head()
        module.record("attempt", ticket=9, head=head, actual_head=head, gate="PASS")
        module.record("review", ticket=9, head=head, actual_head=head, accepted=True,
                      verdict="APPROVE", role="primary")
        self.assertTrue(module.approve_pr(9, head))
        return head

    def events(self, module, event):
        return [row for row in lifecycle.read_events(module.EVENTS) if row.get("event") == event]

    def assert_untouched(self, module):
        self.assertIn(config.LABEL_APPROVED, self.github.pr_labels)
        self.assertNotIn(config.LABEL_HUMAN, self.github.issue_labels)
        self.assertEqual(self.events(module, "escalate"), [])
        self.assertFalse(self.github.merged)

    def test_c1_non_land_host_preserves_foreign_approval_without_local_journal(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(tank)
        self.assertFalse(ultron.EVENTS.exists())
        ultron.land_pass(False)
        self.assert_untouched(ultron)
        self.assertEqual(self.commands["ultron"], [])
        self.assertEqual(self.reads["ultron"], [])

    def test_non_land_host_skips_sync_and_dry_run_without_git_or_github(self):
        ultron = self.dispatcher("ultron")
        # A real upstream makes a missed entry guard observable even without PRs.
        git(ultron.ROOT, "remote", "add", "upstream", str(self.origin))
        ultron.UPSTREAM = "upstream"
        self.github.merged = True
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                ultron.land_pass(dry_run)
        self.assertEqual(self.commands["ultron"], [])
        self.assertEqual(self.reads["ultron"], [])
        self.assertFalse(ultron.FACTORY.exists())

    def test_c1_merge_stage_skips_foreign_elected_claim_before_approval_check(self):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(ultron)
        self.claim("ultron", age=10)
        self.claim("tank")  # A losing local claim must not authorize landing.
        tank.merge_pass_locked(False)
        self.assert_untouched(tank)
        self.assertEqual(self.commands["tank"], [])

    def test_expired_foreign_elected_claim_is_still_skipped(self):
        tank = self.dispatcher("tank")
        self.claim("ultron", age=3 * 3600)
        tank.merge_pass_locked(False)
        self.assert_untouched(tank)
        self.assertEqual(self.commands["tank"], [])

    def move_main(self):
        (self.origin / "main.txt").write_text("main moved\n")
        git(self.origin, "add", "main.txt")
        git(self.origin, "commit", "-qm", "main change")

    def refresh_interleaving(self, *, stale_worktree):
        tank, ultron = self.dispatcher("tank"), self.dispatcher("ultron")
        self.approve(tank)
        if stale_worktree:
            wt = ultron.ensure_worktree(9)
            (wt / "stale.txt").write_text("old generation\n")
            git(wt, "add", "stale.txt")
            git(wt, "commit", "-qm", "kept worktree from old generation")
        self.commands["ultron"].clear()
        self.reads["ultron"].clear()
        self.move_main()
        gates = []

        snapshot = self.github.pr()
        tank_json = tank.gh_json.side_effect
        observed = set()

        def preobserved_pr(args):
            # Both hosts read an approved PR before either withdraws its label.
            kind = "list" if args[:2] == ["pr", "list"] else (
                "view" if args[:2] == ["pr", "view"] and "headRefName" in args[-1] else None
            )
            if kind and kind not in observed:
                observed.add(kind)
                return [snapshot] if kind == "list" else snapshot
            return tank_json(args)

        def tank_gate(*_args, **_kwargs):
            gates.append("tank")
            return True, "gate passed"

        def ultron_gate(*_args, **_kwargs):
            gates.append("ultron")
            tank.land_pass(False)  # Winner pushes and re-approves before the loser resumes.
            return True, "gate passed"

        with mock.patch.object(tank, "run_gate", side_effect=tank_gate), \
                mock.patch.object(ultron, "run_gate", side_effect=ultron_gate), \
                mock.patch.object(tank, "gh_json", side_effect=preobserved_pr):
            ultron.land_pass(False)
            if not self.events(tank, "refreshed"):
                tank.land_pass(False)  # The non-land host skipped, or its stale worktree withdrew.
        self.assertEqual(self.events(tank, "escalate") + self.events(ultron, "escalate"), [])
        self.assertEqual(gates, ["tank"])
        self.assertEqual(self.commands["ultron"], [])
        self.assertEqual(self.reads["ultron"], [])
        self.assert_untouched(tank)
        refreshed = self.github.head()
        self.assertNotEqual(refreshed, self.old_head)
        self.assertEqual(git(self.origin, "merge-base", "main", refreshed), git(self.origin, "rev-parse", "main"))
        self.assertEqual([row["head"] for row in self.events(tank, "approved")], [self.old_head, refreshed])
        self.assertEqual([row["head"] for row in self.events(tank, "review")], [self.old_head, refreshed])
        tank.land_pass(False)
        self.assertTrue(self.github.merged)

    def test_c2_dual_refresh_does_not_escalate_force_with_lease_loser(self):
        self.refresh_interleaving(stale_worktree=False)

    def test_c2_stale_foreign_worktree_does_not_escalate_refresh_head(self):
        self.refresh_interleaving(stale_worktree=True)

    def test_land_host_merges_own_approved_green_current_head(self):
        tank = self.dispatcher("tank")
        self.claim("tank", age=10)
        self.claim("ultron")  # Only the elected claim matters.
        head = self.approve(tank)
        tank.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertEqual([row["head"] for row in self.events(tank, "merged")], [head])

    def test_foreign_claimed_pr_does_not_end_merge_pass_before_own_pr(self):
        tank = self.dispatcher("tank")
        self.claim("ultron")
        foreign_claims = self.comments.copy()
        self.comments.clear()
        self.claim("tank")
        head = self.approve(tank)
        tank_json = tank.gh_json.side_effect

        def two_prs(args):
            if args[:2] == ["pr", "list"]:
                # PR #101 (ticket 8, elected by ultron) sorts before tank's PR #102.
                return [*tank_json(args), {**self.github.pr(), "number": 101, "headRefName": "agent/8"}]
            if args[:3] == ["api", "--paginate", "--slurp"] and "/issues/8/" in args[3]:
                self.reads["tank"].append(args)
                return [foreign_claims]
            return tank_json(args)

        with mock.patch.object(tank, "gh_json", side_effect=two_prs), \
                mock.patch.object(tank, "pr_checks", wraps=tank.pr_checks) as checks:
            tank.merge_pass_locked(False)
        self.assertEqual({call.args[0] for call in checks.call_args_list}, {102})
        self.assertTrue(self.github.merged)
        self.assertEqual([(row["pr"], row["head"]) for row in self.events(tank, "merged")], [(102, head)])
        self.assertEqual(self.events(tank, "escalate"), [])
        self.assertFalse(any("101" in cmd or "agent/8" in cmd or "origin/agent/8" in cmd
                             for cmd in self.commands["tank"]))
        foreign_reads = [args for args in self.reads["tank"]
                         if "101" in args or any(arg == "8" or "/issues/8/" in arg for arg in args)]
        self.assertEqual(foreign_reads, [["api", "--paginate", "--slurp", "repos/acme/widgets/issues/8/comments"]])

    def test_single_host_without_host_name_still_lands(self):
        single = self.dispatcher(None)
        # land_host, like triage_host, is ignored when host_name is absent.
        self.claim("ultron")
        self.approve(single)
        single.land_pass(False)
        self.assertTrue(self.github.merged)
        self.assertFalse(any(args[:1] == ["api"] and "--paginate" in args for args in self.reads["single"]))

    def test_unset_land_host_preserves_landing(self):
        tank = self.dispatcher("tank")
        tank.cfg.land_host = None
        self.approve(tank)
        tank.land_pass(False)
        self.assertTrue(self.github.merged)


if __name__ == "__main__":
    unittest.main()
