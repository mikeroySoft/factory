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

    def test_valid_receipt_does_not_exempt_unapproved_protected_edit(self):
        path = self.repo / ".factory.toml"
        path.write_text(path.read_text() + "# unapproved protected edit\n")
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        ok, report, commands = self.dispatch_gate()
        self.assertFalse(ok, report)
        self.assertIn("--protected-allow", commands[-1])
        self.assertIn("protected-paths: FAIL", report)
        self.assertIn(".factory.toml", report)

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
            ({config.LABEL_ADD_ONLY_OVERRIDE}, "", "--add-only-override", False),
        ):
            with self.subTest(option=option):
                flags = dispatch.gate_flags(
                    None, {"bug"} | labels,
                    body=body + "\nNo repro test: approval fixture",
                    comments=[{"body": self.comment["body"]}],
                )
                with mock.patch.object(dispatch, "gate_flags", return_value=flags), \
                        mock.patch.object(dispatch, "gh_json", side_effect=AssertionError("receipt lookup")):
                    ok, report, commands = self.dispatch_gate()
                self.assertEqual(ok, expected_ok, report)
                self.assertIn(option, commands[-1])
                self.assertNotIn("--approval-comment", commands[-1])
                self.assertNotIn("gate runner did not write", report)
                if not expected_ok:
                    self.assertIn("protected-paths: FAIL", report)

    def test_authorize_requeue_does_not_open_an_owner_generation(self):
        from factory import generation

        release = generation.owner_requeue([
            {"event": "escalate", "ticket": 7, "reason": "gate failed"},
            {"event": "manage", "ticket": 7, "decision": "AUTHORIZE"},
        ], 7)
        self.assertIsNone(release)

    def test_authorize_requeue_does_not_require_a_terminal_human_handoff(self):
        from factory import handoff

        cfg = config.Config(self.repo, "acme/widgets", manager=["manager"])
        escalation = {"event": "escalate", "ticket": 7, "round": 1}
        reason = handoff.terminal(cfg, [
            escalation,
            {"event": "manage", "ticket": 7, "round": 1, "decision": "AUTHORIZE"},
        ], escalation)
        self.assertIsNone(reason)

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
        ok, report, commands = self.dispatch_gate()
        self.assertTrue(ok, report)
        self.assertIn("--add-only-allow", commands[-1])
        self.assertIn("--add-only-allow-base", commands[-1])
        self.assertNotIn("--add-only-override", commands[-1])
        (self.repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n    assert 2\n")
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        ok, report, commands = self.dispatch_gate()
        self.assertFalse(ok, report)
        self.assertIn("protected-tests: FAIL", report)
        (self.repo / "tests/test_old.py").write_text(APPROVED + "# drift\n")
        commit(self.repo)
        ok, report, commands = self.dispatch_gate()
        self.assertFalse(ok, report)
        self.assertNotIn("--add-only-override", commands[-1])

    def test_add_only_label_preserves_explicit_override_without_receipt(self):
        (self.repo / "mutation-equivalents.json").write_text("[]\n")
        (self.repo / "tests/test_old.py").write_text(APPROVED)
        commit(self.repo)
        self.comment["body"] = ""
        self.response.write_text("{}")
        ok, report, commands = self.dispatch_gate(label=True)
        self.assertTrue(ok, report)
        self.assertIn("--add-only-override", commands[-1])
        self.assertNotIn("--approval-comment", commands[-1])

    def test_dispatch_ignores_newer_unrelated_or_stale_comments(self):
        for newer in (
            {"body": "ordinary discussion"},
            {**self.comment, "body": MARKER + json.dumps({**self.data, "head": "0" * 40})},
        ):
            with self.subTest(newer=newer):
                with mock.patch.object(dispatch, "gh_json", return_value=[[self.comment], [newer]]):
                    ok, report, commands = self.dispatch_gate()
                self.assertTrue(ok, report)
                self.assertIn("--approval-comment", commands[-1])

    def test_dispatch_refuses_malformed_comment_pages(self):
        for pages in (1, [1], {"unexpected": []}):
            with self.subTest(pages=pages):
                with mock.patch.object(dispatch, "gh_json", return_value=pages):
                    ok, report, commands = self.dispatch_gate()
                self.assertFalse(ok, report)
                self.assertIn("protected-paths: FAIL", report)
                self.assertNotIn("--approval-comment", commands[-1])

    def test_receipt_cli_requires_positive_ticket_and_comment(self):
        for ticket, comment in ((None, "42"), ("7", None), ("0", "42"), ("7", "0"),
                                ("-1", "42"), ("7", "-1")):
            with self.subTest(ticket=ticket, comment=comment):
                args = self.args()[:2]
                if ticket is not None:
                    args += ["--approval-ticket", ticket]
                if comment is not None:
                    args += ["--approval-comment", comment]
                code, report = run_gate(self.repo, *args)
                self.assertEqual(code, 2, report)
                self.assertIn("must both be positive", report)

    def test_receipt_cli_rejects_each_blanket_override(self):
        for override in ("--protected-override", "--test-override", "--add-only-override"):
            with self.subTest(override=override):
                code, report = run_gate(self.repo, *self.args(), override)
                self.assertEqual(code, 2, report)
                self.assertIn("cannot be combined with blanket overrides", report)

    def test_duplicate_protected_allow_is_refused(self):
        code, report = run_gate(self.repo, *self.args(), *self.args()[:2])
        self.assertEqual(code, 2, report)
        self.assertIn("--protected-allow names a path more than once", report)

    def test_large_comment_id_matches_by_value_and_not_identity(self):
        self.comment["id"] = 1042
        self.save()
        args = self.args()
        args[-1] = "1042"
        code, report = run_gate(self.repo, *args)
        self.assertEqual(code, 0, report)
        self.comment["id"] = 1043
        self.save()
        code, report = run_gate(self.repo, *args)
        self.assertEqual(code, 1, report)
        self.assertIn("approval receipt does not match supplied allows", report)

    def test_receipt_add_only_base_must_be_the_recorded_commit_not_an_alias(self):
        (self.repo / "tests/test_old.py").write_text(APPROVED)
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.data["add_only"] = {"tests/test_old.py": blob(self.repo, APPROVED)}
        self.save()
        args = [*self.args(), "--add-only-allow", f"tests/test_old.py={blob(self.repo, APPROVED)}",
                "--add-only-allow-base"]
        code, report = run_gate(self.repo, *args, self.base)
        self.assertEqual(code, 0, report)
        code, report = run_gate(self.repo, *args, "origin/main")
        self.assertEqual(code, 1, report)
        self.assertIn("approval receipt does not match supplied allows", report)

    def test_protected_allow_requires_in_place_regular_file_edit(self):
        from contextlib import chdir

        from factory import gate

        path = "mutation-equivalents.json"
        allow = {path: self.pin}
        with chdir(self.repo):
            raw = {path: ("100644", "100644", "M")}
            self.assertEqual(gate._verify_protected_allow(allow, raw), "")
            for modes in (("000000", "100644", "A"), ("100644", "000000", "D"),
                          ("100644", "100755", "M"), ("120000", "100644", "M")):
                with self.subTest(modes=modes):
                    self.assertIn("not an in-place", gate._verify_protected_allow(allow, {path: modes}))
            for other in (".factory.toml", "z-protected.json"):
                with self.subTest(path=other):
                    self.assertIn("outside reviewed protected scope", gate._verify_protected_allow(
                        {other: self.pin}, {other: raw[path]},
                    ))
            target = self.repo / path
            for mode in (0o641, 0o654, 0o744):
                target.chmod(mode)
                self.assertIn("working tree is not", gate._verify_protected_allow(allow, raw))
            target.chmod(0o666)
            self.assertEqual(gate._verify_protected_allow(allow, raw), "")
            target.unlink()
            self.assertIn("working tree is not", gate._verify_protected_allow(allow, raw))
            reviewed = self.root / "reviewed.json"
            reviewed.write_text('[{"reviewed": true}]\n')
            target.symlink_to(reviewed)
            self.assertIn("working tree is not", gate._verify_protected_allow(allow, raw))

    def test_receipt_transport_errors_fail_closed_with_report(self):
        from contextlib import chdir

        from factory import gate

        timed = gate.timed

        def unavailable(command, **kwargs):
            if command[:2] == ["gh", "api"]:
                raise OSError("approval transport unavailable")
            return timed(command, **kwargs)

        with chdir(self.repo), mock.patch.multiple(
            gate, cfg=None, GPU_LOCK=None, LEAK_RE=None, GPU_CHECKS=set(),
            CHECK_TIMEOUT=1200, create=True,
        ), mock.patch.object(gate, "timed", side_effect=unavailable):
            code = gate.main(["--bug", "--no-repro-test", "fixture", *self.args()])
        report = (self.repo / ".factory/gate-report.md").read_text()
        self.assertEqual(code, 1, report)
        self.assertIn("reviewed approval refused: approval transport unavailable", report)


    def test_dispatch_does_not_use_attempt_receipts_for_other_tiers_or_named_runs(self):
        dispatch.configure(config.load(self.repo))
        with mock.patch.object(dispatch, "gh_json", side_effect=AssertionError("receipt lookup")):
            for ticket, tier in ((7, "pr"), (7, "release"), ("7-pr", "attempt")):
                with self.subTest(ticket=ticket, tier=tier):
                    self.assertEqual(dispatch._reviewed_gate_flags(
                        self.repo, ticket, self.head, self.base, tier, False, False,
                    ), [])

    def test_dispatch_receipt_transport_failures_leave_protection_enforced(self):
        for error in (OSError("offline"), ValueError("invalid JSON"),
                      subprocess.CalledProcessError(1, ["gh", "api"])):
            with self.subTest(error=error):
                with mock.patch.object(dispatch, "gh_json", side_effect=error):
                    ok, report, commands = self.dispatch_gate()
                self.assertFalse(ok, report)
                self.assertIn("protected-paths: FAIL", report)
                self.assertNotIn("--approval-comment", commands[-1])

    def test_dispatch_refuses_receipt_when_base_has_no_common_ancestor(self):
        dispatch.configure(config.load(self.repo))
        unrelated = git(self.repo, "commit-tree", "HEAD^{tree}", stdin="unrelated root\n")
        with mock.patch.object(dispatch, "gh_json", return_value=[[self.comment]]):
            self.assertEqual(dispatch._reviewed_gate_flags(
                self.repo, 7, self.head, unrelated, "attempt", False, False,
            ), [])

    def test_receipt_accepts_smallest_positive_ticket_and_comment_ids(self):
        for ticket, comment in ((1, 42), (7, 1)):
            with self.subTest(ticket=ticket, comment=comment):
                self.comment["id"] = comment
                self.comment["issue_url"] = f"https://api.github.com/repos/acme/widgets/issues/{ticket}"
                self.save()
                args = [*self.args()[:2], "--approval-ticket", str(ticket),
                        "--approval-comment", str(comment)]
                code, report = run_gate(self.repo, *args)
                self.assertEqual(code, 0, report)

    def test_receipt_requires_complete_add_only_map_and_full_base_pin(self):
        self.data["add_only"] = {"tests/test_old.py": blob(self.repo, APPROVED)}
        self.save()
        code, report = run_gate(self.repo, *self.args(), "--add-only-allow-base", self.base)
        self.assertEqual(code, 1, report)
        self.assertIn("approval receipt does not match supplied allows", report)
        (self.repo / "tests/test_old.py").write_text(APPROVED)
        commit(self.repo)
        self.data["head"] = git(self.repo, "rev-parse", "HEAD")
        self.save()
        code, report = run_gate(
            self.repo, *self.args(), "--add-only-allow", f"tests/test_old.py={blob(self.repo, APPROVED)}",
            "--add-only-allow-base", self.base[:12],
        )
        self.assertEqual(code, 1, report)
        self.assertIn("approval receipt does not match supplied allows", report)

    def test_protected_pin_checks_committed_blob_even_when_worktree_matches_approval(self):
        from contextlib import chdir

        from factory import gate

        path = "mutation-equivalents.json"
        reviewed = (self.repo / path).read_text()
        for greater in (False, True):
            content = next(
                f"unreviewed {n}\n" for n in range(256)
                if (blob(self.repo, f"unreviewed {n}\n") > self.pin) == greater
            )
            (self.repo / path).write_text(content)
            commit(self.repo)
            committed = git(self.repo, "rev-parse", f"HEAD:{path}")
            (self.repo / path).write_text(reviewed)
            with chdir(self.repo):
                refusal = gate._verify_protected_allow(
                    {path: self.pin}, {path: ("100644", "100644", "M")},
                )
            self.assertIn(f"approved {self.pin}, committed {committed}, working tree {self.pin}", refusal)


class ApprovalReceiptValidationTest(unittest.TestCase):
    def test_receipt_boundary_rejects_malformed_or_unpinned_authority(self):
        from copy import deepcopy

        from factory import gate

        head, base, pin = "b" * 40, "c" * 40, "d" * 40
        data = {
            "reviewer": "Reviewer", "ruling": "r3-0850", "head": head, "base": base,
            "protected": {"mutation-equivalents.json": pin}, "add_only": {},
        }
        comment = {
            "id": 1042, "issue_url": "https://api.github.com/repos/acme/widgets/issues/7",
            "user": {"login": "reviewer"}, "author_association": "OWNER",
            "body": MARKER + json.dumps(data),
        }

        def validate(envelope):
            return gate.reviewed_approval(envelope, "acme/widgets", 7, head, base)

        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            self.assertEqual(validate({**comment, "author_association": association}), data)
        self.assertEqual(validate({**comment, "id": 1}), data)
        add_only = {**data, "protected": {}, "add_only": {"tests/test_old.py": pin}}
        self.assertEqual(validate({**comment, "body": MARKER + json.dumps(add_only)}), add_only)

        for field, value in (
            ("id", -1), ("id", 0), ("id", True), ("id", "1042"), ("id", None),
            ("user", None), ("user", {}), ("user", {"login": ""}), ("user", {"login": 1}),
            ("author_association", "NONE"), ("issue_url", "https://api.github.com/repos/acme/widgets/issues/8"),
            ("body", None), ("body", "discussion"), ("body", MARKER + "{"),
        ):
            with self.subTest(envelope_field=field, value=value), self.assertRaises((TypeError, ValueError)):
                validate({**comment, field: value})
        for malformed in (None, [], "", 1):
            with self.subTest(envelope=malformed), self.assertRaises((TypeError, ValueError)):
                validate(malformed)
        variants = [[], {}, {**data, "extra": True}, {k: v for k, v in data.items() if k != "ruling"}]
        for field, values in (
            ("reviewer", (None, "", "impostor")),
            ("ruling", (None, "", "ruling with spaces", "x" * 129)),
            ("head", ("a" * 40, "f" * 40)),
            ("base", ("a" * 40, "f" * 40)),
            ("protected", ([], {"a-protected.json": pin}, {"z-protected.json": pin},
                           {"mutation-equivalents.json": 1}, {"mutation-equivalents.json": "bad"})),
            ("add_only", ([], {"../tests/test_old.py": pin}, {"tests//test_old.py": pin},
                          {"tests/test_old.py": "bad"})),
        ):
            variants.extend({**data, field: value} for value in values)
        variants.append({**data, "protected": {}})
        for invalid in variants:
            with self.subTest(receipt=invalid):
                envelope = deepcopy(comment)
                envelope["body"] = MARKER + json.dumps(invalid)
                with self.assertRaises((TypeError, ValueError)):
                    validate(envelope)


if __name__ == "__main__":
    unittest.main()
