"""Darwin port semantics exercised through public doctor/install consumers."""
from __future__ import annotations

import errno
import json
import socket
import subprocess
import unittest
from unittest.mock import patch

from tests import test_dashboard_port as port_tests


class DashboardPortDarwinTest(unittest.TestCase):
    def fixture(self):
        case = port_tests.DashboardPortTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_foreign_listener_consumers_without_loopback_alias(self):
        class NoLoopbackAlias(socket.socket):
            def bind(self, address):
                if address[0] == "127.0.0.2":
                    raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
                return super().bind(address)

        for method in (
            "test_doctor_ignores_a_listener_on_an_unrelated_address",
            "test_install_refuses_foreign_holder_before_writing_or_mutating_systemd",
        ):
            with self.subTest(consumer=method), patch.object(socket, "socket", NoLoopbackAlias):
                getattr(self.fixture(), method)()

    def test_doctor_refuses_real_dual_stack_wildcard_holder(self):
        case = self.fixture()
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as holder:
            holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            holder.bind(("::", 0))
            holder.listen()
            case.cfg.dashboard_port = holder.getsockname()[1]

            ss = subprocess.CompletedProcess(
                [], 0, case.ss_line("[::]", case.cfg.dashboard_port, "foreign", 4321), ""
            )
            code, output, _ = case.doctor(ss)
            row = next(row for row in json.loads(output)["rows"]
                       if row["label"] == f"dashboard port {case.cfg.dashboard_port}")
            self.assertEqual((code, row["status"]), (1, "FAIL"))
            self.assertIn("foreign", row["detail"])


if __name__ == "__main__":
    unittest.main()
