"""Dashboard bind failures remain actionable CLI errors."""

import contextlib
import errno
import io
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory import dashboard


class DashboardBindTest(unittest.TestCase):
    def run_dashboard(self, port, host="127.0.0.1"):
        cfg = SimpleNamespace(dashboard_port=port, repo="acme/widgets")
        stderr = io.StringIO()
        with patch.object(dashboard.config, "load", return_value=cfg), \
             patch.object(dashboard, "configure"), \
             patch.object(dashboard, "cfg", cfg, create=True), \
             patch.object(dashboard.config, "host_config_path", return_value=Path("/host/factory/config.toml")), \
             contextlib.redirect_stderr(stderr):
            result = dashboard.main(["--port", str(port), "--host", host, "--no-open"])
        self.assertEqual(result, 1)
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertNotIn("Traceback", stderr.getvalue())
        return stderr.getvalue()

    def test_occupied_port(self):
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            holder.listen()
            port = holder.getsockname()[1]
            self.assertEqual(self.run_dashboard(port),
                f'factory dashboard: port {port} on 127.0.0.1 is in use; '
                'set [repo."acme/widgets".dashboard] port in /host/factory/config.toml '
                '(each factory needs its own port)\n')

    def test_other_bind_errors(self):
        for code in (errno.EACCES, errno.EADDRNOTAVAIL):
            with self.subTest(code=code), patch.object(
                dashboard, "ThreadingHTTPServer", side_effect=OSError(code, "bind denied")
            ):
                self.assertIn("bind denied", self.run_dashboard(8123))
