"""Behavioral HTTP and concurrency checks for the native workspace transport."""
from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import briefing, config, decisions
from factory.workspace import ACCESS_FILE, MAX_CHAT_BODY, Handler, Server, Workspace

REPOSITORY = "acme/widgets"


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def make_repo(parent: Path, name: str = "repo", slug: str = REPOSITORY) -> Path:
    root = parent / name
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / config.CONFIG_NAME).write_text(f'[repo]\nslug = "{slug}"\n')
    return root


def source_envelope(root: Path, *, number: int | None = None) -> dict:
    value = {
        "schema_version": 1,
        "ok": True,
        "scope": {"repository": REPOSITORY, "root": str(root.resolve())},
        "observed_at": "2026-10-09T12:00:00+00:00",
        "coverage": {"status": "bounded", "notices": []},
        "sources": [{"id": "S1", "label": "Controlled evidence", "text": "bounded", "truncated": False}],
    }
    if number is None:
        value["cases"] = []
    else:
        value["case"] = {"number": number, "title": "Selected"}
    return value


class WorkspaceHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.environment = mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(self.base / "xdg")}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = make_repo(self.base)
        self.cfg = config.load(self.root)
        self.monitor = SimpleNamespace(
            state={
                "status": "ready",
                "error": None,
                "data": {"repo": REPOSITORY, "tip": "a" * 40},
            }
        )
        self.workspace = Workspace(self.cfg, codebase_monitor=self.monitor)
        self.server = Server(("127.0.0.1", 0), Handler, self.workspace)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    @property
    def authority(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    def request(
        self,
        method: str,
        path: str,
        body: object | bytes | str | None = None,
        *,
        auth: bool | str = False,
        intent: bool = False,
        host: str | None = None,
        origin: str | None = None,
    ) -> tuple[int, dict[str, str], object]:
        headers = {"Host": host or self.authority}
        if auth:
            headers["X-Factory-Access"] = self.workspace.access_key if auth is True else auth
        if intent:
            headers["X-Factory-Act"] = "1"
            headers["Origin"] = origin or f"http://{headers['Host']}"
        if body is not None:
            if not isinstance(body, (bytes, str)):
                body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
        finally:
            connection.close()
        try:
            value = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            value = raw
        return response.status, response_headers, value

    def test_public_assets_do_not_disclose_access_and_apis_require_authentication(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["content-security-policy"])
        self.assertNotIn("access-control-allow-origin", headers)
        status, _, _ = self.request("GET", "/workspace-codebase.js")
        self.assertEqual(status, 200)

        status, headers, _ = self.request("GET", "/chat")
        self.assertEqual((status, headers.get("location")), (302, "/?chat=1"))
        status, _, routed = self.request("GET", headers["location"])
        self.assertEqual(status, 200)
        status, _, _ = self.request("GET", "/api/codebase?repository=acme/widgets")
        self.assertEqual(status, 401)

        status, _, session = self.request("GET", "/api/session", host=f"localhost:{self.server.server_port}")
        self.assertEqual(status, 401)
        self.assertNotIn("access_key", session)
        self.assertEqual(
            stat.S_IMODE((self.root / ".factory" / ACCESS_FILE).stat().st_mode),
            0o600,
        )
        other = Workspace(self.cfg, codebase_monitor=self.monitor)
        self.assertEqual(other.access_key, self.workspace.access_key)
        other.close()
        status, _, codebase = self.request(
            "GET", "/api/codebase?repository=acme/widgets", auth=True
        )
        self.assertEqual(status, 200)
        self.assertEqual(codebase["data"]["repo"], REPOSITORY)
        self.assertEqual(codebase["root"], str(self.root.resolve()))

        status, _, body = self.request(
            "GET",
            "/api/codebase?repository=acme/widgets",
            auth=True,
            host=f"evil.example:{self.server.server_port}",
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "invalid_host")

    def test_idle_and_partial_headers_release_client_capacity(self) -> None:
        self.server.header_timeout = 0.3
        self.server._clients = threading.BoundedSemaphore(1)

        def slot_available() -> bool:
            if not self.server._clients.acquire(blocking=False):
                return False
            self.server._clients.release()
            return True

        for label, prefix in (
            ("idle", b""),
            ("partial headers", b"GET / HTTP/1.1\r\nHost: localhost"),
        ):
            with self.subTest(client=label):
                client = socket.create_connection(self.server.server_address, timeout=1)
                try:
                    if prefix:
                        client.sendall(prefix)
                    occupied = False
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if not slot_available():
                            occupied = True
                            break
                        time.sleep(0.01)
                    self.assertTrue(occupied, f"{label} client never occupied the slot")

                    released = False
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if slot_available():
                            released = True
                            break
                        time.sleep(0.01)
                    self.assertTrue(released, f"{label} client retained the slot past its timeout")
                finally:
                    client.close()

                status, _, _ = self.request("GET", "/")
                self.assertEqual(status, 200)


    def test_non_ascii_access_header_fails_closed_for_every_handler(self) -> None:
        for method in ("GET", "POST", "HEAD", "PUT"):
            with self.subTest(method=method):
                status, _, _ = self.request(method, "/api/data", auth="ä", intent=True)
                self.assertEqual(status, 401)

    def test_repository_theme_requires_access_and_rejects_nonregular_sources(self) -> None:
        theme = self.root / "theme.css"
        self.cfg.dashboard_theme = theme
        theme.write_text(":root { --bg: #203040; }")
        status, _, _ = self.request("GET", "/api/theme?repository=acme/widgets")
        self.assertEqual(status, 401)
        status, headers, _ = self.request("GET", "/api/theme?repository=acme/widgets", auth=True)
        self.assertEqual((status, headers["content-type"]), (200, "text/css; charset=utf-8"))
        theme.unlink()
        os.mkfifo(theme)
        status, _, _ = self.request("GET", "/api/theme?repository=acme/widgets", auth=True)
        self.assertEqual(status, 503)
        theme.unlink()
        theme.symlink_to(self.root / config.CONFIG_NAME)
        status, _, _ = self.request("GET", "/api/theme?repository=acme/widgets", auth=True)
        self.assertEqual(status, 503)

    def test_mutations_require_exact_origin_intent_and_route_closed_decisions(self) -> None:
        preview = {
            "proposal_id": "a" * 32,
            "confirmation": "confirm",
            "requests": [{"op": "issue", "number": 7, "comment": "go"}],
        }
        prepare_body = {"repository": REPOSITORY, "requests": preview["requests"]}
        with mock.patch.object(decisions, "prepare", return_value=preview) as prepare:
            status, _, _ = self.request(
                "POST", "/api/decisions/prepare", prepare_body, auth=True
            )
            self.assertEqual(status, 403)
            status, _, _ = self.request(
                "POST",
                "/api/decisions/prepare",
                prepare_body,
                auth=True,
                intent=True,
                origin="http://evil.example",
            )
            self.assertEqual(status, 403)
            status, _, result = self.request(
                "POST", "/api/decisions/prepare", prepare_body, auth=True, intent=True
            )
            self.assertEqual((status, result["proposal_id"]), (200, "a" * 32))
            called_cfg, called_requests = prepare.call_args.args
            self.assertEqual(called_cfg.root, self.root.resolve())
            self.assertEqual(called_requests, preview["requests"])

        receipt = {"proposal_id": "a" * 32, "status": "success", "ok": True}
        with mock.patch.object(decisions, "apply", return_value=receipt) as apply:
            status, _, result = self.request(
                "POST",
                "/api/decisions/apply",
                {
                    "repository": REPOSITORY,
                    "proposal_id": "a" * 32,
                    "confirmation": "confirm",
                },
                auth=True,
                intent=True,
            )
            self.assertEqual((status, result["status"]), (200, "success"))
            self.assertEqual(apply.call_args.args[1:], ("a" * 32, "confirm"))
            self.assertEqual(apply.call_args.args[0].root, self.root.resolve())

        observed = {**receipt, "observation": {"status": "observed"}}
        with mock.patch.object(decisions, "receipt", return_value=observed) as get_receipt:
            status, _, result = self.request(
                "GET",
                f"/api/decisions/receipt?repository={REPOSITORY}&id={'a' * 32}&observe=1",
                auth=True,
            )
            self.assertEqual((status, result["observation"]["status"]), (200, "observed"))
            self.assertEqual(get_receipt.call_args.args[1], "a" * 32)
            self.assertEqual(get_receipt.call_args.kwargs, {"observe": True})

        with mock.patch.object(
            decisions,
            "receipt",
            side_effect=decisions.DecisionError("missing", "not_found"),
        ):
            status, _, body = self.request(
                "GET",
                f"/api/decisions/receipt?repository={REPOSITORY}&id={'b' * 32}",
                auth=True,
            )
            self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        status, _, body = self.request(
            "POST", "/api/act", {}, auth=True, intent=True
        )
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))

    def test_queries_duplicate_json_and_body_limits_fail_closed(self) -> None:
        for path in (
            "/api/data?repository=../../etc",
            "/api/data?repository=acme/widgets&root=/tmp",
            "/api/data?repository=acme/widgets&repository=acme/widgets",
            "/api/data?refresh=0",
        ):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path, auth=True)
                self.assertEqual(status, 400)

        duplicate = (
            '{"repository":"acme/widgets","repository":"acme/widgets",'
            '"question":"What changed?"}'
        )
        with mock.patch.object(briefing, "run_model") as model:
            status, _, body = self.request(
                "POST", "/api/chat", duplicate, auth=True, intent=True
            )
            self.assertEqual((status, body["error"]["code"]), (400, "invalid_request"))
            status, _, body = self.request(
                "POST", "/api/chat", b"x" * (MAX_CHAT_BODY + 1), auth=True, intent=True
            )
            self.assertEqual((status, body["error"]["code"]), (413, "request_too_large"))
            model.assert_not_called()

    def test_cold_data_returns_immediately_and_refreshes_single_flight_without_model(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def collect(_slug: str) -> None:
            started.set()
            release.wait(3)

        try:
            with mock.patch.object(self.workspace, "_collect_repository", side_effect=collect) as collector, \
                 mock.patch.object(briefing, "run_model") as model:
                with self.workspace._lock:
                    self.workspace._scopes[REPOSITORY]["collection"]["_next"] = float("inf")
                status, _, deferred = self.request(
                    "GET", "/api/data?repository=acme/widgets", auth=True
                )
                self.assertEqual(status, 200)
                self.assertFalse(deferred["collection"]["refreshing"])
                self.assertFalse(started.is_set())
                status, _, first = self.request(
                    "GET", "/api/data?repository=acme/widgets&refresh=1", auth=True
                )
                self.assertEqual(status, 200)
                self.assertTrue(started.wait(1), "collector did not start off the request thread")
                self.assertTrue(first["collection"]["refreshing"])
                self.assertIsNone(first["repositories"][0]["observation"])
                status, _, second = self.request(
                    "GET", "/api/data?repository=acme/widgets&refresh=1", auth=True
                )
                self.assertEqual(status, 200)
                self.assertTrue(second["collection"]["refreshing"])
                self.assertEqual(collector.call_count, 1)
                status, _, _ = self.request(
                    "GET", "/api/chat/meta?repository=acme/widgets", auth=True
                )
                self.assertEqual(status, 200)
                model.assert_not_called()
        finally:
            release.set()

    def test_completed_observation_and_runtime_publish_before_slow_roadmap(self) -> None:
        roadmap_started = threading.Event()
        release = threading.Event()
        observation = source_envelope(self.root)
        runtime_data = {
            "executions": [],
            "events": [],
            "resources": [],
            "history": {"status": "available", "complete": True},
            "errors": [],
        }
        with self.workspace._lock:
            self.workspace._scopes[REPOSITORY]["capabilities"] = {"ok": True}

        def read(_slug: str, _request: dict, operation: str):
            if operation == "observation":
                return observation, None
            roadmap_started.set()
            release.wait(3)
            return {"ok": True, "observed_at": "2026-01-01T00:00:00+00:00"}, None

        try:
            with (
                mock.patch.object(self.workspace, "_read_evidence", side_effect=read),
                mock.patch(
                    "factory.workspace.runtime_events.project", return_value=runtime_data
                ),
                mock.patch(
                    "factory.workspace.runtime_local.dispatcher",
                    return_value=({"observation": "fresh"}, []),
                ),
                mock.patch(
                    "factory.workspace.flow.project", return_value={"schema_version": 1}
                ),
            ):
                status, _, _ = self.request(
                    "GET", "/api/data?repository=acme/widgets", auth=True
                )
                self.assertEqual(status, 200)
                self.assertTrue(roadmap_started.wait(1))
                status, _, current = self.request(
                    "GET", "/api/data?repository=acme/widgets", auth=True
                )
                self.assertEqual(status, 200)
                row = current["repositories"][0]
                self.assertIsNotNone(row["observation"])
                self.assertEqual(row["runtime"]["schema_version"], 1)
                self.assertEqual(row["runtime"]["repo"], REPOSITORY)
        finally:
            release.set()

    def test_collector_admission_reports_busy_without_queuing(self) -> None:
        for _ in range(2):
            self.workspace._collector_slots.acquire()
        try:
            status, _, data = self.request(
                "GET", "/api/data?repository=acme/widgets", auth=True
            )
            self.assertEqual(status, 200)
            self.assertFalse(data["collection"]["refreshing"])
            self.assertEqual(data["collection"]["error"]["code"], "collector_busy")
            status, _, snapshot = self.request(
                "GET", "/api/snapshot?repository=acme/widgets", auth=True
            )
            self.assertEqual(status, 503)
            self.assertEqual(snapshot["error"]["code"], "collector_busy")
        finally:
            for _ in range(2):
                self.workspace._collector_slots.release()

    def test_cold_full_snapshot_is_202_and_coalesced_off_request_thread(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def collect(_slug: str) -> None:
            started.set()
            release.wait(3)

        try:
            with mock.patch.object(self.workspace, "_collect_full_snapshot", side_effect=collect) as collector:
                status, _, first = self.request(
                    "GET", "/api/snapshot?repository=acme/widgets", auth=True
                )
                self.assertEqual((status, first["status"]), (202, "building"))
                self.assertTrue(started.wait(1), "full snapshot did not start off the request thread")
                status, _, second = self.request(
                    "GET", "/api/snapshot?repository=acme/widgets&fresh=1", auth=True
                )
                self.assertEqual((status, second["status"]), (202, "building"))
                self.assertEqual(collector.call_count, 1)
        finally:
            release.set()

    def test_snapshot_preserves_audit_coverage_and_adds_transport_bounds(self) -> None:
        legacy = {"status": "partial", "truncated": True}
        with self.workspace._lock:
            state = self.workspace._snapshots[REPOSITORY]
            state["data"] = {
                "repo": REPOSITORY,
                "root": str(self.root.resolve()),
                "coverage": {"legacy": legacy, "counts": {"spend": {"complete": False}}},
            }
            state["_next"] = float("inf")
        status, _, snapshot = self.request(
            "GET", "/api/snapshot?repository=acme/widgets", auth=True
        )
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["coverage"]["legacy"], legacy)
        self.assertIn("counts", snapshot["coverage"])
        self.assertEqual(snapshot["coverage"]["transport"]["wall_seconds"], 120)

    def test_inspect_capacity_is_immediate_503(self) -> None:
        for _ in range(3):
            self.workspace._process_slots.acquire()
        try:
            status, _, body = self.request(
                "GET", "/api/inspect?repository=acme/widgets&number=7", auth=True
            )
        finally:
            for _ in range(3):
                self.workspace._process_slots.release()
        self.assertEqual((status, body["error"]["code"]), (503, "reader_busy"))

    def test_selected_chat_requires_matching_cached_inspection(self) -> None:
        with self.workspace._lock:
            self.workspace._scopes[REPOSITORY]["observation"] = source_envelope(self.root)
        with mock.patch.object(briefing, "run_model") as model:
            status, _, body = self.request(
                "POST",
                "/api/chat",
                {"repository": REPOSITORY, "question": "What next?", "number": 7},
                auth=True,
                intent=True,
            )
            self.assertEqual((status, body["error"]["code"]), (409, "inspection_required"))
            with self.workspace._lock:
                self.workspace._inspections[(REPOSITORY, 7)] = source_envelope(
                    self.root, number=8
                )
            status, _, body = self.request(
                "POST",
                "/api/chat",
                {"repository": REPOSITORY, "question": "What next?", "number": 7},
                auth=True,
                intent=True,
            )
            self.assertEqual((status, body["error"]["code"]), (409, "inspection_required"))
            model.assert_not_called()

    def test_chat_duplicate_work_is_single_flight_then_cached(self) -> None:
        with self.workspace._lock:
            self.workspace._scopes[REPOSITORY]["observation"] = source_envelope(self.root)
        entered = threading.Event()
        release = threading.Event()
        first: list[tuple[int, dict[str, str], object]] = []

        def answer(_cfg, _prompt: str) -> str:
            entered.set()
            release.wait(3)
            return "Grounded answer [S1]"

        def ask() -> None:
            first.append(self.request(
                "POST",
                "/api/chat",
                {"repository": REPOSITORY, "question": "Repository state?"},
                auth=True,
                intent=True,
            ))

        with mock.patch.object(briefing, "run_model", side_effect=answer) as model:
            thread = threading.Thread(target=ask)
            thread.start()
            self.assertTrue(entered.wait(1))
            status, _, body = self.request(
                "POST",
                "/api/chat",
                {"repository": REPOSITORY, "question": "Repository state?"},
                auth=True,
                intent=True,
            )
            self.assertEqual((status, body["error"]["code"]), (503, "chat_busy"))
            release.set()
            thread.join(timeout=3)
            self.assertEqual(first[0][0], 200)
            status, _, cached = self.request(
                "POST",
                "/api/chat",
                {"repository": REPOSITORY, "question": "Repository state?"},
                auth=True,
                intent=True,
            )
            self.assertEqual((status, cached["answer"]), (200, "Grounded answer [S1]"))
            self.assertEqual(model.call_count, 1)

    def test_partial_evidence_keeps_last_good_but_current_runtime_is_published(self) -> None:
        observation = {"ok": True, "observed_at": "2026-01-01T00:00:00+00:00"}
        roadmap = {"ok": True, "observed_at": "2026-01-01T00:00:00+00:00"}
        old_runtime = {"generated_at": "2026-01-01T00:00:00+00:00", "errors": []}
        with self.workspace._lock:
            scope = self.workspace._scopes[REPOSITORY]
            scope.update(
                capabilities={"ok": True},
                observation=observation,
                roadmap=roadmap,
                runtime=old_runtime,
            )

        partial = {
            "ok": False,
            "observed_at": "2026-01-02T00:00:00+00:00",
            "error": {"code": "partial"},
        }
        error = {"code": "partial", "message": "partial evidence"}
        partial_runtime = {
            "executions": [],
            "events": [],
            "resources": [],
            "history": {"status": "available", "complete": False},
            "errors": [{"code": "bad_event"}],
        }
        with (
            mock.patch.object(self.workspace, "_read_evidence", return_value=(partial, error)),
            mock.patch(
                "factory.workspace.runtime_events.project", return_value=partial_runtime
            ),
            mock.patch(
                "factory.workspace.runtime_local.dispatcher",
                return_value=({"observation": "partial"}, []),
            ),
            mock.patch("factory.workspace.flow.project", return_value={"schema_version": 1}) as project,
        ):
            self.workspace._collect_repository(REPOSITORY)

        with self.workspace._lock:
            scope = self.workspace._scopes[REPOSITORY]
            self.assertIs(scope["observation"], observation)
            self.assertIs(scope["roadmap"], roadmap)
            self.assertIsNot(scope["runtime"], old_runtime)
            self.assertEqual(scope["runtime"]["schema_version"], 1)
            self.assertEqual(scope["runtime"]["repo"], REPOSITORY)
            self.assertEqual(scope["runtime"]["dispatcher"]["observation"], "partial")
            self.assertTrue(scope["errors"])
            self.assertEqual(scope["collection"]["error"]["code"], "partial")
            current_runtime = scope["runtime"]
        project.assert_called_once_with(observation, current_runtime)

    def test_settings_use_registered_root_and_revision_guard(self) -> None:
        status, _, current = self.request(
            "GET", "/api/settings?repository=acme/widgets", auth=True
        )
        self.assertEqual(status, 200)
        self.assertTrue(current["ok"], current)
        status, _, saved = self.request(
            "POST",
            "/api/settings?repository=acme/widgets",
            {
                "revision": current["revision"],
                "changes": {"dispatch.max_active": 7},
            },
            auth=True,
            intent=True,
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["fields"]["dispatch.max_active"]["value"], 7)
        self.assertEqual(config.load(self.root).max_active, 7)
        status, _, stale = self.request(
            "POST",
            "/api/settings?repository=acme/widgets",
            {
                "repository": REPOSITORY,
                "revision": current["revision"],
                "changes": {"dispatch.max_active": 8},
            },
            auth=True,
            intent=True,
        )
        self.assertEqual(status, 200)
        self.assertFalse(stale["ok"])
        self.assertEqual(config.load(self.root).max_active, 7)


class WorkspaceRegistryTest(unittest.TestCase):
    def test_registered_main_overrides_same_slug_startup_but_key_stays_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(base / "xdg")}):
                startup = make_repo(base, "staging")
                registered = make_repo(base, "registered")
                host = config.host_config_path()
                host.parent.mkdir(parents=True)
                host.write_text(
                    f'[repo."{REPOSITORY}"]\npath = {json.dumps(str(registered))}\n'
                )
                cfg = config.load(startup)
                monitor = SimpleNamespace(
                    state={"status": "ready", "data": {"repo": REPOSITORY, "tip": "clone"}}
                )
                workspace = Workspace(cfg, codebase_monitor=monitor)
                try:
                    self.assertEqual(workspace._roots[REPOSITORY], registered.resolve())
                    with workspace._lock:
                        row = workspace._scopes[REPOSITORY]
                        self.assertEqual(row["root"], str(registered.resolve()))
                        self.assertEqual(row["scope_source"], "host_registry")
                    self.assertTrue((startup / ".factory" / ACCESS_FILE).is_file())
                    self.assertFalse((registered / ".factory" / ACCESS_FILE).exists())
                    self.assertNotEqual(workspace._roots[REPOSITORY], workspace.startup_root)
                    cache = registered / ".factory" / "codebase"
                    cache.mkdir(parents=True)
                    cache.joinpath("history.json").write_text(
                        json.dumps(
                            {
                                "schema": 1,
                                "repo": REPOSITORY,
                                "generated_at": "2026-01-01T00:00:00Z",
                                "snapshots": [],
                                "slots": [],
                            }
                        )
                    )
                    published = workspace.codebase(REPOSITORY)
                    self.assertEqual(published["status"], "ready")
                    self.assertEqual(published["data"]["repo"], REPOSITORY)
                    self.assertEqual(
                        published["provenance"],
                        {"source": "published cache", "monitor_status": "unknown"},
                    )
                    current = workspace.settings_snapshot(REPOSITORY)
                    before = (registered / config.CONFIG_NAME).read_bytes()
                    replacement = make_repo(base, "replacement")
                    host.write_text(
                        f'[repo."{REPOSITORY}"]\n'
                        f"path = {json.dumps(str(replacement))}\n"
                    )
                    with self.assertRaisesRegex(RuntimeError, "mapping changed"):
                        workspace.save_settings(
                            REPOSITORY,
                            {
                                "revision": current["revision"],
                                "changes": {"dispatch.max_active": 9},
                            },
                        )
                    self.assertEqual(
                        (registered / config.CONFIG_NAME).read_bytes(),
                        before,
                    )
                finally:
                    workspace.close()

    def test_unsafe_persistent_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(base / "xdg")}):
                root = make_repo(base)
                factory = root / ".factory"
                factory.mkdir(mode=0o700)
                key = factory / ACCESS_FILE
                key.write_text("a" * 43)
                key.chmod(0o644)
                cfg = config.load(root)
                with self.assertRaisesRegex(RuntimeError, "unsafe"):
                    Workspace(cfg)
                self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
