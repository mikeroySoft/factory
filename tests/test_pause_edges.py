"""Pause failures stay fail-safe and public CLI errors remain actionable."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import cli, config, dashboard, pause, runtime_local


class PauseEdgesTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.flag = self.root / "factory" / "factory.pause"
        self.flag.parent.mkdir()

    def command(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = cli.main(list(args))
        return result, output.getvalue()

    def test_unstatable_marker_blocks_and_reports_unknown_age(self) -> None:
        self.flag.touch()
        with patch.object(pause.os, "lstat", side_effect=PermissionError("access denied")):
            result, output = self.command("status", "--json")
            value = json.loads(output)
            self.assertEqual(result, 0)
            self.assertTrue(value["paused"])
            self.assertTrue(value["unowned"])
            self.assertIsNone(value["age_s"])
            result, output = self.command("drain-check")
            self.assertEqual(result, 1)
            self.assertIn("age: unknown", output)
            self.assertIn("unowned", output)
            self.assertIn("age: unknown", self.command("status")[1])

    def test_recent_and_future_markers_have_zero_age(self) -> None:
        now = datetime(2026, 11, 25, 12, tzinfo=UTC)
        for owned in (True, False):
            for seconds in (0, 0.5, 5):
                with self.subTest(owned=owned, seconds=seconds):
                    created = now + timedelta(seconds=seconds)
                    self.flag.write_text(json.dumps({
                        "owner": "agent", "reason": "maintenance",
                        "created_at": created.isoformat(),
                    }) if owned else "")
                    os.utime(self.flag, (created.timestamp(), created.timestamp()))
                    value = pause.read_status(now=now)
                    self.assertEqual(value["age_s"], 0)
                    self.assertEqual(value["unowned"], not owned)

    def test_missing_pause_options_name_the_required_argument(self) -> None:
        for args, missing in ((["--reason", "maintenance"], "--owner"),
                              (["--owner", "agent"], "--reason")):
            with self.subTest(missing=missing):
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as raised:
                    cli.main(["pause", *args])
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("required", output.getvalue())
                self.assertIn(missing, output.getvalue())
                self.assertFalse(self.flag.exists())

    def test_failed_write_keeps_previous_pause_and_removes_temporary(self) -> None:
        self.flag.write_text("previous unowned pause")
        with patch.object(pause.os, "replace", side_effect=PermissionError("replace denied")):
            result, output = self.command("pause", "--owner", "agent", "--reason", "maintenance")
        self.assertEqual(result, 1)
        self.assertIn("factory pause: replace denied", output)
        self.assertEqual(self.flag.read_text(), "previous unowned pause")
        self.assertEqual(list(self.flag.parent.iterdir()), [self.flag])
        self.assertTrue(pause.read_status()["unowned"])

    def test_storage_errors_return_failure_instead_of_success(self) -> None:
        for command, operation in (("pause", "mkdir"), ("resume", "unlink")):
            args = [command, "--owner", "agent", "--reason", "maintenance"] if command == "pause" else [command]
            self.flag.touch()
            with self.subTest(command=command), \
                    patch.object(Path, operation, side_effect=PermissionError("storage denied")):
                result, output = self.command(*args)
            self.assertEqual(result, 1)
            self.assertIn(f"factory {command}: storage denied", output)
            self.assertTrue(self.flag.exists())

    def test_dashboard_keeps_ownership_and_age_from_its_runtime_observation(self) -> None:
        cfg = config.Config(self.root, "acme/widgets", signoff=False)
        pause.set_pause("observation-agent", "maintenance")
        with patch.object(runtime_local, "_command", return_value=""), \
                patch.object(runtime_local, "_capacity", return_value=0):
            observed, _errors = runtime_local.dispatcher(cfg, [], {})
        # Resume after observation: a second read must not replace that snapshot's metadata.
        self.flag.unlink()
        with patch.dict(dashboard.__dict__), \
                patch.object(dashboard.runtime_local, "dispatcher", return_value=(observed, [])), \
                patch.object(dashboard, "_timer_last", return_value=None), \
                patch.object(dashboard, "journal_runs", return_value=[]):
            dashboard.configure(cfg)
            reported = dashboard.dispatcher({"executions": [], "history": {}, "events": []})
        self.assertTrue(reported["paused"])
        self.assertEqual(reported["pause"], observed["pause"])

    def test_marker_replaced_after_stat_never_follows_symlink_or_blocks_on_fifo(self) -> None:
        # A subprocess bounds a regressed blocking open without a leaked test thread.
        source = r'''
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from factory import cli, pause
flag = pause.pause_path()
kind = sys.argv[1]
original_lstat = os.lstat
replaced = False
def racing_lstat(path, *args, **kwargs):
    global replaced
    metadata = original_lstat(path, *args, **kwargs)
    if Path(path) == flag and not replaced:
        replaced = True
        flag.unlink()
        if kind == "fifo":
            os.mkfifo(flag)
        else:
            target = flag.with_name("owned-target")
            target.write_text(json.dumps({"owner": "agent", "reason": "maintenance",
                                         "created_at": "2026-11-25T12:00:00+00:00"}))
            flag.symlink_to(target)
    return metadata
with patch.object(pause.os, "lstat", side_effect=racing_lstat):
    raise SystemExit(cli.main(["status", "--json"]))
'''
        for kind in ("symlink", "fifo"):
            with self.subTest(kind=kind):
                self.flag.unlink(missing_ok=True)
                self.flag.touch()
                result = subprocess.run([sys.executable, "-c", source, kind],
                                        text=True, capture_output=True, timeout=3, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                self.assertTrue(value["paused"])
                self.assertTrue(value["unowned"])
                self.assertIsNone(value["owner"])


if __name__ == "__main__":
    unittest.main()
