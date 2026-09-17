"""Behavioral checks for the repository settings API."""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from factory import config, settings


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(parent: Path, toml: str = "") -> Path:
    repo = parent / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "factory-tests@example.invalid")
    git(repo, "config", "user.name", "Factory tests")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    (repo / "README.md").write_text("hello\n")
    if toml:
        (repo / config.CONFIG_NAME).write_text(toml)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "initial")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


@contextlib.contextmanager
def isolated_xdg(parent: Path):
    previous = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(parent / "xdg")
    try:
        yield parent / "xdg"
    finally:
        if previous is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = previous


def write_host(xdg: Path, text: str) -> Path:
    path = xdg / "factory" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def snapshot(repo: Path) -> dict:
    result = settings.snapshot(repo)
    if not result.get("ok"):
        raise AssertionError(result)
    return result


class SettingsTest(unittest.TestCase):
    def test_save_round_trip_preserves_unrelated_toml_and_mode(self) -> None:
        toml = (
            "# retain this comment\n"
            "[repo]\nslug = \"acme/widgets\"\n\n"
            "[dispatch]\nmax_active = 2\nbudget_min = 17\nmax_attempts = 3\nreview_rounds = 1\n\n"
            "[manager]\nmodel = \"old/model\"\n\n"
            "[unrelated]\nvalue = \"keep me\"\n"
        )
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), toml)
            path = repo / config.CONFIG_NAME
            path.chmod(0o640)
            mode = stat.S_IMODE(path.stat().st_mode)
            before = path.read_text()
            current = snapshot(repo)

            result = settings.save(
                repo,
                {
                    "revision": current["revision"],
                    "changes": {"dispatch.max_active": 6, "manager.model": "new/model"},
                },
            )

            self.assertTrue(result["ok"], result)
            after = path.read_text()
            self.assertIn("# retain this comment", after)
            self.assertIn('[unrelated]\nvalue = "keep me"', after)
            self.assertIn('budget_min = 17', after)
            self.assertNotEqual(before, after)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            self.assertEqual(config.load(repo).max_active, 6)
            self.assertEqual(config.load(repo).manager_model, "new/model")
            fresh = snapshot(repo)
            self.assertEqual(fresh["fields"]["dispatch.max_active"]["value"], 6)
            self.assertTrue(fresh["fields"]["dispatch.max_active"]["source"].startswith("Repository"))

    def test_invalid_type_range_and_unknown_changes_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), "[dispatch]\nmax_active = 2\n")
            path = repo / config.CONFIG_NAME
            current = snapshot(repo)
            before = path.read_bytes()
            invalid = (
                {"dispatch.max_active": True},
                {"dispatch.max_active": 1.5},
                {"dispatch.max_active": "3"},
                {"dispatch.max_active": 0},
                {"dispatch.budget_min": 0},
                {"dispatch.max_attempts": 0},
                {"dispatch.review_rounds": -1},
                {"manager.model": "   "},
                {"settings.not_a_field": 1},
                {"dispatch.max_active": 5, "dispatch.budget_min": 0},
            )
            for changes in invalid:
                with self.subTest(changes=changes):
                    result = settings.save(
                        repo, {"revision": current["revision"], "changes": changes}
                    )
                    self.assertFalse(result["ok"], result)
                    self.assertTrue(result.get("errors") or result.get("error"))
                    self.assertEqual(path.read_bytes(), before)

            malformed = (
                {},
                {"revision": 123, "changes": {}},
                {"revision": current["revision"], "changes": []},
                {
                    "revision": current["revision"],
                    "changes": {"dispatch.max_active": 5},
                    "extra": True,
                },
            )
            for request in malformed:
                with self.subTest(request=request):
                    result = settings.save(repo, request)
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(path.read_bytes(), before)

    def test_repository_host_and_builtin_provenance_and_manager_reset(self) -> None:
        host = (
            "[defaults.manager]\nmodel = \"host-default/model\"\n"
            "[repo.\"acme/widgets\".manager]\nmodel = \"host-repo/model\"\n"
            "[defaults.triage]\nurl = \"https://default.example/v1\"\nmodel = \"default-triage\"\n"
            "[repo.\"acme/widgets\".triage]\nmodel = \"repo-triage\"\n"
        )
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)) as xdg:
            write_host(xdg, host)
            repo = make_repo(
                Path(directory),
                '[manager]\nmodel = "repo/model"\n[triage]\nmodel = "repo-file-triage"\n',
            )
            current = snapshot(repo)
            self.assertEqual(current["fields"]["manager.model"]["value"], "repo/model")
            self.assertTrue(current["fields"]["manager.model"]["source"].startswith("Repository"))
            self.assertEqual(current["agents"]["triage"]["model"], "repo-file-triage")
            self.assertIn("Repository", current["agents"]["triage"]["source"])
            self.assertIn("endpoint: Host default", current["agents"]["triage"]["source"])
            self.assertEqual(current["fields"]["dispatch.max_attempts"]["value"], 3)
            self.assertTrue(
                current["fields"]["dispatch.max_attempts"]["source"].startswith("Built-in default")
            )

            reset = settings.save(
                repo,
                {"revision": current["revision"], "changes": {"manager.model": None}},
            )
            self.assertTrue(reset["ok"], reset)
            inherited = snapshot(repo)
            self.assertEqual(inherited["fields"]["manager.model"]["value"], "host-repo/model")
            self.assertIn("Host repository override", inherited["fields"]["manager.model"]["source"])

    def test_legacy_manager_model_is_reported_and_reset_keeps_legacy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(
                Path(directory),
                '[manager]\ncommand = ["omp", "--model", "legacy/model", "--token", "SECRET"]\n',
            )
            current = snapshot(repo)
            self.assertEqual(current["fields"]["manager.model"]["value"], "legacy/model")
            self.assertIn("legacy", current["fields"]["manager.model"]["source"].lower())
            reset = settings.save(
                repo,
                {"revision": current["revision"], "changes": {"manager.model": None}},
            )
            self.assertTrue(reset["ok"], reset)
            after = snapshot(repo)
            self.assertEqual(after["fields"]["manager.model"]["value"], "legacy/model")
            self.assertIn("legacy", after["fields"]["manager.model"]["source"].lower())

    def test_repository_and_host_edits_make_a_revision_stale(self) -> None:
        host_text = '[defaults.manager]\nmodel = "host/model"\n'
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)) as xdg:
            host_path = write_host(xdg, host_text)
            repo = make_repo(Path(directory), '[manager]\nmodel = "repo/model"\n')

            repo_revision = snapshot(repo)["revision"]
            repo_path = repo / config.CONFIG_NAME
            repo_path.write_text(repo_path.read_text() + "# changed elsewhere\n")
            before = repo_path.read_bytes()
            result = settings.save(
                repo,
                {"revision": repo_revision, "changes": {"dispatch.max_active": 4}},
            )
            self.assertFalse(result["ok"], result)
            self.assertIn("stale", result["error"].lower())
            self.assertEqual(repo_path.read_bytes(), before)

            host_revision = snapshot(repo)["revision"]
            host_path.write_text(host_path.read_text() + "# changed on host\n")
            before = repo_path.read_bytes()
            result = settings.save(
                repo,
                {"revision": host_revision, "changes": {"dispatch.max_active": 4}},
            )
            self.assertFalse(result["ok"], result)
            self.assertIn("stale", result["error"].lower())
            self.assertEqual(repo_path.read_bytes(), before)

    def test_summary_is_credential_safe_and_endpoint_is_reduced_to_origin(self) -> None:
        toml = (
            "[workers]\n"
            'default = ["omp", "--model", "worker/model", "--api-key", "WORKER_SECRET", "{prompt}"]\n'
            'chore = ["env", "TOKEN=WRAPPER_SECRET", "omp", "--model", "chore/model", "{prompt}"]\n'
            "[review]\n"
            'command = ["omp", "--model", "review/model", "--header", "Bearer REVIEW_SECRET", "{prompt}"]\n'
            "[triage]\n"
            'url = "https://user:PASS@triage.example:8443/v1/chat/completions?token=QUERY_SECRET#fragment"\n'
            'model = "triage/model"\n'
        )
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), toml)
            result = snapshot(repo)
            rendered = json.dumps(result, sort_keys=True)
            for secret in (
                "WORKER_SECRET",
                "WRAPPER_SECRET",
                "REVIEW_SECRET",
                "QUERY_SECRET",
                "user:PASS",
                "/v1/chat/completions",
            ):
                self.assertNotIn(secret, rendered)
            workers = {worker["label"]: worker for worker in result["agents"]["workers"]}
            self.assertEqual(workers["default"]["program"], "omp")
            self.assertEqual(workers["default"]["model"], "worker/model")
            self.assertEqual(result["agents"]["reviewer"]["program"], "omp")
            self.assertEqual(result["agents"]["reviewer"]["model"], "review/model")
            self.assertTrue(result["agents"]["triage"]["endpoint"].startswith("https://triage.example"))

    def test_structured_worker_command_summary_and_provenance(self) -> None:
        toml = (
            "[workers]\n"
            'default = { command = ["omp", "--model", "worker/model", "{prompt}"], when = "All work" }\n'
        )
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), toml)
            workers = snapshot(repo)["agents"]["workers"]
            self.assertEqual(
                workers,
                [{
                    "label": "default",
                    "program": "omp",
                    "model": "worker/model",
                    "source": "Repository",
                }],
            )

    def test_invalid_config_error_does_not_disclose_values(self) -> None:
        sentinel = "OWNER_SECRET!"
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), f'[collaboration]\nfallback = "{sentinel}"\n')
            result = settings.snapshot(repo)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["error"], "Repository configuration could not be loaded safely")
            self.assertNotIn(sentinel, json.dumps(result))

    def test_missing_file_is_created_and_symlink_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory))
            current = snapshot(repo)
            result = settings.save(
                repo,
                {"revision": current["revision"], "changes": {"dispatch.budget_min": 101}},
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(config.load(repo).budget_min, 101)

            path = repo / config.CONFIG_NAME
            target = Path(directory) / "outside.toml"
            target.write_text('[dispatch]\nbudget_min = 999\n')
            path.unlink()
            path.symlink_to(target)
            before = target.read_bytes()
            result = settings.save(
                repo,
                {"revision": current["revision"], "changes": {"dispatch.budget_min": 102}},
            )
            self.assertFalse(result["ok"], result)
            self.assertEqual(target.read_bytes(), before)
            self.assertTrue(path.is_symlink())

    def test_replace_failure_keeps_config_bytes_and_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(
                Path(directory),
                '[dispatch]\nmax_active = 2\n[unrelated]\nvalue = "keep this"\n',
            )
            path = repo / config.CONFIG_NAME
            path.chmod(0o640)
            before = path.read_bytes()
            mode = stat.S_IMODE(path.stat().st_mode)
            current = snapshot(repo)

            with mock.patch.object(settings.os, "replace", side_effect=OSError("replace failed")):
                result = settings.save(
                    repo,
                    {"revision": current["revision"], "changes": {"dispatch.max_active": 6}},
                )

            self.assertFalse(result["ok"], result)
            self.assertTrue(result.get("error"), result)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)


