from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from factory import gate


class ToolchainTest(unittest.TestCase):
    def test_rocm_cli_and_in_tree_amdgpu_are_reported(self) -> None:
        def probe(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["rocm", "examine"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({
                        "detected_gfx_target": "gfx1201",
                        "driver_status": "amdgpu_available",
                        "active_runtime": "therock-7.0",
                    }),
                    "",
                )
            if command == ["uname", "-r"]:
                return subprocess.CompletedProcess(command, 0, "6.17.1\n", "")
            if command == ["modinfo", "-F", "version", "amdgpu"]:
                raise FileNotFoundError(command[0])
            raise FileNotFoundError(command[0])

        with (
            mock.patch.object(gate.subprocess, "run", side_effect=probe),
            mock.patch.object(Path, "is_dir", return_value=True),
        ):
            found = gate.toolchain()

        self.assertEqual(found["rocm"], "gfx1201 (rocm-cli)")
        self.assertEqual(found["amdgpu"], "loaded (in-tree)")

    def test_failed_probes_fall_back_to_kfd_and_loaded_module(self) -> None:
        def probe(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command == ["uname", "-r"]:
                return subprocess.CompletedProcess(command, 0, "6.17.1\n", "")
            if command[:2] == ["rocm", "examine"]:
                return subprocess.CompletedProcess(
                    command, 1, json.dumps({"detected_gfx_target": "gfx9999"}), "",
                )
            if command == ["hipconfig", "--version"]:
                return subprocess.CompletedProcess(command, 1, "diagnostic output\n", "")
            if command == ["modinfo", "-F", "version", "amdgpu"]:
                return subprocess.CompletedProcess(command, 1, "not-a-version\n", "")
            raise FileNotFoundError(command[0])

        with (
            mock.patch.object(gate.subprocess, "run", side_effect=probe),
            mock.patch.object(gate, "_kfd_targets", return_value=["gfx1010"]),
            mock.patch.object(Path, "is_dir", return_value=True),
        ):
            found = gate.toolchain()

        self.assertEqual(found["rocm"], "gfx1010 (kfd sysfs)")
        self.assertEqual(found["amdgpu"], "loaded (in-tree)")

    def test_absent_rocm_and_amdgpu_stay_unavailable(self) -> None:
        def probe(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command == ["uname", "-r"]:
                return subprocess.CompletedProcess(command, 0, "6.17.1\n", "")
            raise FileNotFoundError(command[0])

        with (
            mock.patch.object(gate.subprocess, "run", side_effect=probe),
            mock.patch.object(Path, "is_dir", return_value=False),
            mock.patch.object(Path, "glob", return_value=[]),
        ):
            found = gate.toolchain()

        self.assertEqual(found["rocm"], "unavailable")
        self.assertEqual(found["amdgpu"], "unavailable")


if __name__ == "__main__":
    unittest.main()
