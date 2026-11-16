"""Repository-wide host-claim setup checks shared by dispatch and doctor."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import typing
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import claims, config, onboard

CLAIM = "Factory claim: host=tank nonce=n-1 at=2026-11-12T08:00:00+00:00"


def comment(login: str | None, body: str = CLAIM) -> dict:
    user = {} if login is None else {"login": login}
    return {"body": body, "user": user, "author_association": "COLLABORATOR"}


class HostSetupProblemTest(unittest.TestCase):
    def test_unset_host_is_refused_for_a_host_label_or_valid_claim(self) -> None:
        for labels, comments in (
            ([{"name": "host:tank"}], []),
            ([], [comment("factory-bot")]),
        ):
            with self.subTest(labels=labels, comments=comments):
                problem = claims.host_setup_problem(None, "factory-bot", labels, comments)
                self.assertIsNotNone(problem)
                self.assertIn("host_name", problem)

    def test_claim_login_must_match_and_cannot_be_missing(self) -> None:
        for author in ("another-bot", None):
            with self.subTest(author=author):
                problem = claims.host_setup_problem("tank", "factory-bot", [], [comment(author)])
                self.assertIsNotNone(problem)
                self.assertIn("login", problem)

    def test_matching_login_is_case_insensitive(self) -> None:
        self.assertIsNone(claims.host_setup_problem(
            "tank", "Factory-Bot", [{"name": "host:tank"}], [comment("factory-bot")],
        ))

    def test_ordinary_single_host_repository_needs_no_host_name_or_login_lookup(self) -> None:
        ordinary = [
            comment("someone", "not a claim"),
            comment("someone", "Factory claim: host=tank nonce=n-1 at=not-a-date"),
            comment("someone", "prefix Factory claim: host=tank nonce=n-1 at=2026-11-12T08:00:00+00:00"),
        ]
        self.assertIsNone(claims.host_setup_problem(None, "", [{"name": "bug"}], ordinary))

    def test_outsider_claim_cannot_refuse_named_or_single_host_dispatch(self) -> None:
        for host in (None, "tank"):
            for association in ("NONE", "CONTRIBUTOR", "FIRST_TIMER", None):
                with self.subTest(host=host, association=association):
                    forged = {**comment("outsider"), "author_association": association}
                    self.assertIsNone(claims.host_setup_problem(host, "factory-bot", [], [forged]))

    def test_claim_without_text_body_or_timezone_is_not_a_claim(self) -> None:
        not_claims = [
            {**comment("factory-bot"), "body": None},
            {"user": {"login": "factory-bot"}, "author_association": "OWNER"},
            comment("factory-bot", "Factory claim: host=tank nonce=n-1 at=2026-11-12T08:00:00"),
        ]
        for candidate in not_claims:
            with self.subTest(candidate=candidate):
                self.assertIsNone(claims.host_setup_problem(None, "factory-bot", [], [candidate]))

    def test_outsider_association_claim_counts_when_login_matches_case_insensitively(self) -> None:
        own = {**comment("FACTORY-BOT"), "author_association": "NONE"}
        problem = claims.host_setup_problem(None, "factory-bot", [], [own])
        self.assertIsNotNone(problem)
        self.assertIn("host_name", problem)

    def test_outsider_claim_without_user_cannot_refuse_dispatch(self) -> None:
        anonymous = {"body": CLAIM, "user": None, "author_association": "NONE"}
        self.assertIsNone(claims.host_setup_problem(None, "factory-bot", [], [anonymous]))

    def test_outsider_login_sorting_before_dispatcher_cannot_refuse(self) -> None:
        for login in ("aardvark", "factory-bo"):
            with self.subTest(login=login):
                forged = {**comment(login), "author_association": "NONE"}
                self.assertIsNone(claims.host_setup_problem("tank", "factory-bot", [], [forged]))


class RepositoryHostProblemTest(unittest.TestCase):
    def test_reads_all_label_and_closed_issue_comment_pages(self) -> None:
        calls: list[list[str]] = []
        closed_claim = {**comment("FACTORY-BOT"), "issue_url": "https://api.github.test/repos/acme/widgets/issues/7"}
        responses = {
            "repos/acme/widgets/labels?per_page=100": [[{"name": "bug"}], [{"name": "host:tank"}]],
            "repos/acme/widgets/issues/comments?per_page=100": [[], [closed_claim]],
        }

        def github(argv: list[str]) -> object:
            calls.append(argv)
            if argv == ["api", "user"]:
                return {"login": "factory-bot"}
            return responses[argv[-1]]

        self.assertIsNone(claims.repository_host_problem("acme/widgets", "tank", github))
        self.assertEqual(calls, [
            ["api", "--paginate", "--slurp", "repos/acme/widgets/labels?per_page=100"],
            ["api", "--paginate", "--slurp", "repos/acme/widgets/issues/comments?per_page=100"],
            ["api", "user"],
        ])

    def test_ordinary_repository_does_not_read_authenticated_login(self) -> None:
        calls: list[list[str]] = []

        def github(argv: list[str]) -> object:
            calls.append(argv)
            return [[{"name": "bug"}]] if "labels" in argv[-1] else [[comment("someone", "ordinary")]]

        self.assertIsNone(claims.repository_host_problem("acme/widgets", None, github))
        self.assertEqual(len(calls), 2)

    def test_read_and_unparseable_results_fail_closed(self) -> None:
        def unreadable(argv: list[str]) -> object:
            raise OSError("offline")

        def malformed(argv: list[str]) -> object:
            return {}

        for github in (unreadable, malformed):
            with self.subTest(github=github.__name__), self.assertRaises(claims.HostSetupError):
                claims.repository_host_problem("acme/widgets", None, github)

    def test_pages_that_are_not_lists_of_objects_fail_closed(self) -> None:
        for labels in ([[{"name": "bug"}, "host:tank"]], [{}]):
            def github(argv: list[str], labels: object = labels) -> object:
                return labels if "labels" in argv[-1] else [[]]

            with self.subTest(labels=labels), \
                    self.assertRaisesRegex(claims.HostSetupError, "expected paginated JSON objects"):
                claims.repository_host_problem("acme/widgets", "tank", github)

    def test_unreadable_dispatcher_login_fails_closed(self) -> None:
        def github(argv: list[str]) -> object:
            if argv == ["api", "user"]:
                raise RuntimeError("token expired")
            return [[]] if "labels" in argv[-1] else [[comment("factory-bot")]]

        with self.assertRaisesRegex(claims.HostSetupError, "dispatcher GitHub login: token expired"):
            claims.repository_host_problem("acme/widgets", "tank", github)


class ClaimsAnnotationTest(unittest.TestCase):
    def test_host_check_annotations_resolve_exactly(self) -> None:
        self.assertEqual(typing.get_type_hints(claims.host_setup_problem), {
            "host_name": str | None, "login": str, "labels": list[dict],
            "comments": list[dict], "return": str | None,
        })
        self.assertEqual(typing.get_type_hints(claims.repository_host_problem), {
            "repo": str, "host_name": str | None, "gh_json": Callable[[list[str]], object],
            "include_comments": bool, "return": str | None,
        })


class DoctorHostClaimsTest(unittest.TestCase):
    def doctor(
        self, *, author: str = "human", host: str | None = "tank", read_error: bool = False,
    ) -> tuple[int, dict, list[str | None]]:
        seen_config_dirs: list[str | None] = []

        def command(cmd: list[str], cwd: Path | None = None, env=None) -> subprocess.CompletedProcess:
            if cmd[:3] == ["gh", "api", "--paginate"]:
                seen_config_dirs.append(env.get("GH_CONFIG_DIR"))
                if read_error and "issues/comments" in cmd[-1]:
                    raise OSError("offline")
                value = [[{"name": "host:tank"}]] if "labels" in cmd[-1] else [[comment(author)]]
                return subprocess.CompletedProcess(cmd, 0, json.dumps(value), "")
            if cmd[:3] == ["gh", "api", "user"]:
                seen_config_dirs.append(env.get("GH_CONFIG_DIR"))
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"login": "factory-bot"}), "")
            if cmd[:3] == ["gh", "repo", "view"]:
                return subprocess.CompletedProcess(cmd, 0, "ADMIN\n", "")
            if cmd[:3] == ["gh", "label", "list"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(list(config.LABELS)), "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / config.CONFIG_NAME).write_text('[repo]\nslug = "acme/widgets"\n')
            cfg = config.Config(root, "acme/widgets", signoff=False)
            cfg.host_name = host
            cfg.install = config.merge(config.DEFAULT_INSTALL, {
                "env": {"FACTORY_BASE_ENV": "yes"},
                "dispatch_env": {"GH_CONFIG_DIR": "/service/gh"},
            })

            def login(environment: dict[str, str]) -> str:
                return "factory-bot" if environment.get("GH_CONFIG_DIR") == "/service/gh" else "human"

            with mock.patch.object(onboard.config, "load", return_value=cfg), \
                    mock.patch.object(onboard.config, "host_config", return_value={}), \
                    mock.patch.object(onboard, "sh", side_effect=command), \
                    mock.patch.object(onboard, "gh_login", side_effect=login), \
                    mock.patch.object(onboard, "_dashboard_port_check", return_value=(True, "")), \
                    mock.patch.object(onboard.shutil, "which", return_value="/bin/tool"), \
                    mock.patch("factory.triage.probe_endpoint", return_value=(True, "ok")), \
                    mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
                code = onboard.doctor(["--json"])

        rows = json.loads(output.getvalue())["rows"]
        return code, next(row for row in rows if row["label"] == "host claims"), seen_config_dirs

    def test_uses_dispatch_service_identity_and_reports_foreign_claim(self) -> None:
        code, row, config_dirs = self.doctor()
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("human", row["detail"])
        self.assertIn("factory-bot", row["detail"])
        self.assertEqual(config_dirs, ["/service/gh", "/service/gh", "/service/gh"])

    def test_unset_host_name_is_a_doctor_fail(self) -> None:
        code, row, _ = self.doctor(author="factory-bot", host=None)
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("host_name", row["detail"])

    def test_matching_dispatcher_login_passes(self) -> None:
        code, row, _ = self.doctor(author="FACTORY-BOT")
        self.assertEqual((code, row["status"]), (0, "PASS"))

    def test_read_failure_is_a_host_claims_fail(self) -> None:
        code, row, _ = self.doctor(read_error=True)
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("offline", row["detail"])


if __name__ == "__main__":
    unittest.main()
