"""ABI health diagnostics remain testable without Darwin native calls."""
import io
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory import config, darwin_observer, onboard, watch


class DarwinABIHealthTest(unittest.TestCase):
    def test_doctor_and_watch_report_unsupported_darwin_abi(self):
        dynamic_darwin = bytearray(b"darwin").decode()
        self.assertEqual(dynamic_darwin, darwin_observer.SUPPORTED_PLATFORM)
        self.assertIsNot(dynamic_darwin, darwin_observer.SUPPORTED_PLATFORM)
        cases = [
            (("darwin", "arm64", 8, "little", (27, 3)), True),
            (("darwin", "x86_64", 8, "little", (27, 2)), True),
            (("darwin", "arm64", 8, "little", (27, 2)), False),
            (("linux", "x86_64", 8, "little", None), False),
            ((dynamic_darwin, "arm64", 8, "little", (27, 3)), True),
            (("aix", "arm64", 8, "little", None), False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(Path(directory), "acme/widgets", signoff=False)
            for facts, unsupported in cases:
                with self.subTest(facts=facts), ExitStack() as stack:
                    stack.enter_context(patch.object(darwin_observer, "abi_facts", return_value=facts))
                    stack.enter_context(patch.object(config, "load", return_value=cfg))
                    stack.enter_context(patch.object(config, "host_config", return_value={}))
                    stack.enter_context(patch.object(config, "engine_drift", return_value=None))
                    stack.enter_context(patch.object(onboard, "sh", return_value=subprocess.CompletedProcess([], 0, "[]", "")))
                    stack.enter_context(patch.object(onboard.claims, "repository_host_problem", return_value=None))
                    stack.enter_context(patch.object(onboard, "ssh_signing_key_status", return_value=None))
                    stack.enter_context(patch.object(onboard, "unit_dir", return_value=cfg.root / "units"))
                    stack.enter_context(patch.object(onboard, "_dashboard_port_check", return_value=(True, "free")))
                    stack.enter_context(patch("factory.triage.probe_endpoint", return_value=(False, "offline")))
                    stack.enter_context(patch.object(watch, "failed_units", return_value=[]))
                    stack.enter_context(patch.object(watch, "runtime_errors", return_value=[]))
                    output = io.StringIO()
                    with redirect_stdout(output):
                        onboard.doctor([])
                    failures = [line for line in output.getvalue().splitlines()
                                if "FAIL" in line and "unsupported darwin ABI" in line]
                    self.assertEqual(bool(failures), unsupported)
                    signals = [s for s in watch.signals(cfg, []) if s["key"] == "unsupported darwin ABI"]
                    self.assertEqual(bool(signals), unsupported)
                    if unsupported:
                        self.assertIn(repr(facts), failures[0])
                        self.assertIn(repr(facts), watch.body(signals[0], "acme/widgets"))
                        self.assertEqual(signals[0]["command"], "factory doctor")
