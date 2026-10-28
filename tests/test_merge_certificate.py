from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import certcheck, config, dispatch, lifecycle, watch

try:
    from test_certcheck_regression import MergeCertificateRegressionTest
    from test_factory import build_fork, git, merge_stage_mocks
except ModuleNotFoundError:
    from tests.test_certcheck_regression import MergeCertificateRegressionTest
    from tests.test_factory import build_fork, git, merge_stage_mocks


class MergeCertificateTest(unittest.TestCase):
    def test_certificate_is_repeatable_and_gate_report_survives_cleanup(self) -> None:
        fixture = MergeCertificateRegressionTest(
            "test_approval_head_tamper_fails_and_unjournaled_main_commit_is_uncertified",
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        env = mock.patch.dict(os.environ, fixture.env)
        env.start()
        self.addCleanup(env.stop)

        fixture.events[:] = [row for row in fixture.events if row.get("event") != "merged"]
        gate = next(row for row in fixture.events if row.get("event") == "attempt")
        decision = next(
            row for row in fixture.events
            if row.get("event") == "decision" and row.get("stage") == "gate"
        )
        gate.update(
            gate_decision_id=decision["decision_id"],
            report_sha256=decision["report_sha256"],
        )
        decision["head"] = fixture.head
        fixture.git("update-ref", "refs/remotes/origin/main", fixture.base)
        wt = fixture.root / ".factory" / "wt-7"
        fixture.git("worktree", "add", "-q", str(wt), "agent/7")
        report = wt / ".factory" / "gate-report-7.md"
        report.parent.mkdir()
        report.write_text("# Gate\n- test: PASS\n")
        gate["report_path"] = str(report)
        decision["report_path"] = str(report)
        fixture.write_events()

        dispatch.configure(config.Config(fixture.root, "acme/widgets", signoff=False))
        final = {
            "headRefName": "agent/7", "headRefOid": fixture.head,
            "baseRefName": "main",
        }
        checks = [{"name": "test", "bucket": "pass"}]
        check_runs = {
            "check_runs": [
                {"id": 101, "name": "test", "status": "completed", "conclusion": "success"},
            ],
        }
        with mock.patch.object(dispatch, "gh_json", return_value=check_runs):
            path, document = dispatch.write_merge_certificate(
                7, 70, fixture.head, final, "squash", checks,
                lifecycle.read_events(dispatch.EVENTS),
            )
            first = path.read_bytes()
            second_path, second_document = dispatch.write_merge_certificate(
                7, 70, fixture.head, final, "squash", checks,
                lifecycle.read_events(dispatch.EVENTS),
            )
        self.assertEqual((path, document, first), (second_path, second_document, second_path.read_bytes()))

        digest = dispatch.finalize_merge_certificate(path, document, fixture.merge_sha)
        lifecycle.append(dispatch.EVENTS, {
            "at": "2026-10-27T10:03:00Z", "event": "merged", "ticket": 7, "pr": 70,
            "method": "squash", "head": fixture.head, "merge_sha": fixture.merge_sha,
            "certificate": str(path.relative_to(fixture.root)), "certificate_sha256": digest,
        })
        with mock.patch.object(dispatch, "retain_handoff", return_value=True):
            dispatch.cleanup_after_merge(7, fixture.head)

        retained = fixture.root / ".factory" / "certificates" / f"7-{fixture.head}.gate.md"
        self.assertFalse(wt.exists())
        self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), decision["report_sha256"])
        verdict = certcheck.verify_file(
            path, fixture.root, "acme/widgets",
            certcheck.read_journal(dispatch.EVENTS),
        )
        self.assertEqual(verdict.status, "certified", verdict.reason)

    def test_upstream_sync_records_real_gate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _origin, upstream = build_fork(Path(directory))
            (upstream / "upstream.txt").write_text("upstream\n")
            git(upstream, "add", "upstream.txt")
            git(upstream, "commit", "-qm", "upstream")
            cfg = config.Config(root, "acme/widgets", upstream="upstream", main="main")
            with mock.patch.object(config, "remote_slug", return_value="acme/upstream-widgets"):
                dispatch.configure(cfg)
            real_run = dispatch.run

            def run(cmd, **kwargs):
                if cmd[0] == "gh":
                    return subprocess.CompletedProcess(
                        cmd, 0, "https://github.com/acme/widgets/issues/42\n", "",
                    )
                return real_run(cmd, **kwargs)

            report_text = "# Gate\n- test: PASS\n"
            with mock.patch.object(dispatch, "gh_json", return_value=[]), \
                    mock.patch.object(dispatch, "run", side_effect=run), \
                    mock.patch.object(dispatch, "run_gate", return_value=(True, report_text)):
                dispatch.sync_pass(False)

            rows = lifecycle.read_events(dispatch.EVENTS)
            attempt = next(row for row in rows if row.get("event") == "attempt")
            decision = next(row for row in rows if row.get("stage") == "gate")
            report = Path(attempt["report_path"])
            self.assertEqual(attempt["gate_decision_id"], decision["decision_id"])
            self.assertEqual(report.read_text(), report_text)
            self.assertEqual(attempt["report_sha256"], hashlib.sha256(report.read_bytes()).hexdigest())

    def merge_case(self, certify: str, checker_code: int) -> tuple[list[list[str]], mock.Mock, config.Config]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root, origin, _ = build_fork(Path(temporary.name))
        git(origin, "checkout", "-qb", "agent/7")
        (origin / "feature.txt").write_text("feature")
        git(origin, "add", "feature.txt")
        git(origin, "commit", "-qm", "feature")
        git(origin, "checkout", "-q", "main")
        cfg = config.Config(root, "acme/widgets", signoff=False, merge_certify=certify)
        dispatch.configure(cfg)
        fake_gh_json, fake_merge_run = merge_stage_mocks(
            origin, 70, "agent/7", "feature", dispatch.run,
        )
        head = git(origin, "rev-parse", "agent/7")
        report = cfg.factory / "gate-report-7.md"
        report.write_text("# Gate report\n\n- test: PASS\n")
        dispatch._record_gate_decision(7, True, report.read_text(), head, str(report), 0)
        dispatch.record(
            "attempt", ticket=7, gate="PASS", head=head, actual_head=head,
            **dispatch._PENDING_GATES.pop(7),
        )
        dispatch.record(
            "review", ticket=7, verdict="APPROVE", accepted=True,
            head=head, actual_head=head,
        )
        dispatch._record_review_decision(
            7, "APPROVE", "approved", head, True, True, None, 0,
            comment_url="https://github.com/acme/widgets/pull/70#issuecomment-1",
        )
        dispatch.record(
            "approved", ticket=7, pr=70, head=head, gate_head=head,
            review_head=head, source="dispatch", actor="factory",
        )
        certificate = cfg.factory / "candidate.json"
        certificate.parent.mkdir(exist_ok=True)
        certificate.write_text("{}\n")
        stub = cfg.factory / "wt-7" / "factory" / "certcheck.py"
        stub.parent.mkdir(parents=True)
        stub.write_text("raise SystemExit(0)\n")
        calls: list[list[str]] = []

        def run(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[:6] == [dispatch.sys.executable, "-P", "-E", "-m", "factory", "verify-merge"]:
                return subprocess.CompletedProcess(cmd, checker_code, '{"status":"failed:gate"}\n', "")
            if cmd[:3] == ["gh", "pr", "edit"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_merge_run(cmd, *args, **kwargs)

        escalate = mock.Mock()
        with mock.patch.object(dispatch, "gh_json", side_effect=fake_gh_json), \
                mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "ci", "bucket": "pass"}]), \
                mock.patch.object(dispatch, "write_merge_certificate", return_value=(certificate, {})), \
                mock.patch.object(dispatch, "finalize_merge_certificate", return_value="a" * 64), \
                mock.patch.object(dispatch, "cleanup_after_merge"), \
                mock.patch.object(dispatch, "run", side_effect=run), \
                mock.patch.object(dispatch, "escalate", escalate):
            dispatch.merge_pass_locked(False)
        return calls, escalate, cfg

    def test_enforce_uses_installed_checker_and_stops_merge(self) -> None:
        calls, escalate, _cfg = self.merge_case("enforce", 1)
        checker = next(call for call in calls if "verify-merge" in call)
        self.assertEqual(checker[:6], [dispatch.sys.executable, "-P", "-E", "-m", "factory", "verify-merge"])
        self.assertFalse(any(call[:3] == ["gh", "pr", "merge"] for call in calls))
        self.assertTrue(any("--remove-label" in call for call in calls))
        self.assertEqual(escalate.call_args.kwargs["code"], "certificate_failed")

    def test_audit_mode_does_not_run_checker_or_change_merge_path(self) -> None:
        calls, escalate, cfg = self.merge_case("audit", 1)
        self.assertFalse(any("verify-merge" in call for call in calls))
        self.assertTrue(any(call[:3] == ["gh", "pr", "merge"] for call in calls))
        escalate.assert_not_called()
        merged = next(row for row in lifecycle.read_events(cfg.factory / "events.jsonl")
                      if row.get("event") == "merged")
        self.assertEqual(merged["certificate_sha256"], "a" * 64)
        self.assertEqual(merged["certificate"], ".factory/candidate.json")

    def test_merge_certify_config_defaults_to_audit_and_accepts_enforce(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _origin, _upstream = build_fork(Path(directory))
            (root / ".factory.toml").write_text('[repo]\nslug = "acme/widgets"\n')
            self.assertEqual(config.load(root).merge_certify, "audit")
            (root / ".factory.toml").write_text(
                '[repo]\nslug = "acme/widgets"\n[merge]\ncertify = "enforce"\n',
            )
            self.assertEqual(config.load(root).merge_certify, "enforce")
            (root / ".factory.toml").write_text(
                '[repo]\nslug = "acme/widgets"\n[merge]\ncertify = "maybe"\n',
            )
            with self.assertRaises(config.ConfigError):
                config.load(root)


class CertificateWatchTest(unittest.TestCase):
    def test_failed_audit_becomes_one_watch_bug_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets")
            merged = {
                "at": "2026-10-27T10:03:00+00:00", "event": "merged",
                "ticket": 7, "merge_sha": "a" * 40,
            }
            failure = {
                "commit": "a" * 40, "status": "failed:gate",
                "reason": "forged: retained gate report digest differs",
            }
            with mock.patch.object(certcheck, "audit", return_value=[failure]), \
                    mock.patch.object(watch, "failed_units", return_value=[]), \
                    mock.patch.object(watch, "runtime_errors", return_value=[]):
                signals = watch.signals(cfg, [merged])
        self.assertEqual(len(signals), 1)
        self.assertEqual(
            (signals[0]["fingerprint"], signals[0]["kind"], signals[0]["key"]),
            ("certificate:gate", "certificate", "failed:gate"),
        )
        self.assertEqual(signals[0]["command"], "factory verify-merge --since 7d")


if __name__ == "__main__":
    unittest.main()
