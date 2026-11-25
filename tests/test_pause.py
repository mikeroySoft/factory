"""Host pause protocol through the public CLI and scheduled-pass admission."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from factory import cli


class PauseTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.flag = self.root / "state" / "factory" / "factory.pause"
        self.flag.parent.mkdir(parents=True)
        environment = patch.dict(os.environ, {
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def command(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = cli.main(list(args))
        return code, output.getvalue()

    def status(self) -> dict:
        code, output = self.command("status", "--json")
        self.assertEqual(code, 0, output)
        return json.loads(output)

    def test_empty_pause_still_blocks_scheduled_pass(self) -> None:
        self.flag.touch()
        code, output = self.command("drain-check")
        self.assertEqual(code, 1, output)
        self.assertIn("unowned", output)

    def test_empty_pause_status_is_unowned(self) -> None:
        self.flag.touch()
        value = self.status()
        self.assertTrue(value["paused"])
        self.assertTrue(value["unowned"])
        self.assertIsNone(value["owner"])
        self.assertIsNone(value["reason"])

    def test_missing_fields_and_invalid_metadata_are_unowned(self) -> None:
        fields = {"owner": "maintenance-agent", "reason": "engine update", "created_at": "2026-11-25T12:00:00+02:00"}
        malformed = ["", "{", "[]", "null", "\xff"]
        malformed += [json.dumps({key: value for key, value in fields.items() if key != missing}) for missing in fields]
        malformed += [json.dumps({**fields, key: value}) for key, value in (
            ("owner", " "), ("reason", 4), ("created_at", "2026-11-25T12:00:00"),
            ("created_at", "2026-11-25"), ("created_at", "not a timestamp"),
        )]
        for content in malformed:
            with self.subTest(content=content):
                self.flag.write_bytes(content.encode("latin-1"))
                self.assertTrue(self.status()["unowned"])
                self.assertEqual(self.command("drain-check")[0], 1)

    def test_pause_writes_metadata_and_status_round_trips_exactly(self) -> None:
        owner, reason = "maintenance-agent", 'Engine update: "new build"\nsecond line'
        before = datetime.now(UTC)
        code, output = self.command("pause", "--owner", owner, "--reason", reason)
        self.assertEqual(code, 0, output)
        stored = json.loads(self.flag.read_text())
        self.assertEqual(stored["owner"], owner)
        self.assertEqual(stored["reason"], reason)
        created = datetime.fromisoformat(stored["created_at"])
        self.assertIsNotNone(created.utcoffset())
        self.assertLessEqual(before, created)
        self.assertLessEqual(created, datetime.now(UTC))
        value = self.status()
        self.assertTrue(value["paused"])
        self.assertFalse(value["unowned"])
        for field in ("owner", "reason", "created_at"):
            self.assertEqual(value[field], stored[field])
        self.assertEqual(self.command("drain-check")[0], 1)
        self.assertIn(owner, self.command("status")[1])
        self.assertIn("age", self.command("status")[1])

    def test_age_uses_creation_time_or_unowned_mtime(self) -> None:
        from factory import pause

        now = datetime(2026, 11, 25, 12, tzinfo=UTC)
        created = now - timedelta(seconds=2400)
        fields = {"owner": "agent", "reason": "maintenance", "created_at": created.astimezone().isoformat()}
        for missing in (None, "owner", "reason", "created_at"):
            with self.subTest(missing=missing):
                self.flag.write_text(json.dumps({key: value for key, value in fields.items() if key != missing}))
                mtime = (now - timedelta(seconds=120)).timestamp()
                os.utime(self.flag, (mtime, mtime))
                value = pause.read_status(now=now)
                self.assertEqual(value["age_s"], 2400 if missing is None else 120)
                self.assertEqual(value["unowned"], missing is not None)

    def test_resume_only_removes_pause_and_is_idempotent(self) -> None:
        self.command("pause", "--owner", "agent", "--reason", "maintenance")
        sibling = self.flag.with_name("other-state")
        sibling.write_text("keep")
        for _ in range(2):
            self.assertEqual(self.command("resume")[0], 0)
            self.assertFalse(self.flag.exists())
            self.assertFalse(self.status()["paused"])
            self.assertFalse(self.status()["unowned"])
            self.assertEqual(self.command("drain-check")[0], 0)
        self.assertEqual(sibling.read_text(), "keep")

    def test_pause_requires_nonblank_owner_and_reason(self) -> None:
        for args in ([], ["--owner", "agent"], ["--reason", "maintenance"],
                     ["--owner", " ", "--reason", "maintenance"],
                     ["--owner", "agent", "--reason", " "]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                self.command("pause", *args)
            self.assertEqual(raised.exception.code, 2)
            self.assertFalse(self.flag.exists())

    def test_default_state_path_uses_home_when_xdg_unset_or_empty(self) -> None:
        for empty in (False, True):
            with self.subTest(empty=empty), patch.dict(os.environ):
                os.environ.pop("XDG_STATE_HOME", None)
                if empty:
                    os.environ["XDG_STATE_HOME"] = ""
                self.assertEqual(self.command("pause", "--owner", "agent", "--reason", "maintenance")[0], 0)
                flag = Path(os.environ["HOME"]) / ".local" / "state" / "factory" / "factory.pause"
                self.assertTrue(flag.exists())
                self.assertTrue(self.status()["paused"])
                self.assertEqual(self.command("resume")[0], 0)


if __name__ == "__main__":
    unittest.main()
