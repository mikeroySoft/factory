from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, learn, lifecycle, onboard


class PromoteRegressionTest(unittest.TestCase):
    BRANCH = "agent/lessons-2026-10-27-t10"
    HEAD = "a" * 40

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cfg = config.Config(root=Path(temp.name), repo="acme/widgets", manager=["true"])
        self.cfg.factory.mkdir()
        self.events = self.cfg.factory / "events.jsonl"
        self.pr = {
            "number": 42,
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
            "baseRefName": "main",
            "isDraft": False,
            "reviewDecision": "",
        }
        self.compare = {"behind_by": 0, "files": [{"filename": config.LESSONS_NAME}]}
        self.run = self.enterContext(mock.patch.object(
            dispatch,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ))
        self.enterContext(mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "gate", "bucket": "pass"}]))
        self.enterContext(mock.patch.object(dispatch, "log"))
        self.enterContext(mock.patch.multiple(
            dispatch,
            create=True,
            cfg=self.cfg,
            EVENTS=self.events,
            ROOT=self.cfg.root,
            REPO=self.cfg.repo,
        ))
        self.gh = self.enterContext(mock.patch.object(
            dispatch,
            "gh_json",
            side_effect=lambda args: self.compare if args[0] == "api" else {},
        ))

    def journal(self, *rows: dict) -> None:
        with self.events.open("a") as handle:
            for row in rows:
                handle.write(json.dumps({"at": "2026-10-27T00:00:00Z", **row}) + "\n")

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(self.events) if row.get("event") == event]

    def review(self) -> dict:
        return {"event": "review-result", "pr": 42, "head": self.HEAD, "verdict": "APPROVE"}

    def gate(self) -> dict:
        return {"event": "gate", "branch": self.BRANCH, "head": self.HEAD, "gate": "PASS"}

    def test_promote_gates_an_approved_legacy_head_once_then_merges(self) -> None:
        self.journal(self.review())

        def pass_gate(_wt: Path, branch: str, _protected: bool) -> str:
            dispatch.record("gate", branch=branch, head=self.HEAD, gate="PASS")
            return self.HEAD

        with mock.patch.object(learn, "gate_head", side_effect=pass_gate) as gate_head:
            learn.promote_pass([self.pr])
            learn.promote_pass([self.pr])

        gate_head.assert_called_once()
        self.assertEqual(len(self.rows("gate")), 1)
        self.assertEqual(len(self.rows("approved")), 1)
        self.assertEqual(len(self.rows("merged")), 1)

    def test_promote_refreshes_a_behind_lessons_only_head_without_approval_receipt(self) -> None:
        self.journal(self.gate(), self.review())
        self.compare = {"behind_by": 3, "files": [{"filename": config.LESSONS_NAME}]}
        self.run.side_effect = RuntimeError("the head branch is not up to date")

        with mock.patch.object(learn, "refresh") as refresh:
            learn.promote_pass([self.pr])

        refresh.assert_called_once_with(self.cfg, 42, self.BRANCH, self.HEAD)
        self.assertEqual(self.rows("approved"), [])
        self.assertEqual(self.rows("merged"), [])

    def test_promote_records_one_block_for_repeated_red_ci(self) -> None:
        self.journal(self.gate(), self.review())
        with mock.patch.object(dispatch, "pr_checks", return_value=[{"name": "lint", "bucket": "fail"}]):
            for _ in range(3):
                learn.promote_pass([self.pr])

        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"]), (42, self.HEAD))
        self.assertEqual(self.rows("approved"), [])

    def test_promote_records_a_human_merge_and_opens_measurement(self) -> None:
        self.journal(self.gate(), self.review())
        self.gh.side_effect = lambda args: {
            "state": "MERGED",
            "mergedBy": {"login": "mikeroySoft"},
            "headRefName": self.BRANCH,
            "headRefOid": self.HEAD,
        }

        learn.promote_pass([])

        [merged] = self.rows("merged")
        [opened] = self.rows("promotion-open")
        self.assertEqual((merged["pr"], merged["by"], merged["actor"]), (42, "human", "mikeroySoft"))
        self.assertEqual((opened["pr"], opened["head"], opened["branch"]), (42, self.HEAD, self.BRANCH))

    def test_reconciliation_does_not_relabel_a_factory_merge_after_refresh(self) -> None:
        old_head = "b" * 40
        self.journal(
            {"event": "review-result", "pr": 42, "head": old_head, "verdict": "APPROVE"},
            {
                "event": "merged",
                "pr": 42,
                "head": self.HEAD,
                "branch": self.BRANCH,
                "method": "squash",
                "by": "factory",
            },
        )

        for _ in range(3):
            learn.promote_pass([])

        self.gh.assert_not_called()
        [merged] = self.rows("merged")
        self.assertEqual((merged["head"], merged["by"]), (self.HEAD, "factory"))

    def test_reconciliation_settles_historical_non_lessons_reviews(self) -> None:
        self.journal(self.review())
        self.gh.side_effect = lambda _args: {
            "state": "CLOSED",
            "mergedBy": None,
            "headRefName": "contributor/fix",
            "headRefOid": self.HEAD,
        }

        for _ in range(3):
            learn.promote_pass([])

        self.assertEqual(self.gh.call_count, 1)
        [reconciled] = self.rows("promotion-reconciled")
        self.assertEqual(
            (reconciled["pr"], reconciled["head"], reconciled["reason"]),
            (42, self.HEAD, "not a lessons branch"),
        )

    def test_reconciliation_settles_closed_lessons_reviews(self) -> None:
        self.journal(self.review())
        current_head = "b" * 40
        self.gh.side_effect = lambda _args: {
            "state": "CLOSED",
            "mergedBy": None,
            "headRefName": self.BRANCH,
            "headRefOid": current_head,
        }

        for _ in range(3):
            learn.promote_pass([])

        self.assertEqual(self.gh.call_count, 1)
        [blocked] = self.rows("promotion-blocked")
        self.assertEqual((blocked["pr"], blocked["head"], blocked["reason"]), (
            42,
            current_head,
            "closed without merge",
        ))


