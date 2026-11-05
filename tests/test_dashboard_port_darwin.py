"""Dashboard port checks preserve reusable binds and detect Darwin holders."""
from __future__ import annotations

import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, onboard


class DashboardExclusivePortTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cfg = config.Config(Path(temporary.name), "acme/widgets", signoff=False)

    def test_check_allows_rebind_after_an_accepted_connection(self) -> None:
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen()
            port = server.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port)) as client:
                connection, _ = server.accept()
                # The server closes first, leaving its port in TIME_WAIT.
                connection.close()
                self.assertEqual(client.recv(1), b"")
        # The real dashboard can rebind; doctor must not report a false conflict.
        with socket.socket() as replacement:
            replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            replacement.bind(("127.0.0.1", port))
            replacement.listen()
        with patch.object(onboard, "sh", return_value=subprocess.CompletedProcess([], 0, "", "")):
            ok, detail = onboard._dashboard_port_check(self.cfg, "127.0.0.1", port)
        self.assertTrue(ok, detail)

    def test_check_refuses_a_dual_stack_wildcard_holder(self) -> None:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as holder:
            holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            holder.bind(("::", 0))
            holder.listen()
            port = holder.getsockname()[1]
            listeners = subprocess.CompletedProcess(
                [], 0, f'LISTEN 0 5 [::]:{port} *:* users:(("foreign",pid=4321,fd=3))\n', ""
            )
            with patch.object(onboard, "sh", side_effect=[
                listeners, subprocess.CompletedProcess([], 0, "0\n", "")
            ]):
                ok, detail = onboard._dashboard_port_check(self.cfg, "127.0.0.1", port)
        self.assertFalse(ok, detail)
        self.assertIn("foreign", detail)


if __name__ == "__main__":
    unittest.main()
