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
from tests import test_workspace_trace as workspace_trace


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


@unittest.skipUnless(workspace_trace.shutil.which("node"), "Node is required for browser asset checks")
class SchedulingHoldChronologyTest(unittest.TestCase):
    def test_latest_hold_keeps_journal_order_without_joining_pipeline(self) -> None:
        review = workspace_trace.decision("review", "REVISE", "2026-10-23T10:00:00Z")
        retry = workspace_trace.decision(
            "worker_select",
            "default",
            "2026-10-23T10:02:00Z",
            causes=[{"decision_id": review["decision_id"], "relation": "retries"}],
        )
        terminal = workspace_trace.decision("merge", "merge now", "2026-10-23T10:04:00Z")
        holds = [
            {
                "event": "scheduling",
                "mode": "maintenance",
                "at": at,
                "explained": [{"ticket": 7, "decision": "held", "reason": reason}],
            }
            for at, reason in (
                ("2026-10-23T10:01:00Z", "old reason"),
                ("2026-10-23T10:03:00Z", "latest reason"),
            )
        ]
        baseline = workspace_trace.trace.explain([review, retry, terminal], 7)

        graph = workspace_trace.trace.explain([review, holds[0], retry, holds[1], terminal], 7)

        self.assertEqual(
            [node["decision_id"] for node in graph["nodes"]],
            [
                review["decision_id"],
                retry["decision_id"],
                "scheduling:7:3",
                terminal["decision_id"],
            ],
        )
        self.assertEqual(
            [node["rationale"] for node in graph["nodes"] if node["chosen"] == "held"],
            ["maintenance: latest reason"],
        )
        for key in ("edges", "loops", "cost", "frontier", "frontier_stage", "coverage"):
            self.assertEqual(graph[key], baseline[key], key)

    def test_terminal_banner_and_replay_time_survive_hold_then_terminal(self) -> None:
        hold = {
            "event": "scheduling",
            "mode": "maintenance",
            "at": "2026-10-23T10:01:00Z",
            "explained": [{"ticket": 7, "decision": "held", "reason": "capacity reserved"}],
        }
        terminal_at = "2026-10-23T10:02:00Z"
        terminals = (
            ("merged", {"event": "merged", "ticket": 7, "at": terminal_at}),
            ("wontfix", workspace_trace.decision("wontfix", "duplicate", terminal_at)),
            ("escalated", workspace_trace.decision("escalate", "protected_path", terminal_at)),
        )

        for kind, terminal in terminals:
            with self.subTest(kind=kind):
                graph = workspace_trace.trace.explain([hold, terminal], 7)
                html = workspace_trace.WorkspaceTraceRenderTest.render(self, graph)

                self.assertIn(f'data-terminal="{kind}"', html)
                self.assertIn(f"<output data-trace-time>{terminal_at}</output>", html)
