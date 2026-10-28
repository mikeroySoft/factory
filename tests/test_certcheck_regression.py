from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from factory import __version__

ROOT = Path(__file__).resolve().parents[1]


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


class MergeCertificateRegressionTest(unittest.TestCase):
    """A dispatcher-shaped journal detects approval drift and an unjournaled main commit."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "XDG_CONFIG_HOME": str(Path(self.temporary.name) / "config"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        self.env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Certificate Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.root / ".factory.toml").write_text('[repo]\nslug = "acme/widgets"\n')
        (self.root / "base.txt").write_text("base\n")
        self.git("add", ".factory.toml", "base.txt")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")

        self.git("checkout", "-qb", "agent/7")
        (self.root / "feature.txt").write_text("feature\n")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "feature")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--squash", "agent/7")
        self.git("commit", "-qm", "feature (#7)")
        self.merge_sha = self.git("rev-parse", "HEAD")

        factory = self.root / ".factory"
        self.certificates = factory / "certificates"
        self.certificates.mkdir(parents=True)
        report = self.certificates / f"7-{self.head}.gate.md"
        report.write_text("# Gate\n- test: PASS\n")
        report_digest = hashlib.sha256(report.read_bytes()).hexdigest()
        config_digest = hashlib.sha256((self.root / ".factory.toml").read_bytes()).hexdigest()
        gate_id, review_id = str(uuid.uuid4()), str(uuid.uuid4())
        gate_row = {
            "at": "2026-10-27T10:00:00Z", "event": "attempt", "ticket": 7,
            "gate": "PASS", "head": self.head, "actual_head": self.head,
        }
        review_row = {
            "at": "2026-10-27T10:01:00Z", "event": "review", "ticket": 7,
            "verdict": "APPROVE", "accepted": True, "head": self.head,
            "actual_head": self.head,
        }
        approval_row = {
            "at": "2026-10-27T10:02:00Z", "event": "approved", "ticket": 7,
            "pr": 70, "head": self.head, "gate_head": self.head,
            "review_head": self.head, "source": "dispatch", "actor": "factory",
        }
        self.events = [
            {
                "at": "2026-10-27T09:59:59Z", "event": "decision", "ticket": 7,
                "stage": "gate", "decision_id": gate_id, "report_sha256": report_digest,
                "report_path": str(report),
            },
            gate_row,
            review_row,
            {
                "at": "2026-10-27T10:01:30Z", "event": "decision", "ticket": 7,
                "pr": 70, "stage": "review", "decision_id": review_id,
                "head": self.head, "comment_url": "https://github.com/acme/widgets/pull/70#issuecomment-1",
            },
            approval_row,
        ]
        certificate = {
            "schema_version": 1,
            "repository": "acme/widgets",
            "ticket": 7,
            "pr": 70,
            "head": self.head,
            "merge_sha": self.merge_sha,
            "premises": {
                "binding": {
                    "ticket": 7, "pr": 70, "head_ref": "agent/7", "base_ref": "main",
                    "base_sha": self.base, "merge_method": "squash",
                },
                "head": {"approved": self.head, "pr": self.head, "matched": self.head},
                "gate": {
                    "row": gate_row, "decision_id": gate_id,
                    "report": f".factory/certificates/7-{self.head}.gate.md",
                    "report_sha256": report_digest,
                },
                "review": {
                    "row": review_row, "decision_id": review_id,
                    "comment_url": "https://github.com/acme/widgets/pull/70#issuecomment-1",
                },
                "approval": {"row": approval_row},
                "manager": {"policy": "escalated", "row": None},
                "ci": {
                    "head": self.head,
                    "checks": [{"id": 101, "name": "test", "conclusion": "success"}],
                },
                "freshness": {"behind_by": 0, "method": "squash"},
                "eval": None,
                "human_decision": None,
                "risk_tier": None,
                "policy": {
                    "base_sha": self.base,
                    "config_sha256": config_digest,
                    "manager_review": "escalated",
                    "review_policy": {"path": "REVIEW.md", "sha256": None},
                    "dispatcher_version": __version__,
                    "installed_commit": None,
                },
            },
        }
        self.certificate = self.certificates / f"7-{self.head}.json"
        self.certificate.write_bytes(canonical(certificate))
        certificate_digest = hashlib.sha256(self.certificate.read_bytes()).hexdigest()
        self.events.append({
            "at": "2026-10-27T10:03:00Z", "event": "merged", "ticket": 7, "pr": 70,
            "method": "squash", "head": self.head, "merge_sha": self.merge_sha,
            "certificate_sha256": certificate_digest,
            "certificate": f".factory/certificates/7-{self.head}.json",
        })
        self.write_events()
        self.install_gh_stub()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def write_events(self) -> None:
        path = self.root / ".factory" / "events.jsonl"
        path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in self.events))

    def install_gh_stub(self) -> None:
        bindir = Path(self.temporary.name) / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "a = sys.argv[1:]\n"
            "head = os.environ['CERT_HEAD']; merge = os.environ['CERT_MERGE']\n"
            "if a[:2] == ['api', 'repos/acme/widgets/pulls/70']:\n"
            " print(json.dumps({'number':70,'head':{'ref':'agent/7','sha':head},'base':{'ref':'main'},'merge_commit_sha':merge}))\n"
            "elif a[:2] == ['api', f'repos/acme/widgets/commits/{head}/check-runs?per_page=100']:\n"
            " print(json.dumps({'check_runs':[{'id':101,'name':'test','status':'completed','conclusion':'success'}]}))\n"
            "else:\n"
            " raise SystemExit('unexpected gh call: ' + repr(a))\n"
        )
        gh.chmod(0o755)
        self.env.update({
            "PATH": str(bindir) + os.pathsep + self.env["PATH"],
            "CERT_HEAD": self.head,
            "CERT_MERGE": self.merge_sha,
        })

    def factory(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "factory", *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_approval_head_tamper_fails_and_unjournaled_main_commit_is_uncertified(self) -> None:
        valid = self.factory("verify-merge", "--certificate", str(self.certificate), "--json")
        self.assertEqual(json.loads(valid.stdout)["status"], "certified")

        approval = next(row for row in self.events if row.get("event") == "approved")
        approval["head"] = "0" * 40
        self.write_events()
        tampered = self.factory(
            "verify-merge", "--certificate", str(self.certificate), "--json", expected=1,
        )
        self.assertEqual(json.loads(tampered.stdout)["status"], "failed:approval")
        self.assertIn("stale", json.loads(tampered.stdout)["reason"])

        approval["head"] = self.head
        self.write_events()
        (self.root / "hand.txt").write_text("outside dispatcher\n")
        self.git("add", "hand.txt")
        self.git("commit", "-qm", "hand merge")
        outside = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", outside)
        audit = json.loads(self.factory("verify-merge", "--since", "7d", "--json").stdout)
        self.assertEqual(next(row["status"] for row in audit if row["commit"] == outside), "uncertified")


if __name__ == "__main__":
    unittest.main()
