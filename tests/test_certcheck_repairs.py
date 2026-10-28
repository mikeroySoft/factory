from __future__ import annotations

import hashlib
import json
import os
import unittest
from unittest import mock

from factory import certcheck, config, dispatch, watch

try:
    from test_certcheck_regression import MergeCertificateRegressionTest, canonical
except ModuleNotFoundError:
    from tests.test_certcheck_regression import MergeCertificateRegressionTest, canonical


class CertificateRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MergeCertificateRegressionTest(
            "test_approval_head_tamper_fails_and_unjournaled_main_commit_is_uncertified",
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        patcher = mock.patch.dict(os.environ, self.fixture.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rewrite_certificate(self, mutate) -> None:
        document = json.loads(self.fixture.certificate.read_bytes())
        mutate(document)
        data = canonical(document)
        self.fixture.certificate.write_bytes(data)
        self.fixture.events[-1]["certificate_sha256"] = hashlib.sha256(data).hexdigest()
        self.fixture.write_events()

    def stub_github(self, *, pr=None, checks=None) -> None:
        if pr is None:
            pr = {
                "head": {"ref": "agent/7", "sha": self.fixture.head},
                "base": {"ref": "main"},
            }
        if checks is None:
            checks = {"check_runs": [
                {"id": 101, "name": "test", "status": "completed", "conclusion": "success"},
            ]}
        gh = self.fixture.root.parent / "bin" / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            f"pr = json.loads({json.dumps(pr)!r})\n"
            f"checks = json.loads({json.dumps(checks)!r})\n"
            "print(json.dumps(pr if '/pulls/' in sys.argv[2] else checks))\n",
        )
        gh.chmod(0o755)

    def cli_verdict(self, premise: str, kind: str) -> None:
        result = self.fixture.factory(
            "verify-merge", "--certificate", str(self.fixture.certificate), "--json",
            expected=1,
        )
        verdict = json.loads(result.stdout)
        self.assertEqual(verdict["status"], f"failed:{premise}")
        self.assertTrue(verdict["reason"].startswith(f"{kind}:"), verdict)
        self.assertNotIn("Traceback", result.stderr)

    def remote_history(self) -> str:
        self.fixture.git("update-ref", "refs/heads/main", self.fixture.base)
        self.fixture.git("checkout", "-q", "--detach", self.fixture.merge_sha)
        (self.fixture.root / "outside.txt").write_text("outside dispatcher\n")
        self.fixture.git("add", "outside.txt")
        self.fixture.git("commit", "-qm", "outside dispatcher")
        outside = self.fixture.git("rev-parse", "HEAD")
        self.fixture.git("update-ref", "refs/remotes/origin/main", outside)
        return outside

    def test_cli_and_watch_audit_remote_main_with_stale_local_main_and_detached_head(self) -> None:
        outside = self.remote_history()
        self.assertEqual(self.fixture.git("rev-parse", "main"), self.fixture.base)
        self.assertEqual(self.fixture.git("branch", "--show-current"), "")
        approval = next(row for row in self.fixture.events if row.get("event") == "approved")
        approval["head"] = "0" * 40
        self.fixture.write_events()
        result = self.fixture.factory("verify-merge", "--since", "7d", "--json", expected=1)
        audited = json.loads(result.stdout)
        statuses = {row["commit"]: row["status"] for row in audited}
        self.assertEqual(statuses[outside], "uncertified")
        self.assertEqual(statuses[self.fixture.merge_sha], "failed:approval")
        expected = self.fixture.git(
            "log", "--first-parent", "--format=%H", "--since=7 days ago", "origin/main",
        ).splitlines()
        self.assertEqual([row["commit"] for row in audited], expected)
        cfg = config.Config(self.fixture.root, "acme/widgets")
        signals = watch.certificate_signals(cfg, self.fixture.events)
        self.assertEqual([row["key"] for row in signals], ["failed:approval"])
        self.assertIn(self.fixture.merge_sha, signals[0]["excerpt"])
        print(f"fixture CLI audit (exit {result.returncode}): {result.stdout.strip()}")

    def test_missing_remote_main_never_falls_back_to_local_main(self) -> None:
        result = self.fixture.factory("verify-merge", "--since", "7d", "--json", expected=1)
        self.assertIn("missing", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_malformed_pr_response_is_missing_binding_in_cli_and_watch(self) -> None:
        self.remote_history()
        for response in ([], {"head": None, "base": {}}, {"head": [], "base": "main"}):
            with self.subTest(response=response):
                self.stub_github(pr=response)
                self.cli_verdict("binding", "missing")
                cfg = config.Config(self.fixture.root, "acme/widgets")
                signals = watch.certificate_signals(cfg, self.fixture.events)
                self.assertEqual([row["key"] for row in signals], ["failed:binding"])

    def test_malformed_check_runs_are_missing_ci_in_cli_and_watch(self) -> None:
        self.remote_history()
        for response in (
            [], {"check_runs": None}, {"check_runs": [None]},
            {"check_runs": [{"id": 101, "name": "test", "conclusion": []}]},
        ):
            with self.subTest(response=response):
                self.stub_github(checks=response)
                self.cli_verdict("ci", "missing")
                cfg = config.Config(self.fixture.root, "acme/widgets")
                signals = watch.certificate_signals(cfg, self.fixture.events)
                self.assertEqual([row["key"] for row in signals], ["failed:ci"])

    def test_pr_and_certificate_agreeing_on_wrong_base_still_fails_binding(self) -> None:
        self.stub_github(pr={
            "head": {"ref": "agent/7", "sha": self.fixture.head},
            "base": {"ref": "other"},
        })
        self.rewrite_certificate(
            lambda document: document["premises"]["binding"].update(base_ref="other"),
        )
        self.cli_verdict("binding", "forged")

    def test_configured_nondefault_main_is_verified_independently(self) -> None:
        (self.fixture.root / ".factory.toml").write_text(
            '[repo]\nslug = "acme/widgets"\nmain = "trunk"\n',
        )
        self.cli_verdict("binding", "forged")
        self.stub_github(pr={
            "head": {"ref": "agent/7", "sha": self.fixture.head},
            "base": {"ref": "trunk"},
        })
        self.rewrite_certificate(
            lambda document: document["premises"]["binding"].update(base_ref="trunk"),
        )
        result = self.fixture.factory(
            "verify-merge", "--certificate", str(self.fixture.certificate), "--json",
        )
        self.assertEqual(json.loads(result.stdout)["status"], "certified")

    def test_gate_digest_uses_raw_report_bytes_and_survives_copy(self) -> None:
        dispatch.configure(config.Config(self.fixture.root, "acme/widgets"))
        source = self.fixture.root / ".factory" / "raw-gate.md"
        source.write_bytes(b"# Gate\r\n- test: PASS\r\n")
        dispatch._record_gate_decision(
            7, True, source.read_text(), self.fixture.head, str(source), 0.1,
        )
        rows = certcheck.read_journal(dispatch.EVENTS)
        decision = rows[-1]
        self.assertEqual(decision["report_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        gate = next(row for row in rows if row.get("event") == "attempt")
        gate.update(gate_decision_id=decision["decision_id"], report_path=str(source))
        self.fixture.git("update-ref", "refs/remotes/origin/main", self.fixture.base)
        with mock.patch.object(dispatch, "_certificate_checks", return_value=[]):
            _, document = dispatch.write_merge_certificate(
                7, 70, self.fixture.head,
                {"headRefName": "agent/7", "headRefOid": self.fixture.head, "baseRefName": "main"},
                "squash", [], rows,
            )
        gate_premise = document["premises"]["gate"]
        retained = self.fixture.root / gate_premise["report"]
        self.assertEqual(retained.read_bytes(), source.read_bytes())
        self.assertEqual(gate_premise["report_sha256"], decision["report_sha256"])

    def test_missing_report_is_not_replaced_with_an_empty_file(self) -> None:
        dispatch.configure(config.Config(self.fixture.root, "acme/widgets"))
        document = json.loads(self.fixture.certificate.read_bytes())
        retained = self.fixture.root / document["premises"]["gate"]["report"]
        retained.unlink()
        gate = next(row for row in self.fixture.events if row.get("event") == "attempt")
        decision = next(row for row in self.fixture.events if row.get("stage") == "gate")
        gate.update(gate_decision_id=decision["decision_id"], report_path=str(retained))
        decision["head"] = self.fixture.head
        self.fixture.git("update-ref", "refs/remotes/origin/main", self.fixture.base)
        with mock.patch.object(dispatch, "_certificate_checks", return_value=document["premises"]["ci"]["checks"]):
            path, document = dispatch.write_merge_certificate(
                7, 70, self.fixture.head,
                {"headRefName": "agent/7", "headRefOid": self.fixture.head, "baseRefName": "main"},
                "squash", [], self.fixture.events,
            )
        self.assertFalse(retained.exists())
        digest = dispatch.finalize_merge_certificate(path, document, self.fixture.merge_sha)
        self.fixture.events[-1]["certificate_sha256"] = digest
        self.fixture.write_events()
        self.cli_verdict("gate", "missing")


if __name__ == "__main__":
    unittest.main()
