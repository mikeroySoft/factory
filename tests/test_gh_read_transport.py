"""Focused transport and classifier mutation witnesses for GitHub reads."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from factory import config, evidence, gh_reads, onboard, release, stats


class GitHubReadTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = self.root / "events.jsonl"

    @staticmethod
    def budget(*, core=(0, 1200), graphql=(0, 1200)) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            [],
            0,
            json.dumps({
                "resources": {
                    "core": {"remaining": core[0], "reset": core[1]},
                    "graphql": {"remaining": graphql[0], "reset": graphql[1]},
                }
            }),
            "",
        )

    def failed_read(
        self,
        cmd: list[str],
        *,
        probe: subprocess.CompletedProcess[str],
        diagnostic: str = "API rate limit exceeded; Retry-After: 17",
        env: dict[str, str] | None = None,
    ) -> tuple[gh_reads.Pass, mock.Mock, str]:
        output = io.StringIO()
        with (
            mock.patch("factory.gh_reads.time.time", return_value=1000),
            mock.patch("factory.gh_reads.subprocess.run", return_value=probe) as run,
            redirect_stdout(output),
            gh_reads.scope(self.journal, "transport", persistent=False) as state,
        ):
            with gh_reads.item(7):
                gh_reads.run(
                    cmd,
                    lambda: subprocess.CompletedProcess(cmd, 1, "", diagnostic),
                    env=env,
                )
        return state, run, output.getvalue()

    def test_read_classifier_requires_gh_and_distinguishes_graphql_queries(self) -> None:
        self.assertFalse(gh_reads.is_read(["echo", "issue", "view"]))
        cases = (
            (["gh", "api", "graphql", "-f", "query=query { viewer { login } }"], True),
            (["gh", "api", "https://host/api/graphql", "-F", "query=query Q { viewer { login } }"], True),
            (["gh", "api", "graphql", "-f", "query=mutation { deleteProjectV2(input: {}) { clientMutationId } }"], False),
            (["gh", "api", "repos/acme/widgets", "-f", "query=query { viewer { login } }"], False),
        )
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                self.assertIs(gh_reads.is_read(cmd), expected)

    def test_api_field_forms_are_writes_without_an_explicit_read_method(self) -> None:
        forms = (
            ["-f", "name=value"],
            ["-F", "name=@value.txt"],
            ["--field", "name=value"],
            ["--raw-field", "name=value"],
            ["--input", "payload.json"],
            ["-fname=value"],
            ["-Fname=@value.txt"],
            ["--field=name=value"],
            ["--raw-field=name=value"],
            ["--input=payload.json"],
        )
        for form in forms:
            with self.subTest(form=form):
                self.assertFalse(gh_reads.is_read(["gh", "api", "repos/acme/widgets", *form]))
                self.assertFalse(gh_reads.is_read(["gh", "api", *form, "repos/acme/widgets"]))
        self.assertTrue(gh_reads.is_read(
            ["gh", "api", "repos/acme/widgets", "--method", "GET", "--field", "ignored=value"]
        ))

    def test_failure_probe_preserves_hostname_and_environment(self) -> None:
        env = {"GH_HOST": "ghe.example"}
        state, run, _output = self.failed_read(
            ["gh", "api", "repos/acme/widgets/issues/7", "--hostname", "ghe.example"],
            probe=self.budget(),
            diagnostic="connection refused",
            env=env,
        )
        self.assertEqual(state.codes, {"rate_limited": 0, "gh_read_failed": 1})
        run.assert_called_once_with(
            ["gh", "api", "rate_limit", "--hostname", "ghe.example"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_rate_limit_reset_requires_nonnegative_budget_and_future_reset(self) -> None:
        scenarios = (
            (["gh", "issue", "view", "7"], self.budget(graphql=(-1, 1200)), 1017),
            (["gh", "issue", "view", "7"], self.budget(graphql=(0, 900)), 1017),
            (["gh", "issue", "view", "7"], self.budget(graphql=(0, 1200)), 1200),
            (["gh", "release", "view", "v1.2.3"], self.budget(core=(0, 1300)), 1300),
        )
        for cmd, probe, expected in scenarios:
            with self.subTest(cmd=cmd, expected=expected):
                state, _, _ = self.failed_read(cmd, probe=probe)
                self.assertEqual(state.reason, "rate_limited")
                self.assertEqual(state.reset, expected)

    def test_expected_absences_are_operation_specific(self) -> None:
        result = lambda code, out="", err="": subprocess.CompletedProcess([], code, out, err)
        accepted = (
            (["gh", "pr", "view", "agent/7"], result(1, err="No pull requests found for branch"), True),
            (["gh", "api", "repos/acme/widgets/compare/a...b"], result(1, err="HTTP 404"), True),
            (["gh", "api", "repos/acme/widgets/issues/7/dependencies/blocked_by"], result(1, err="HTTP 404"), True),
            (["gh", "release", "view", "v1.2.3"], result(1, err="Release not found."), False),
            (["gh", "pr", "checks", "7"], result(1, "[]"), False),
            (["gh", "pr", "checks", "7"], result(8, "[]"), False),
            (["gh", "pr", "checks", "7"], result(1, err="no checks reported"), False),
            (["gh", "pr", "checks", "7", "--required"],
             result(1, err="no required checks reported"), False),
        )
        for cmd, completed, check in accepted:
            with self.subTest(accepted=cmd):
                self.assertTrue(gh_reads._expected_result(cmd, completed, check))

        rejected = (
            (["gh", "issue", "view", "7"], result(1, err="No pull requests found"), True),
            (["gh", "issue", "view", "repos/acme/widgets/compare/a...b"], result(1, err="HTTP 404"), True),
            (["gh", "api", "repos/acme/widgets/issues/7"], result(1, err="HTTP 404"), True),
            (["gh", "release", "watch", "v1.2.3"], result(1, err="Release not found"), False),
            (["gh", "release", "view", "v1.2.3"], result(1, err="Release not found"), True),
            (["gh", "pr", "branch", "7"], result(1, "[]"), False),
            (["gh", "pr", "status", "7"], result(1, "[]"), False),
            (["gh", "pr", "checks", "7"], result(1, err="connection refused"), False),
            (["gh", "pr", "checks", "7"], result(1, err="no checks reported\nconnection refused"), False),
            (["gh", "pr", "checks", "7"], result(1, "partial", "no checks reported"), False),
            (["gh", "pr", "checks", "7"], result(8, err="no checks reported"), False),
        )
        for cmd, completed, check in rejected:
            with self.subTest(rejected=cmd):
                self.assertFalse(gh_reads._expected_result(cmd, completed, check))

    def test_majority_is_strictly_more_than_half(self) -> None:
        output = io.StringIO()
        with (
            mock.patch("factory.gh_reads.subprocess.run", return_value=self.budget()),
            redirect_stdout(output),
            gh_reads.scope(self.journal, "majority", persistent=False) as state,
        ):
            for ticket in range(1, 5):
                cmd = ["gh", "issue", "view", str(ticket)]
                with gh_reads.item(ticket):
                    gh_reads.run(cmd, lambda cmd=cmd: subprocess.CompletedProcess(cmd, 0, "{}", ""))
            for ticket in range(5, 8):
                cmd = ["gh", "issue", "view", str(ticket)]
                with gh_reads.item(ticket):
                    gh_reads.run(cmd, lambda cmd=cmd: subprocess.CompletedProcess(cmd, 1, "", "offline"))
            self.assertEqual((state.reads, state.failed_reads, state.reason), (7, 3, None))

    def test_stopped_items_do_not_consume_the_remaining_input(self) -> None:
        consumed: list[int] = []

        def rows():
            for value in (1, 2, 3):
                consumed.append(value)
                yield value

        with gh_reads.scope(self.journal, "items", persistent=False) as state:
            state.reason = "rate_limited"
            self.assertEqual(list(gh_reads.items(rows())), [])
        self.assertEqual(consumed, [1])

    def test_stopped_reason_and_keyword_call_contract_are_preserved(self) -> None:
        marker = object()
        self.assertIs(gh_reads.run(cmd=["echo"], invoke=lambda: marker), marker)
        with gh_reads.scope(self.journal, "stopped", persistent=False) as state:
            state.reason = "rate_limited"
            with self.assertRaises(gh_reads.ReadFailed) as raised:
                gh_reads.run(["gh", "issue", "view", "7"], lambda: marker)
        self.assertEqual(raised.exception.code, "rate_limited")

    def test_failed_ticket_reuses_the_original_failure_code(self) -> None:
        first = ["gh", "issue", "view", "7"]
        later = mock.Mock()
        output = io.StringIO()
        with (
            mock.patch("factory.gh_reads.subprocess.run", return_value=self.budget()),
            redirect_stdout(output),
            gh_reads.scope(self.journal, "failed-ticket", persistent=False) as state,
        ):
            with gh_reads.item(7):
                gh_reads.run(first, lambda: subprocess.CompletedProcess(first, 1, "", "offline"))
            self.assertIsNone(state.reason)
            with self.assertRaises(gh_reads.ReadFailed) as raised:
                gh_reads.run(["gh", "pr", "view", "7"], later)
        self.assertEqual(raised.exception.code, "gh_read_failed")
        later.assert_not_called()

    def test_bounded_reader_accepts_named_transport_arguments(self) -> None:
        with (
            mock.patch("factory.evidence.time.monotonic", return_value=10),
            self.assertRaises(evidence.EvidenceError) as raised,
        ):
            evidence._bounded_github_read(
                command=["gh", "api", "repos/acme/widgets"],
                endpoint="repos/acme/widgets",
                deadline=9,
            )
        self.assertEqual(raised.exception.code, "collection_timeout")

    def test_consumer_wrappers_keep_subprocess_error_and_output_contracts(self) -> None:
        cfg = config.Config(self.root, "acme/widgets")
        command = self.root / "gh"
        command.write_text(
            f"#!{sys.executable}\nimport os, sys\n"
            "print(os.environ['FAKE_GH_STDOUT'])\n"
            "print('permission denied', file=sys.stderr)\n"
            "sys.exit(int(os.environ.get('FAKE_GH_STATUS', '0')))\n"
        )
        command.chmod(0o755)
        with mock.patch.dict(os.environ, {
            "PATH": f"{self.root}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_STDOUT": "octocat",
            "FAKE_GH_STATUS": "0",
        }):
            self.assertEqual(onboard.gh_login(dict(os.environ)), "octocat")
            with self.assertRaises(subprocess.CalledProcessError) as failed:
                onboard.gh_login({**os.environ, "FAKE_GH_STATUS": "3"})
            self.assertEqual((failed.exception.returncode, failed.exception.stderr),
                             (3, "permission denied\n"))

            os.environ["FAKE_GH_STDOUT"] = '{"id": 7}'
            self.assertEqual(release._gh(cfg, "repos/acme/widgets/releases/latest"), {"id": 7})
            os.environ["FAKE_GH_STDOUT"] = "v1.2.3"
            result = release._gh_release(cfg, "view", "v1.2.3")
            self.assertEqual((result.returncode, result.stdout, result.stderr),
                             (0, "v1.2.3\n", "permission denied\n"))

            os.environ["FAKE_GH_STATUS"] = "3"
            diagnostic = io.StringIO()
            with mock.patch.object(stats, "REPOSITORY", "acme/widgets"), \
                    redirect_stderr(diagnostic), self.assertRaises(SystemExit) as failed:
                stats.gh("issue", "list")
            self.assertEqual(failed.exception.code, 3)
            self.assertEqual(diagnostic.getvalue(), "permission denied\n")


if __name__ == "__main__":
    unittest.main()
