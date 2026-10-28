from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from factory import certcheck

try:
    from test_certcheck_regression import MergeCertificateRegressionTest, canonical
except ModuleNotFoundError:
    from tests.test_certcheck_regression import MergeCertificateRegressionTest, canonical


class CertificateTamperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MergeCertificateRegressionTest(
            "test_approval_head_tamper_fails_and_unjournaled_main_commit_is_uncertified",
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        patcher = mock.patch.dict(os.environ, self.fixture.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    @property
    def events(self) -> list[dict]:
        return self.fixture.events

    def document(self) -> dict:
        return json.loads(self.fixture.certificate.read_text())

    def rewrite_certificate(self, mutate) -> None:
        document = self.document()
        mutate(document)
        data = canonical(document)
        self.fixture.certificate.write_bytes(data)
        merged = next(row for row in self.events if row.get("event") == "merged")
        merged["certificate_sha256"] = hashlib.sha256(data).hexdigest()
        self.fixture.write_events()

    def verdict(self, *, human_rule=certcheck.human_decision_required) -> certcheck.Verdict:
        rows = certcheck.read_journal(self.fixture.root / ".factory" / "events.jsonl")
        return certcheck.verify_file(
            self.fixture.certificate,
            self.fixture.root,
            "acme/widgets",
            rows,
            human_rule=human_rule,
        )

    def assert_premise(self, premise: str) -> None:
        verdict = self.verdict()
        self.assertEqual(verdict.status, f"failed:{premise}", verdict.reason)

    def test_gate_fail_names_gate(self) -> None:
        next(row for row in self.events if row.get("event") == "attempt")["gate"] = "FAIL"
        self.fixture.write_events()
        self.assert_premise("gate")

    def test_missing_review_names_review(self) -> None:
        self.events[:] = [row for row in self.events if row.get("event") != "review"]
        self.fixture.write_events()
        self.assert_premise("review")

    def test_review_actual_head_drift_names_review(self) -> None:
        next(row for row in self.events if row.get("event") == "review")["actual_head"] = "0" * 40
        self.fixture.write_events()
        self.assert_premise("review")

    def test_approval_before_review_names_approval(self) -> None:
        approval = next(row for row in self.events if row.get("event") == "approved")
        self.events.remove(approval)
        review_index = next(i for i, row in enumerate(self.events) if row.get("event") == "review")
        self.events.insert(review_index, approval)
        self.fixture.write_events()
        self.assert_premise("approval")

    def test_failing_check_names_ci(self) -> None:
        gh = Path(self.fixture.env["PATH"].split(os.pathsep, 1)[0]) / "gh"
        gh.write_text(gh.read_text().replace("'conclusion':'success'", "'conclusion':'failure'"))
        gh.chmod(0o755)
        self.assert_premise("ci")

    def test_different_squash_tree_names_freshness(self) -> None:
        (self.fixture.root / "different.txt").write_text("different\n")
        self.fixture.git("add", "different.txt")
        self.fixture.git("commit", "-qm", "different squash")
        wrong_merge = self.fixture.git("rev-parse", "HEAD")
        self.fixture.env["CERT_MERGE"] = wrong_merge

        def mutate(document: dict) -> None:
            document["merge_sha"] = wrong_merge

        self.rewrite_certificate(mutate)
        next(row for row in self.events if row.get("event") == "merged")["merge_sha"] = wrong_merge
        self.fixture.write_events()
        self.assert_premise("freshness")

    def test_wrong_report_digest_names_gate(self) -> None:
        self.rewrite_certificate(
            lambda document: document["premises"]["gate"].update(report_sha256="0" * 64),
        )
        self.assert_premise("gate")

    def test_wrong_policy_digest_names_policy(self) -> None:
        self.rewrite_certificate(
            lambda document: document["premises"]["policy"].update(config_sha256="0" * 64),
        )
        self.assert_premise("policy")

    def test_historical_policy_ignores_current_install_claims(self) -> None:
        host = Path(self.fixture.env["XDG_CONFIG_HOME"]) / "factory" / "config.toml"
        host.parent.mkdir(parents=True)
        host.write_text('[defaults.manager]\nreview = "all"\n')
        with mock.patch.object(certcheck, "_version", return_value="next"), \
                mock.patch.object(certcheck, "_installed_commit", return_value="f" * 40):
            verdict = self.verdict()
        self.assertEqual(verdict.status, "certified", verdict.reason)

    def test_manager_all_without_approve_names_manager(self) -> None:
        def mutate(document: dict) -> None:
            document["premises"]["manager"] = {"policy": "all", "row": None}
            document["premises"]["policy"]["manager_review"] = "all"

        self.rewrite_certificate(mutate)
        self.assert_premise("manager")

    def test_rotated_journal_and_optional_null_premises_verify(self) -> None:
        journal = self.fixture.root / ".factory" / "events.jsonl"
        with gzip.open(journal.with_name("events.jsonl.1.gz"), "wt", encoding="utf-8") as handle:
            handle.write(journal.read_text())
        journal.write_text("")
        self.assertEqual(self.verdict().status, "certified")

    def test_audit_classifies_every_first_parent_commit(self) -> None:
        (self.fixture.root / "outside.txt").write_text("outside\n")
        self.fixture.git("add", "outside.txt")
        environment = {
            **self.fixture.env,
            "GIT_AUTHOR_DATE": "2026-10-28T12:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-10-28T12:00:00+00:00",
        }
        subprocess.run(
            ["git", "commit", "-qm", "outside dispatcher"],
            cwd=self.fixture.root, env=environment, check=True,
        )
        outside = self.fixture.git("rev-parse", "HEAD")
        rows = certcheck.read_journal(self.fixture.root / ".factory" / "events.jsonl")
        results = certcheck.audit(
            self.fixture.root, "acme/widgets", rows, "main", "2026-10-01T00:00:00+00:00",
        )
        commits = self.fixture.git("log", "--first-parent", "--format=%H", "main").splitlines()
        self.assertEqual([row["commit"] for row in results], commits)
        self.assertEqual(next(row["status"] for row in results if row["commit"] == self.fixture.merge_sha), "certified")
        self.assertEqual(next(row["status"] for row in results if row["commit"] == outside), "uncertified")
        self.assertTrue(all(
            row["status"] in {"certified", "legacy", "uncertified"}
            or row["status"].startswith("failed:")
            for row in results
        ))

    def test_future_human_decision_rule_fails_closed(self) -> None:
        verdict = self.verdict(human_rule=lambda _certificate: True)
        self.assertEqual(verdict.status, "failed:human_decision")
        self.assertIn("missing", verdict.reason or "")


if __name__ == "__main__":
    unittest.main()
