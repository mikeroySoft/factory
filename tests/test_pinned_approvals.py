"""#827: ticket-scoped reviewer receipts authorize only exact reviewed blobs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch
from tests.test_add_only_allow import APPROVED, ROOT, blob, commit, git, repository, run_gate

MARKER = "Factory gate approval: "


class PinnedApprovalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = repository(self.root)
        (self.repo / ".factory.toml").write_text(
            '[gate]\nprotected_paths = [".factory.toml", "mutation-equivalents.json"]\n'
            'add_only_paths = ["tests/**"]\n'
        )
        (self.repo / "mutation-equivalents.json").write_text("[]\n")
        commit(self.repo, "protected baseline")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        (self.repo / "mutation-equivalents.json").write_text('[{"reviewed": true}]\n')
        commit(self.repo, "reviewed edit")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.pin = git(self.repo, "rev-parse", "HEAD:mutation-equivalents.json")
        self.data = {
            "reviewer": "reviewer", "ruling": "r3-0850", "head": self.head,
            "base": self.base, "protected": {"mutation-equivalents.json": self.pin},
            "add_only": {},
        }
        self.comment = {
            "id": 42, "issue_url": "https://api.github.com/repos/acme/widgets/issues/7",
            "user": {"login": "reviewer"}, "author_association": "OWNER",
        }
        self.response = self.root / "response.json"
        bindir = self.root / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(
            f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
            f"c = json.loads(Path({str(self.response)!r}).read_text())\n"
            "print(json.dumps([[c]] if '--paginate' in sys.argv else c))\n"
        )
        gh.chmod(0o755)
        patch = mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]})
        patch.start()
        self.addCleanup(patch.stop)
        self.save()

    def save(self):
        self.comment["body"] = MARKER + json.dumps(self.data)
        self.response.write_text(json.dumps(self.comment))

    def args(self):
        return [
            "--protected-allow", f"mutation-equivalents.json={self.pin}",
            "--approval-ticket", "7", "--approval-comment", "42",
        ]

    def test_reviewed_protected_blob_passes_with_auditable_receipt(self):
        code, report = run_gate(self.repo, *self.args())
        self.assertEqual(code, 0, report)
        for evidence in ("reviewer", "r3-0850", self.head, self.pin, "42"):
            self.assertIn(evidence, report)

    def test_different_committed_blob_is_refused_even_with_current_head_receipt(self):
        (self.repo / "mutation-equivalents.json").write_text('[{"unreviewed": true}]\n')
        commit(self.repo, "unreviewed substitution")
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        code, report = run_gate(self.repo, *self.args())
        self.assertEqual(code, 1, report)
        self.assertIn("protected-paths: FAIL", report)

    def test_allow_cannot_exempt_another_protected_path(self):
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text() + "# unreviewed\n")
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.data["protected"] = {".factory.toml": git(self.repo, "rev-parse", "HEAD:.factory.toml")}
        self.save()
        args = self.args()
        args[1] = ".factory.toml=" + self.data["protected"][".factory.toml"]
        code, report = run_gate(self.repo, *args)
        self.assertNotEqual(code, 0, report)

    def test_missing_or_mismatched_reviewer_evidence_is_refused(self):
        for field, value in (
            ("reviewer", "impostor"), ("ruling", ""), ("head", self.base),
            ("base", self.head), ("protected", {}),
        ):
            with self.subTest(field=field):
                saved = self.data.copy()
                self.data[field] = value
                self.save()
                code, report = run_gate(self.repo, *self.args())
                self.assertNotEqual(code, 0, report)
                self.data = saved
        self.save()
        code, report = run_gate(self.repo, *self.args()[:2])
        self.assertNotEqual(code, 0, report)
        for key, value in (("author_association", "NONE"), ("issue_url", "https://api.github.com/repos/acme/widgets/issues/8")):
            with self.subTest(key=key):
                old = self.comment[key]
                self.comment[key] = value
                self.save()
                code, report = run_gate(self.repo, *self.args())
                self.assertNotEqual(code, 0, report)
                self.comment[key] = old

    def test_protected_receipt_does_not_bypass_existing_test_check(self):
        path = self.repo / "tests/test_other.py"
        path.write_text(path.read_text() + "    assert 2\n")
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        code, report = run_gate(self.repo, *self.args())
        self.assertEqual(code, 1, report)
        self.assertIn("protected-tests: FAIL", report)

    def test_dirty_protected_blob_is_refused(self):
        (self.repo / "mutation-equivalents.json").write_text("[]\n")
        code, report = run_gate(self.repo, *self.args())
        self.assertEqual(code, 1, report)
        self.assertIn("protected-paths: FAIL", report)

    def dispatch_gate(self, *, label=False):
        dispatch.configure(config.load(self.repo))
        seen = []

        def run(cmd, cwd=None, check=True, env=None, **kwargs):
            if cmd[:4] == [sys.executable, "-P", "-E", "-m"]:
                seen.append(cmd)
                cmd = [sys.executable, *cmd[3:]]
            return subprocess.run(
                cmd, cwd=cwd, check=check, capture_output=True, text=True,
                env={**(env or os.environ), "PYTHONPATH": str(ROOT)}, **kwargs,
            )

        flags = dispatch.gate_flags(
            None, {"bug"} | ({config.LABEL_ADD_ONLY_OVERRIDE} if label else set()),
            body="No repro test: approval fixture", comments=[{"body": self.comment["body"]}],
        )
        with mock.patch.object(dispatch, "run", side_effect=run), mock.patch.object(dispatch, "_record_gate_decision"):
            ok, report = dispatch.run_gate(self.repo, 7, **flags)
        return ok, report, seen

    def test_dispatch_automatically_passes_current_reviewed_allow(self):
        ok, report, commands = self.dispatch_gate()
        self.assertTrue(ok, report)
        self.assertIn("--protected-allow", commands[-1])
        self.assertNotIn("--protected-override", commands[-1])

    def test_dispatch_discovers_receipt_posted_after_ticket_snapshot(self):
        flags = dispatch.gate_flags(
            None, {"bug"}, body="No repro test: approval fixture", comments=[],
        )
        with mock.patch.object(dispatch, "gate_flags", return_value=flags):
            ok, report, commands = self.dispatch_gate()
        self.assertTrue(ok, report)
        self.assertIn("--protected-allow", commands[-1])

    def test_dispatch_blanket_overrides_do_not_mix_with_receipts(self):
        for labels, body, option, expected_ok in (
            ({config.LABEL_PROTECTED_OVERRIDE}, "", "--protected-override", True),
            (set(), "factory-protected-override: reviewed test change", "--test-override", False),
        ):
            with self.subTest(option=option):
                flags = dispatch.gate_flags(
                    None, {"bug"} | labels,
                    body=body + "\nNo repro test: approval fixture",
                    comments=[{"body": self.comment["body"]}],
                )
                with mock.patch.object(dispatch, "gate_flags", return_value=flags):
                    ok, report, commands = self.dispatch_gate()
                self.assertEqual(ok, expected_ok, report)
                self.assertIn(option, commands[-1])
                self.assertNotIn("--approval-comment", commands[-1])
                self.assertNotIn("gate runner did not write", report)
                if not expected_ok:
                    self.assertIn("protected-paths: FAIL", report)

    def test_dispatch_stale_approval_cannot_authorize_new_head(self):
        (self.repo / "new.txt").write_text("new head\n")
        commit(self.repo)
        ok, report, commands = self.dispatch_gate()
        self.assertFalse(ok, report)
        self.assertNotIn("--protected-allow", commands[-1])

    def test_dispatch_pins_add_only_edits_and_preserves_other_tests(self):
        (self.repo / "tests/test_old.py").write_text(APPROVED)
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.data["add_only"] = {"tests/test_old.py": blob(self.repo, APPROVED)}
        self.save()
        ok, report, commands = self.dispatch_gate(label=True)
        self.assertTrue(ok, report)
        self.assertIn("--add-only-allow", commands[-1])
        self.assertIn("--add-only-allow-base", commands[-1])
        self.assertNotIn("--add-only-override", commands[-1])
        (self.repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n    assert 2\n")
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        ok, report, commands = self.dispatch_gate(label=True)
        self.assertFalse(ok, report)
        self.assertIn("protected-tests: FAIL", report)
        (self.repo / "tests/test_old.py").write_text(APPROVED + "# drift\n")
        commit(self.repo)
        ok, report, commands = self.dispatch_gate(label=True)
        self.assertFalse(ok, report)
        self.assertNotIn("--add-only-override", commands[-1])

    def test_add_only_label_without_receipt_is_not_blanket_authority(self):
        (self.repo / "mutation-equivalents.json").write_text("[]\n")
        (self.repo / "tests/test_old.py").write_text(APPROVED)
        commit(self.repo)
        self.comment["body"] = ""
        self.response.write_text("{}")
        ok, report, commands = self.dispatch_gate(label=True)
        self.assertFalse(ok, report)
        self.assertIn("protected-paths: FAIL", report)
        self.assertIn("tests/test_old.py", report)
        self.assertNotIn("--add-only-override", commands[-1])


if __name__ == "__main__":
    unittest.main()
