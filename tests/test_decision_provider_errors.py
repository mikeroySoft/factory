"""Provider-read diagnostics reach MCP and dashboard callers without credentials."""
from __future__ import annotations

import json
import os
import sys
import subprocess
import tempfile
import unittest
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from factory import config, decisions, mcp, workspace


class ProviderErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = config.Config(root=self.root, repo="acme/widgets")
        gh = self.root / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if sys.argv[1] == 'api':\n"
            "    print('alice')\n"
            "else:\n"
            "    print(os.environ['STUB_STDERR'], file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
        )
        gh.chmod(0o755)
        self.environment = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ['PATH']}
        self.now = datetime(2026, 11, 26, tzinfo=UTC)
        for patcher in (
            mock.patch.object(config, "load", return_value=self.cfg),
            mock.patch.object(config, "dashboard_env", side_effect=lambda cfg: self.environment.copy()),
            mock.patch.object(decisions, "_now", return_value=self.now),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def error(self, diagnostic: str) -> dict:
        self.environment["STUB_STDERR"] = diagnostic
        response = mcp.call_tool(
            "factory_prepare_action",
            {"repository": self.cfg.repo, "action": "requeue_agent", "args": {"number": 928}},
            {self.cfg.repo: str(self.root)},
        )
        self.assertTrue(response["isError"])
        payload = json.loads(response["content"][0]["text"])
        self.assertFalse(payload["ok"])
        return payload["error"]

    def test_gh_rate_limits_and_generic_failures(self) -> None:
        variants = (
            "GraphQL: API rate limit already exceeded for user ID 1791194",
            "gh: API rate limit exceeded for 192.0.2.1. (HTTP 403)",
            "You have exceeded a secondary rate limit. Please wait a few minutes before you try again.",
            "You have triggered an abuse detection mechanism. Please wait a few minutes.",
            "gh: Too Many Requests (HTTP 429)",
        )
        for diagnostic in variants:
            with self.subTest(diagnostic=diagnostic):
                error = self.error(diagnostic)
                self.assertEqual(error["code"], "provider_rate_limited")
                self.assertTrue(error["retryable"])
                self.assertEqual(error["retry_after_seconds"], 60)
                self.assertEqual(error["backoff"], {"initial_seconds": 60, "multiplier": 2, "max_seconds": 3600})
                self.assertEqual(error["stderr"], diagnostic)
        error = self.error("gh: Could not resolve host: api.github.com")
        self.assertEqual(error["code"], "provider_unavailable")
        self.assertEqual(error["stderr"], "gh: Could not resolve host: api.github.com")
        self.assertNotIn("retryable", error)
        self.assertNotIn("backoff", error)

    def test_provider_retry_headers(self) -> None:
        for header, expected in (
            ("Retry-After: 123", 123),
            ("retry-after: Thu, 26 Nov 2026 00:02:00 GMT", 120),
            (f"X-RateLimit-Reset: {int(self.now.timestamp()) + 300}", 300),
            (f"Retry-After: 12\nX-RateLimit-Reset: {int(self.now.timestamp()) + 300}", 12),
            ("Retry-After: nonsense", 60),
        ):
            with self.subTest(header=header):
                diagnostic = "gh: API rate limit exceeded\n" + header
                error = self.error(diagnostic)
                self.assertEqual(error["retry_after_seconds"], expected)
                self.assertEqual(error["stderr"], diagnostic)

    def test_stderr_is_redacted_before_bounding(self) -> None:
        secrets = (
            "ghp_" + "a" * 40,
            "github_pat_" + "b" * 50,
            "Bearer " + "c" * 40,
            "GH_TOKEN=" + "d" * 40,
        )
        diagnostic = "generic failure\n" + "\n".join(secrets) + "\nend of diagnostic"
        error = self.error(diagnostic)
        self.assertIn("end of diagnostic", error["stderr"])
        for secret in secrets:
            self.assertNotIn(secret, json.dumps(error))
        # Clipping first would expose the suffix of a token longer than the output cap.
        error = self.error("generic failure\nghp_" + "z" * (decisions.OUTPUT_CAP + 100))
        self.assertNotIn("z" * 20, error["stderr"])
        error = self.error("prefix\n" + "x" * (decisions.OUTPUT_CAP + 100) + "\nlast diagnostic")
        self.assertLessEqual(len(error["stderr"]), decisions.OUTPUT_CAP)
        self.assertTrue(error["stderr"].endswith("last diagnostic"))

    def test_dashboard_error_retains_retry_metadata(self) -> None:
        self.environment["STUB_STDERR"] = "GraphQL: API rate limit already exceeded for user ID 1791194"
        with self.assertRaises(decisions.DecisionError) as caught:
            decisions.prepare(self.cfg, [{"op": "issue", "number": 928, "comment": "retry"}])
        error = workspace._request_error(caught.exception)
        self.assertEqual(error.status, 429)
        self.assertEqual(error.code, "provider_rate_limited")
        self.assertEqual(error.extra["stderr"], self.environment["STUB_STDERR"])
        self.assertTrue(error.extra["retryable"])
        self.assertEqual(error.extra["retry_after_seconds"], 60)

    def test_quoted_credentials_are_redacted_before_bounding(self) -> None:
        for assignment, secret in (
            (json.dumps({"access_token": "0123456789abcdef" * 3}), "0123456789abcdef" * 3),
            ('"api_key": "private value with spaces"', "private value with spaces"),
            ("'client_secret' = 'private value with spaces'", "private value with spaces"),
            (json.dumps({"password": 'private "quoted" \\ value'}), 'private "quoted" \\ value'),
            (json.dumps({"access_token": "z" * (decisions.OUTPUT_CAP + 100)}), "z" * 20),
        ):
            with self.subTest(assignment=assignment[:50]):
                error = self.error("generic failure\n" + assignment + "\nlast diagnostic")
                self.assertNotIn(secret, error["stderr"])
                self.assertNotIn(json.dumps(secret)[1:-1], error["stderr"])
                self.assertIn("[credential withheld]", error["stderr"])
                self.assertTrue(error["stderr"].endswith("last diagnostic"))

    def test_receipt_observation_retains_diagnostics_without_replaying(self) -> None:
        issue = {
            "number": 928, "title": "Fix parser", "body": "Issue body", "state": "OPEN",
            "labels": [], "assignees": [], "updatedAt": "2026-11-26T00:00:00Z",
            "url": "https://github.com/acme/widgets/issues/928",
        }
        with mock.patch.object(decisions, "_read_json", return_value=issue):
            preview = decisions.prepare(self.cfg, [{"op": "issue", "number": 928, "comment": "retry"}])
        proposal_id = preview["proposal_id"]
        original = decisions.receipt(self.cfg, proposal_id)
        events = (self.cfg.factory / "events.jsonl").read_bytes()
        for diagnostic, code in (
            ("API rate limit exceeded\nRetry-After: 123", "provider_rate_limited"),
            ("Could not resolve host: api.github.com", "provider_unavailable"),
        ):
            with self.subTest(code=code):
                self.environment["STUB_STDERR"] = diagnostic + '\n{"access_token":"private credential"}'
                result = decisions.receipt(self.cfg, proposal_id, observe=True)
                error, = result["observation"]["errors"]
                self.assertEqual(error["target"], {"kind": "issue", "number": 928})
                self.assertEqual(error["code"], code)
                self.assertIn(diagnostic, error["stderr"])
                self.assertNotIn("private credential", json.dumps(result))
                if code == "provider_rate_limited":
                    self.assertTrue(error["retryable"])
                    self.assertEqual(error["retry_after_seconds"], 123)
                    self.assertEqual(error["backoff"], {
                        "initial_seconds": 60, "multiplier": 2, "max_seconds": 3600,
                    })
                else:
                    self.assertNotIn("retryable", error)
                self.assertEqual({k: v for k, v in result.items() if k != "observation"}, original)
                self.assertEqual(decisions.receipt(self.cfg, proposal_id), original)
                self.assertEqual((self.cfg.factory / "events.jsonl").read_bytes(), events)
                self.assertFalse((self.cfg.factory / "decisions" / f"intent-{proposal_id}.json").exists())

    def test_retry_headers_fallback_and_elapsed_deadlines(self) -> None:
        for header in (
            "Retry-After: 0",
            "Retry-After: Wed, 25 Nov 2026 23:59:59 GMT",
            f"X-RateLimit-Reset: {int(self.now.timestamp()) - 1}",
        ):
            with self.subTest(header=header):
                self.assertEqual(self.error("API rate limit exceeded\n" + header)["retry_after_seconds"], 1)
        error = self.error(
            "API rate limit exceeded\nRetry-After: invalid\n"
            f"X-RateLimit-Reset: {int(self.now.timestamp()) + 45}"
        )
        self.assertEqual(error["retry_after_seconds"], 45)

    def test_git_diagnostic_does_not_become_a_github_throttle(self) -> None:
        argv = ["git", "status"]
        diagnostic = "remote API rate limit exceeded"
        with (
            mock.patch.object(decisions, "_run", return_value=subprocess.CompletedProcess(argv, 1, "", diagnostic)),
            self.assertRaises(decisions.DecisionError) as caught,
        ):
            decisions._checked(self.cfg, argv, time.monotonic() + 10)
        self.assertEqual(caught.exception.code, "provider_unavailable")
        self.assertEqual(caught.exception.extra, {"stderr": diagnostic})

    def test_dashboard_generic_error_retains_message_and_default_status(self) -> None:
        error = workspace._request_error(decisions.DecisionError("Invalid decision", "invalid_request"))
        self.assertEqual(error.code, "invalid_request")
        self.assertEqual(error.status, 400)
        self.assertEqual(str(error), "Invalid decision")


if __name__ == "__main__":
    unittest.main()
