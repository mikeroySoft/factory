"""Survivor kills for factory.onboard._wildcard_held: only EADDRINUSE means "held"; any other
bind error means the probe cannot tell and propagates. The socket is mocked at the OS boundary."""
from __future__ import annotations

import errno
import unittest
from unittest import mock

from factory import onboard


def failing_socket(code: int) -> mock.MagicMock:
    probe = mock.MagicMock()
    probe.__enter__.return_value = probe
    probe.bind.side_effect = OSError(code, "bind failed")
    return probe


class WildcardHeldTest(unittest.TestCase):
    def test_permission_error_propagates(self) -> None:
        self.assertLess(errno.EACCES, errno.EADDRINUSE)
        with (
            mock.patch.object(onboard.socket, "socket", return_value=failing_socket(errno.EACCES)),
            self.assertRaises(OSError) as raised,
        ):
            onboard._wildcard_held(8080)
        self.assertEqual(raised.exception.errno, errno.EACCES)

    def test_address_in_use_means_held(self) -> None:
        with mock.patch.object(onboard.socket, "socket",
                               return_value=failing_socket(errno.EADDRINUSE)):
            self.assertTrue(onboard._wildcard_held(8080))


if __name__ == "__main__":
    unittest.main()
