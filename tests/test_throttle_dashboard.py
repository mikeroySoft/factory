"""Throttle mode remains visible in dashboard and workspace observations."""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from factory import config, dashboard, throttle
from factory.workspace import Handler, Server, Workspace


class ThrottleDashboardTest(unittest.TestCase):
    def test_full_snapshot_reports_the_read_only_mode_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(dashboard.__dict__):
            cfg = config.Config(Path(directory), "acme/widgets", lock=Path(directory) / "gpu.lock")
            dashboard.configure(cfg)
            runtime = {
                "events": [],
                "executions": [],
                "resources": [],
                "history": {"status": "available", "complete": True},
                "errors": [],
            }
            dispatcher = {
                "service_active": False,
                "timer_active": False,
                "next_at": None,
                "paused": None,
                "observed_at": "2026-11-16T00:00:00Z",
                "observation": "fresh",
                "capacity": {"configured": cfg.max_active, "active": 0, "complete": True},
                "run_ids": [],
                "latest_transition": None,
            }
            mode = {"mode": "feature-drive", "feature": 826}
            with (
                mock.patch.object(dashboard.throttle, "read", return_value=mode) as read_mode,
                mock.patch.object(dashboard, "github", side_effect=RuntimeError("offline")),
                mock.patch.object(dashboard.runtime_events, "project", return_value=runtime),
                mock.patch.object(dashboard.runtime_local, "dispatcher", return_value=(dispatcher, [])),
                mock.patch.object(dashboard, "bounded_journal_rows", return_value=([], {
                    "status": "available", "scope": "retained_live_journal", "truncated": False,
                })),
                mock.patch.object(dashboard, "disk_ticket_numbers", return_value=set()),
                mock.patch.object(dashboard, "journal_runs", return_value=[]),
                mock.patch.object(dashboard, "_timer_last", return_value=None),
                mock.patch.object(dashboard, "upstream_state", return_value={}),
                mock.patch.object(dashboard, "triage_llm_online", return_value=False),
                mock.patch.object(dashboard.stats, "audit_by_ticket", return_value={}),
                mock.patch.object(dashboard.stats, "dispatcher_login", return_value="factory-bot"),
                mock.patch.object(dashboard.stats, "worker_metrics", return_value={}),
            ):
                snapshot = dashboard.snapshot()

            self.assertEqual(snapshot["throttle"], mode)
            read_mode.assert_called_once_with(cfg)

    def test_workspace_caches_real_mode_for_each_selected_repository(self) -> None:
        first, second = "acme/widgets", "acme/gadgets"
        first_cfg = SimpleNamespace(dashboard_theme=None)
        second_cfg = SimpleNamespace(dashboard_theme=None)
        workspace = object.__new__(Workspace)
        workspace.default_repository = first
        workspace._roots = {first: Path("/first"), second: Path("/second")}
        workspace._configs = {first: first_cfg, second: second_cfg}
        workspace._scopes = {
            slug: Workspace._new_scope(slug, root, "test")
            for slug, root in workspace._roots.items()
        }
        workspace._lock = threading.RLock()
        states = {
            id(first_cfg): {"mode": "normal", "feature": None},
            id(second_cfg): {"mode": "feature-drive", "feature": 826},
        }

        with (
            mock.patch.object(workspace, "request_refresh") as refresh,
            mock.patch("factory.workspace.throttle.read", side_effect=lambda cfg: states[id(cfg)]),
        ):
            workspace.data(first)
            data = workspace.data(second)

        rows = {row["slug"]: row for row in data["repositories"]}
        self.assertEqual(rows[first]["throttle"], states[id(first_cfg)])
        self.assertEqual(rows[second]["throttle"], states[id(second_cfg)])
        self.assertEqual(data["collection"]["selected_repository"], second)
        self.assertEqual(
            refresh.call_args_list,
            [mock.call(first, force=False), mock.call(second, force=False)],
        )

    def test_server_reports_real_selected_repository_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"XDG_CONFIG_HOME": str(Path(directory) / "xdg")}
        ):
            root = Path(directory) / "repo"
            root.mkdir()
            cfg = config.Config(root, "acme/widgets", lock=root / "gpu.lock")
            throttle.set_mode(cfg, "feature-drive", 826, who="test")
            workspace = Workspace(cfg)
            with workspace._lock:
                workspace._scopes[cfg.repo]["collection"]["_next"] = float("inf")
            server = Server(("127.0.0.1", 0), Handler, workspace)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=4)
                connection.request(
                    "GET",
                    f"/api/data?repository={cfg.repo}",
                    headers={
                        "Host": f"127.0.0.1:{server.server_port}",
                        "X-Factory-Access": workspace.access_key,
                    },
                )
                response = connection.getresponse()
                data = json.loads(response.read())
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            self.assertEqual(response.status, 200)
            self.assertEqual(data["repositories"][0]["throttle"], {
                "mode": "feature-drive", "feature": 826,
            })


if __name__ == "__main__":
    unittest.main()
