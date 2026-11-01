"""Behavioral HTTP and concurrency checks for the native workspace transport."""
from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import actions, briefing, config, decisions
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

    def test_reader_accepts_output_at_bound_and_refuses_overflow(self) -> None:
        payload = '{"answer":42}'
        for maximum, expected_error in ((len(payload), None), (len(payload) - 1, "response_too_large")):
            with self.subTest(maximum=maximum):
                value, error, _ = self.workspace._run_json(
                    [sys.executable, "-c", f"import sys; sys.stdout.write({payload!r}); sys.stderr.write('diagnostic')"],
                    cwd=self.root, body=None, timeout=3, maximum=maximum, operation="observe",
                )
                if expected_error:
                    self.assertIsNone(value)
                    self.assertEqual(error["code"], expected_error)
                else:
                    self.assertIsNone(error)
                    self.assertEqual(value, {"answer": 42})

    def test_evidence_requires_matching_repository_and_root(self) -> None:
        for repository, root, accepted in (
            (REPOSITORY, str(self.root), True),
            ("acme/other", str(self.root), False),
            (REPOSITORY, str(self.base / "other"), False),
        ):
            with self.subTest(repository=repository, root=root):
                envelope = source_envelope(self.root)
                envelope["scope"] = {"repository": repository, "root": root}
                with mock.patch.object(self.workspace, "_run_json", return_value=(envelope, None, 0)):
                    value, error = self.workspace._read_evidence(REPOSITORY, {"op": "observe"}, "observe")
                if accepted:
                    self.assertIsNone(error)
                else:
                    self.assertIsNone(value)
                    self.assertEqual(error["code"], "invalid_response")

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
        status, _, _ = self.request("GET", headers["location"])
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

        status, _, ticket_flow = self.request("GET", "/api/ticket-flow", auth=True)
        self.assertEqual(status, 200)
        self.assertEqual(set(ticket_flow), {"nodes", "edges", "source"})
        self.assertEqual(ticket_flow["source"]["path"], "docs/ticket-flow.md")
        revision = ticket_flow["source"]["revision"]
        if revision is None:
            self.assertIsNone(ticket_flow["source"]["url"])
        else:
            self.assertIn(revision, ticket_flow["source"]["url"])
        self.assertTrue(ticket_flow["nodes"])
        self.assertTrue(ticket_flow["edges"])
        status, _, body = self.request(
            "GET", "/api/ticket-flow?repository=acme/widgets", auth=True
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_query")

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
        admitted = threading.Event()
        bounded_request = self.server._bounded_request

        def observe_admission(request, address) -> None:
            admitted.set()
            bounded_request(request, address)

        self.server._bounded_request = observe_admission

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
                # A complete response can arrive before its handler releases the slot.
                self.assertTrue(self.server._clients.acquire(timeout=2))
                self.server._clients.release()
                admitted.clear()
                client = socket.create_connection(self.server.server_address, timeout=1)
                try:
                    if prefix:
                        client.sendall(prefix)
                    # Probing before admission can take the only slot and cause a 503.
                    self.assertTrue(admitted.wait(2), f"{label} client was not admitted")
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
            self.assertEqual(prepare.call_args.kwargs, {"caller": "dashboard"})
        with mock.patch.object(actions, "prepare", return_value=preview) as prepare:
            action_body = {"repository": REPOSITORY, "action": "requeue_agent", "args": {"number": 7}}
            status, _, result = self.request("POST", "/api/decisions/prepare", action_body, auth=True, intent=True)
            self.assertEqual((status, result["proposal_id"]), (200, "a" * 32))
            self.assertEqual(prepare.call_args.args[1:], ("requeue_agent", {"number": 7}))
            self.assertEqual(prepare.call_args.kwargs, {"caller": "dashboard"})
            prepare.side_effect = decisions.DecisionError("issue is not open", "precondition_failed")
            status, _, result = self.request("POST", "/api/decisions/prepare", action_body, auth=True, intent=True)
            self.assertEqual((status, result["error"]["code"]), (409, "precondition_failed"))
            status, _, result = self.request("POST", "/api/decisions/prepare", {**action_body, "requests": []}, auth=True, intent=True)
            self.assertEqual((status, result["error"]["code"]), (400, "invalid_request"))

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


    def test_trace_requires_access_key(self) -> None:
        status, _, body = self.request("GET", "/api/trace?ticket=7")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "authentication_required")

    def test_trace_rejects_missing_or_invalid_ticket(self) -> None:
        for path in ("/api/trace", "/api/trace?ticket=nope", "/api/trace?ticket=0"):
            with self.subTest(path=path):
                status, _, body = self.request("GET", path, auth=True)
                self.assertEqual(status, 400)
                self.assertIn(body["error"]["code"], {"invalid_query", "invalid_request"})

    def test_trace_returns_empty_graph_for_unknown_ticket(self) -> None:
        from factory import trace

        status, _, body = self.request("GET", "/api/trace?ticket=404", auth=True)
        self.assertEqual(status, 200)
        self.assertEqual(body, trace.explain([], 404))

    def test_trace_returns_explain_projection_unchanged(self) -> None:
        from factory import lifecycle, trace

        row = {
            "event": "decision",
            **trace.decision(
                ticket=7,
                stage="triage",
                inputs=[{"kind": "issue", "ref": "https://github.com/acme/widgets/issues/7",
                         "summary": "Trace fixture"}],
                options=[
                    {"option": "ready-for-agent", "eligible": True, "reason": "selected"},
                    {"option": "needs-info", "eligible": False, "reason": "complete"},
                ],
                chosen="ready-for-agent",
                rationale="The ticket is complete.",
                causes=[],
                cost={"prompt_tokens": 10, "completion_tokens": 2, "seconds": 0.5},
                actor={"kind": "model", "name": "triage", "model": "fixture", "version": "1"},
                at="2026-10-23T10:00:00Z",
                decision_id="00000000-0000-4000-8000-000000000007",
            ),
        }
        lifecycle.append(self.cfg.factory / "events.jsonl", row)

        status, _, body = self.request(
            "GET", f"/api/trace?ticket=7&repository={REPOSITORY}", auth=True
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, trace.explain(trace.read_rows(self.cfg.factory / "events.jsonl"), 7))


    def test_decisions_view_asset_redirect_and_shareable_route(self) -> None:
        status, _, asset = self.request("GET", "/workspace-trace.js")
        self.assertEqual(status, 200)
        self.assertIn(b"WorkspaceTrace", asset)

        status, headers, _ = self.request("GET", "/decisions")
        self.assertEqual((status, headers.get("location")), (302, "/?view=decisions"))
        status, _, _ = self.request("GET", "/?view=decisions&ticket=7")
        self.assertEqual(status, 200)
        status, _, body = self.request("GET", "/?view=decisions&ticket=bad")
        self.assertEqual((status, body["error"]["code"]), (400, "invalid_query"))


    def test_live_executions_are_authenticated_read_only_and_cover_every_loop(self) -> None:
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stages = ("triage", "worker", "gate", "review", "manage", "watch", "learn")
        executions = [
            {
                "execution_id": f"execution-{index}",
                "stage": stage,
                "ticket": None if stage in {"watch", "learn"} else index,
                "state": "active",
                "entered_at": observed,
                "ended_at": None,
                "observed_at": observed,
                "latest_at": observed,
                "activity": {"worker": "prime"} if stage == "worker" else {},
            }
            for index, stage in enumerate(stages, 1)
        ]
        executions.append({
            **executions[1],
            "execution_id": "ended",
            "state": "completed",
            "ended_at": observed,
        })
        with self.workspace._lock:
            self.workspace._scopes[REPOSITORY]["observation"] = {
                "cases": [
                    {"number": index, "title": f"Ticket {index}"}
                    for index in range(1, 8)
                ]
            }

        with mock.patch(
            "factory.workspace.runtime_events.project",
            return_value={"executions": executions, "errors": []},
        ) as project:
            status, _, body = self.request(
                "GET", f"/api/live-executions?repository={REPOSITORY}"
            )
            self.assertEqual((status, body["error"]["code"]), (401, "authentication_required"))

            status, _, body = self.request(
                "GET", f"/api/live-executions?repository={REPOSITORY}", auth=True
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                body["lanes"],
                ["triage", "worker", "gate", "reviewer", "manager", "watch", "learn"],
            )
            self.assertEqual(
                [row["lane"] for row in body["executions"]],
                body["lanes"],
            )
            self.assertTrue(all(row["agent"] and row["harness"] for row in body["executions"]))
            self.assertEqual(
                [(row["ticket"], row["title"]) for row in body["executions"]],
                [
                    *((index, f"Ticket {index}") for index in range(1, 6)),
                    (None, "Repository watch"),
                    (None, "Repository learn"),
                ],
            )
            self.assertNotIn("ended", {row["execution_id"] for row in body["executions"]})

            status, _, _ = self.request(
                "POST",
                f"/api/live-executions?repository={REPOSITORY}",
                {},
                auth=True,
                intent=True,
            )
            self.assertIn(status, {400, 404, 405})
            project.assert_called_once()

    def test_live_executions_ignore_dispatcher_ticket_scope(self) -> None:
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        executions = [
            {
                "execution_id": stage,
                "stage": stage,
                "ticket": 7,
                "state": "active",
                "entered_at": observed,
                "ended_at": None,
                "observed_at": observed,
                "activity": {"worker": "default"} if stage == "worker" else {},
            }
            for stage in ("ticket", "worker")
        ]
        with mock.patch(
            "factory.workspace.runtime_events.project",
            return_value={"executions": executions, "errors": []},
        ):
            body = self.workspace.live_executions(REPOSITORY)

        self.assertEqual(
            [(row["execution_id"], row["lane"]) for row in body["executions"]],
            [("worker", "worker")],
        )


    def test_live_umbrella_only_ticket_stays_visible_without_a_running_agent(self) -> None:
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        execution = {
            "execution_id": "pipeline",
            "stage": "ticket",
            "ticket": 7,
            "state": "active",
            "entered_at": "2026-01-01T00:00:00Z",
            "latest_at": "2026-01-01T00:00:00Z",
            "observed_at": observed,
            "ended_at": None,
        }
        with mock.patch(
            "factory.workspace.runtime_events.project",
            return_value={"executions": [execution], "errors": []},
        ):
            body = self.workspace.live_executions(REPOSITORY)

        self.assertEqual(len(body["executions"]), 1)
        row = body["executions"][0]
        self.assertEqual((row["ticket"], row["lane"]), (7, "worker"))
        self.assertEqual(row["activity"], "pipeline")
        self.assertEqual(row["agent"], "No running agent")
        self.assertFalse(row["stale"], "A fresh process observation is not an old transition")

    def test_live_worker_harness_excludes_host_launch_wrapper(self) -> None:
        self.cfg.workers["wrapped"] = ["/opt/bin/omp", "--prompt", "{prompt}"]
        self.cfg.worker_wrap = ["/opt/bin/resource-wrapper", "--"]
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        execution = {
            "execution_id": "wrapped",
            "stage": "worker",
            "ticket": 7,
            "state": "active",
            "entered_at": observed,
            "observed_at": observed,
            "activity": {"worker": "wrapped"},
        }
        with mock.patch(
            "factory.workspace.runtime_events.project",
            return_value={"executions": [execution], "errors": []},
        ):
            body = self.workspace.live_executions(REPOSITORY)

        row = body["executions"][0]
        self.assertEqual((row["agent"], row["harness"]), ("wrapped", "omp"))


    def test_live_worker_uses_its_own_journaled_profile_not_the_latest_ticket_event(self) -> None:
        from factory import lifecycle

        self.cfg.workers["actual"] = ["/opt/bin/omp", "{prompt}"]
        self.cfg.worker_wrap = ["/opt/bin/wrapper", "--"]
        journal = self.cfg.factory / "events.jsonl"
        with (
            mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            lifecycle.scope(journal, "worker", ticket=7, worker="actual"),
        ):
            with journal.open("a") as handle:
                handle.write(json.dumps({
                    "event": "attempt", "ticket": 7, "worker": "default",
                    "title": "Correct profile",
                }) + "\n")
            body = self.workspace.live_executions(REPOSITORY)

        row = next(row for row in body["executions"] if row["ticket"] == 7)
        self.assertEqual((row["agent"], row["harness"]), ("actual", "omp"))

    def test_live_reviewer_uses_journaled_identity_for_second_review(self) -> None:
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        execution = {
            "execution_id": "second-review",
            "stage": "review",
            "ticket": 7,
            "state": "active",
            "entered_at": observed,
            "observed_at": observed,
            "activity": {"model": "second/model", "harness": "second-reviewer"},
        }
        with mock.patch(
            "factory.workspace.runtime_events.project",
            return_value={"executions": [execution], "errors": []},
        ):
            body = self.workspace.live_executions(REPOSITORY)

        row = body["executions"][0]
        self.assertEqual((row["agent"], row["harness"]), ("second/model", "second-reviewer"))



    def test_live_learn_records_its_command_identity_before_configuration_changes(self) -> None:
        from factory import dispatch, learn, lifecycle

        self.cfg.manager = ["/opt/bin/omp", "--model", "command/model", "@{prompt}"]
        self.cfg.manager_model = "dashboard/briefing"
        journal = self.cfg.factory / "events.jsonl"
        seen = {}

        def running(*_args):
            self.cfg.manager = ["/opt/bin/other", "--model", "changed/model"]
            seen.update(self.workspace.live_executions(REPOSITORY))

        with (
            mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            mock.patch.object(dispatch, "EVENTS", journal, create=True),
            mock.patch.object(learn, "evidence", return_value=([7], "evidence")),
            mock.patch.object(learn, "_run", side_effect=running),
        ):
            learn.run(self.cfg)

        row = next(row for row in seen["executions"] if row["lane"] == "learn")
        self.assertEqual((row["agent"], row["harness"]), ("command/model", "omp"))
        entry = next(row for row in lifecycle.read_events(journal) if row.get("kind") == "enter")
        self.assertEqual((entry.get("model"), entry.get("harness")), ("command/model", "omp"))

    def test_live_manager_and_learn_never_borrow_the_dashboard_briefing_model(self) -> None:
        self.cfg.manager_model = "dashboard/briefing"
        observed = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for command, expected in (
            (["/opt/bin/omp", "--model", "command/model"], ("command/model", "omp")),
            (["/opt/bin/omp"], ("manager", "omp")),
        ):
            self.cfg.manager = command
            for stage in ("manage", "learn"):
                with self.subTest(command=command, stage=stage), mock.patch(
                    "factory.workspace.runtime_events.project",
                    return_value={"executions": [{
                        "execution_id": stage, "stage": stage, "ticket": 7,
                        "state": "active", "entered_at": observed, "observed_at": observed,
                    }], "errors": []},
                ):
                    [row] = self.workspace.live_executions(REPOSITORY)["executions"]
                    self.assertEqual((row["agent"], row["harness"]), expected)

    def test_live_worker_keeps_a_removed_recorded_profile(self) -> None:
        from factory import lifecycle

        journal = self.cfg.factory / "events.jsonl"
        with (
            mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            lifecycle.scope(journal, "worker", ticket=7, worker="removed-profile"),
        ):
            [row] = self.workspace.live_executions(REPOSITORY)["executions"]
        self.assertEqual((row["agent"], row["harness"]), ("removed-profile", "harness unavailable"))

    def test_live_watch_distinguishes_model_diagnosis_from_deterministic_activity(self) -> None:
        from factory import dispatch, lifecycle, watch

        seen = {}
        journal = self.cfg.factory / "events.jsonl"

        def diagnose(*_args):
            seen.update(self.workspace.live_executions(REPOSITORY))
            return "diagnosis"

        with (
            mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""}),
            mock.patch.object(dispatch, "EVENTS", journal, create=True),
            mock.patch("factory.triage.call_llm", side_effect=diagnose),
            lifecycle.scope(journal, "watch"),
        ):
            watch._diagnose(self.cfg, {"excerpt": "alarm"})

        identities = {(row["agent"], row["harness"]) for row in seen["executions"]}
        self.assertIn(("deterministic", "factory watch"), identities)
        self.assertIn((self.cfg.llm_model, "factory triage"), identities)


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
