"""Exclusive dashboard port checks on Darwin and Linux."""
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

    def test_check_refuses_a_reusable_reserved_port(self) -> None:
        with socket.socket() as holder:
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            holder.bind(("127.0.0.1", 0))
            # No listening socket appears in ss. The port is still reserved;
            # address reuse must not make it appear available.
            with patch.object(onboard, "sh", return_value=subprocess.CompletedProcess([], 0, "", "")):
                ok, detail = onboard._dashboard_port_check(self.cfg, "127.0.0.1", holder.getsockname()[1])
        self.assertFalse(ok, detail)
        self.assertIn("unknown process", detail)

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
