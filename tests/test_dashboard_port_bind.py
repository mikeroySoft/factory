"""Dashboard-port detection against the host kernel's bind and dual-stack semantics."""
from __future__ import annotations

import errno
import json
import socket
import subprocess
import unittest
from unittest.mock import patch

from factory import onboard, platform_identity
from tests import test_dashboard_port as base

HEADER = "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"


def bind_error(family: int, address: str, port: int) -> int | None:
    """errno of an SO_REUSEADDR bind like the dashboard's, or None when it binds."""
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((address, port))
        except OSError as exc:
            return exc.errno
        return None


class DashboardPortBindTest(unittest.TestCase):
    setUp = base.DashboardPortTest.setUp
    doctor = base.DashboardPortTest.doctor
    ss_line = staticmethod(base.DashboardPortTest.ss_line)

    def holder(self, family: int, address: str, *, v6only: int | None = None, reuseport: bool = False) -> int:
        listener = socket.socket(family, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        if v6only is not None:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
        if reuseport:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        listener.bind((address, 0))
        listener.listen()
        return listener.getsockname()[1]

    def port_row(self, ss: subprocess.CompletedProcess) -> tuple[int, dict]:
        code, output, _ = self.doctor(ss)
        label = f"dashboard port {self.cfg.dashboard_port}"
        return code, next(row for row in json.loads(output)["rows"] if row["label"] == label)

    def test_kernel_lets_a_specific_reuseaddr_bind_beside_a_listening_wildcard(self) -> None:
        port = self.holder(socket.AF_INET6, "::", v6only=0)
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass  # the dual-stack holder serves IPv4 loopback
        self.assertEqual(bind_error(socket.AF_INET, "0.0.0.0", port), errno.EADDRINUSE)
        specific = bind_error(socket.AF_INET, "127.0.0.1", port)
        if platform_identity.darwin():
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as fresh:
                self.assertEqual(fresh.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 0)
            self.assertIsNone(specific)
        else:
            self.assertEqual(specific, errno.EADDRINUSE)

    def test_doctor_fails_closed_on_a_wildcard_holder_with_an_unknown_owner(self) -> None:
        for name, family, address, v6only in (
            ("dual-stack ::", socket.AF_INET6, "::", 0),
            ("IPv4 0.0.0.0", socket.AF_INET, "0.0.0.0", None),
        ):
            with self.subTest(holder=name):
                self.cfg.dashboard_port = self.holder(family, address, v6only=v6only)
                code, row = self.port_row(subprocess.CompletedProcess([], 0, HEADER, ""))
                self.assertEqual((code, row["status"]), (1, "FAIL"), row["detail"])
                self.assertIn("is in use by an unknown process", row["detail"])

    def test_doctor_attributes_a_dual_stack_holder(self) -> None:
        self.cfg.dashboard_port = self.holder(socket.AF_INET6, "::", v6only=0)
        ss = subprocess.CompletedProcess(
            [], 0, self.ss_line("[::]", self.cfg.dashboard_port, "foreign", 4321), ""
        )
        code, row = self.port_row(ss)
        self.assertEqual((code, row["status"]), (1, "FAIL"))
        self.assertIn("is in use by foreign", row["detail"])

    def test_doctor_ignores_an_ipv6_loopback_holder(self) -> None:
        self.cfg.dashboard_port = self.holder(socket.AF_INET6, "::1", v6only=1)
        ss = subprocess.CompletedProcess(
            [], 0, self.ss_line("[::1]", self.cfg.dashboard_port, "foreign", 4321), ""
        )
        code, row = self.port_row(ss)
        self.assertEqual((code, row["status"], row["detail"]), (0, "PASS", "available on 127.0.0.1"))

    def test_missing_ss_never_decides_the_port_verdict(self) -> None:
        """Real holders, missing `ss`: only native bind evidence (Darwin) may pass a free specific port."""
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            free = unused.getsockname()[1]
        cases = [("free", "127.0.0.1", free, platform_identity.darwin()),
                 ("free, wildcard host", "0.0.0.0", free, False)]
        for name, family, address, v6only, reuseport in (
            ("specific 127.0.0.1", socket.AF_INET, "127.0.0.1", None, False),
            ("specific 127.0.0.1 SO_REUSEPORT", socket.AF_INET, "127.0.0.1", None, True),
            ("IPv4 0.0.0.0", socket.AF_INET, "0.0.0.0", None, False),
            ("dual-stack ::", socket.AF_INET6, "::", 0, False),
            ("v4-mapped ::ffff:127.0.0.1", socket.AF_INET6, "::ffff:127.0.0.1", 0, False),
        ):
            port = self.holder(family, address, v6only=v6only, reuseport=reuseport)
            cases.append((name, "127.0.0.1", port, False))
        for name, host, port, native_free in cases:
            with self.subTest(port=name), patch.object(
                onboard, "sh", side_effect=FileNotFoundError(errno.ENOENT, "No such file", "ss")
            ) as sh:
                ok, detail = onboard._dashboard_port_check(self.cfg, host, port)
                if native_free:
                    self.assertEqual((ok, detail), (True, "available on 127.0.0.1"))
                    sh.assert_not_called()
                else:
                    self.assertFalse(ok)
                    self.assertIn("could not be checked (ss:", detail)


if __name__ == "__main__":
    unittest.main()