class SettingsHTTPTest(unittest.TestCase):

    def test_malformed_repo_and_host_toml_return_safe_error_envelopes(self) -> None:
        from factory import dashboard

        cases = (
            ("repo", '[dispatch\nsentinel = "REPO_SENTINEL"\n', "REPO_SENTINEL"),
            ("host", '[defaults.manager\nmodel = "HOST_SENTINEL"\n', "HOST_SENTINEL"),
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for kind, malformed, sentinel in cases:
                case_parent = base / kind
                case_parent.mkdir()
                with isolated_xdg(case_parent) as xdg:
                    repo = make_repo(case_parent, "[dispatch]\nmax_active = 2\n")
                    dashboard.configure(config.load(repo))
                    if kind == "repo":
                        path = repo / config.CONFIG_NAME
                        path.write_text(malformed)
                    else:
                        path = write_host(xdg, malformed)
                    before = path.read_bytes()
                    server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        connection = http.client.HTTPConnection(*server.server_address)
                        try:
                            connection.request("GET", "/api/settings")
                            response = connection.getresponse()
                            result = json.loads(response.read())
                        finally:
                            connection.close()
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)

                    self.assertEqual(response.status, 200)
                    self.assertFalse(result.get("ok"), result)
                    self.assertEqual(set(result), {"ok", "error"}, result)
                    self.assertNotIn(sentinel, json.dumps(result))
                    self.assertEqual(path.read_bytes(), before)

    def test_concurrent_authorized_saves_share_one_revision(self) -> None:
        from factory import dashboard

        toml = '[dispatch]\nmax_active = 2\n[unrelated]\nvalue = "preserve me"\n'
        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), toml)
            dashboard.configure(config.load(repo))
            revision = snapshot(repo)["revision"]
            barrier = threading.Barrier(2)
            server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def save(value: int) -> tuple[int, int, dict]:
                barrier.wait()
                connection = http.client.HTTPConnection(*server.server_address)
                try:
                    body = json.dumps(
                        {"revision": revision, "changes": {"dispatch.max_active": value}}
                    ).encode()
                    connection.request(
                        "POST",
                        "/api/settings",
                        body=body,
                        headers={"Content-Type": "application/json", "X-Factory-Act": "1"},
                    )
                    response = connection.getresponse()
                    return value, response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(save, (7, 8)))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual([status for _, status, _ in results], [200, 200])
            successful = [item for item in results if item[2].get("ok")]
            failed = [item for item in results if not item[2].get("ok")]
            self.assertEqual(len(successful), 1, results)
            self.assertEqual(len(failed), 1, results)
            winner = successful[0]
            loser = failed[0]
            self.assertEqual(winner[2]["fields"]["dispatch.max_active"]["value"], winner[0])
            self.assertTrue(
                any(word in loser[2].get("error", "").lower() for word in ("stale", "conflict", "changed")),
                results,
            )
            self.assertEqual(config.load(repo).max_active, winner[0])
            self.assertIn(b'[unrelated]\nvalue = "preserve me"', (repo / config.CONFIG_NAME).read_bytes())

    def test_settings_post_requires_the_existing_same_origin_guard(self) -> None:
        from factory import dashboard

        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), "[dispatch]\nmax_active = 2\n")
            dashboard.configure(config.load(repo))
            server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                path = repo / config.CONFIG_NAME
                before = path.read_bytes()
                revision = snapshot(repo)["revision"]
                body = json.dumps(
                    {"revision": revision, "changes": {"dispatch.max_active": 9}}
                ).encode()
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/settings",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                response_body = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 403)
                self.assertFalse(response_body["ok"])
                self.assertEqual(path.read_bytes(), before)

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/settings",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Factory-Act": "1",
                        "Origin": "http://evil.example",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()
                self.assertEqual(path.read_bytes(), before)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


    def test_authorized_save_changes_model_for_next_new_request(self) -> None:
        from factory import briefing, dashboard

        with tempfile.TemporaryDirectory() as directory, isolated_xdg(Path(directory)):
            repo = make_repo(Path(directory), '[manager]\nmodel = "old/model"\n')
            dashboard.configure(config.load(repo))
            run = "2026-01-01T00:00:00Z"
            evidence = {
                "errors": [],
                "dispatcher": {
                    "runs": [
                        {"started": run, "finished": run, "result": "done", "lines": ["ok"]}
                    ]
                },
            }
            source = briefing.run_sources(dashboard.cfg, evidence, run)[0]["id"]
            bindir = Path(directory) / "bin"
            bindir.mkdir()
            args_path = Path(directory) / "omp-args"
            omp = bindir / "omp"
            omp.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$@\" > \"{args_path}\"\n"
                f"printf 'stub answer [{source}]'\n"
            )
            omp.chmod(0o755)
            previous_path = os.environ.get("PATH")
            os.environ["PATH"] = f"{bindir}:{previous_path or ''}"
            previous_snapshot = dashboard.cached_snapshot
            dashboard.cached_snapshot = lambda fresh: evidence
            server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                revision = snapshot(repo)["revision"]
                body = json.dumps(
                    {"revision": revision, "changes": {"manager.model": "new/model"}}
                ).encode()
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/settings",
                    body=body,
                    headers={"Content-Type": "application/json", "X-Factory-Act": "1"},
                )
                response = connection.getresponse()
                saved = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(saved["ok"], saved)

                connection = http.client.HTTPConnection(*server.server_address)
                request = json.dumps({"run": run, "question": "What happened?"}).encode()
                connection.request(
                    "POST",
                    "/api/ask",
                    body=request,
                    headers={"Content-Type": "application/json", "X-Factory-Act": "1"},
                )
                response = connection.getresponse()
                asked = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertTrue(asked["ok"], asked)
            finally:
                dashboard.cached_snapshot = previous_snapshot
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                if previous_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = previous_path
            self.assertIn("--model\nnew/model\n", args_path.read_text())


if __name__ == "__main__":
    unittest.main()