class LessonsFileTest(unittest.TestCase):
    def test_new_lessons_file_starts_with_the_repository_spdx_license(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("MIT License\n\nCopyright (c) Example\n")
            cfg = config.Config(root=root, repo="acme/widgets", manager=None)
            cfg.factory.mkdir()
            with mock.patch.object(learn, "evidence", return_value=([1], "evidence")), \
                    mock.patch.object(learn, "propose", return_value=(["Run tests."], None)), \
                    mock.patch.object(dispatch, "record"), \
                    mock.patch.object(learn, "record_learn_decision"):
                learn.run(cfg, last=1)

            self.assertTrue(
                (root / config.LESSONS_NAME).read_text().startswith("<!-- SPDX-License-Identifier: MIT -->\n")
            )

    def test_apache_license_file_with_standard_indentation_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text(
                "                                 Apache License\n"
                "                           Version 2.0, January 2004\n"
            )

            self.assertEqual(learn.repository_spdx(root), "Apache-2.0")


class DoctorSigningKeyTest(unittest.TestCase):
    def test_doctor_warns_when_configured_ssh_signing_key_is_not_registered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
            key = root / "agent.pub"
            key.write_text("ssh-ed25519 AAAAagent factory-agent\n")
            for name, value in (
                ("gpg.format", "ssh"),
                ("commit.gpgsign", "true"),
                ("user.signingkey", str(key)),
            ):
                subprocess.run(["git", "-C", str(root), "config", name, value], check=True)
            cfg = config.Config(root=root, repo="acme/widgets", manager=["true", "{prompt}", "{cwd}"])
            cfg.factory.mkdir()
            real_sh = onboard.sh
            commands: list[list[str]] = []

            def command(argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None):
                commands.append(argv)
                if argv == ["gh", "api", "user", "--jq", ".login"]:
                    return subprocess.CompletedProcess(argv, 0, "mikeroysoft-agent\n", "")
                if argv[:3] == ["gh", "api", "users/mikeroysoft-agent/ssh_signing_keys"]:
                    return subprocess.CompletedProcess(argv, 0, "ssh-ed25519 AAAAother another-key\n", "")
                if argv[:3] == ["gh", "api", "user/ssh_signing_keys"]:
                    return subprocess.CompletedProcess(argv, 0, "ssh-ed25519 AAAAother another-key\n", "")
                if argv[:3] == ["gh", "repo", "view"]:
                    return subprocess.CompletedProcess(argv, 0, "ADMIN\n", "")
                if argv[:3] == ["gh", "label", "list"]:
                    return subprocess.CompletedProcess(argv, 0, "[]\n", "")
                if argv[0] == "git":
                    return real_sh(argv, cwd)
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch.object(config, "load", return_value=cfg), \
                    mock.patch.object(onboard, "sh", side_effect=command), \
                    mock.patch.object(onboard, "report_stage_success"), \
                    mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")), \
                    mock.patch.object(onboard.shutil, "which", return_value="/bin/true"), \
                    mock.patch("factory.triage.probe_endpoint", return_value=(True, "ok")), \
                    mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}):
                output = mock.Mock()
                output.write = lambda text: written.append(text)
                written: list[str] = []
                with mock.patch("sys.stdout", output):
                    onboard.doctor(["--json"])

            rows = json.loads("".join(written))["rows"]
            signing = next(row for row in rows if row["label"] == "SSH signing key registered")
            self.assertEqual(signing["status"], "WARN")
            self.assertIn("unknown_key", signing["detail"])
            self.assertIn(["gh", "api", "user", "--jq", ".login"], commands)
            self.assertIn(
                [
                    "gh",
                    "api",
                    "users/mikeroysoft-agent/ssh_signing_keys",
                    "--paginate",
                    "--jq",
                    ".[].key",
                ],
                commands,
            )


if __name__ == "__main__":
    unittest.main()
