"""#553: failed remote reads defer work without changing ticket state."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, dispatch, lifecycle


class FetchOutageTest(unittest.TestCase):
    def test_fetch_outage_ends_dispatch_cleanly_before_claiming(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = []

            def command(cmd, *args, **kwargs):
                commands.append(cmd)
                if cmd[:2] == ["git", "fetch"]:
                    raise subprocess.CalledProcessError(
                        128, cmd, stderr="ssh: Could not resolve hostname github.com: No address associated with hostname\n"
                        "fatal: Could not read from remote repository.\n",
                    )
                raise AssertionError(f"unexpected command after failed fetch: {cmd}")

            with patch.object(config, "repo_root", return_value=root), \
                 patch.object(dispatch, "_run", side_effect=command), \
                 patch("time.sleep"):
                self.assertEqual(dispatch.main([]), 0)
            self.assertTrue(all(cmd[:2] == ["git", "fetch"] for cmd in commands))
            rows = lifecycle.read_events(root / ".factory/events.jsonl")
            skips = [row for row in rows if row.get("event") == "read-skip"]
            self.assertEqual([row["code"] for row in skips], ["gh_read_failed"])
            self.assertEqual(skips[0]["returncode"], 128)
            summary = next(row for row in rows if row.get("event") == "read-summary")
            self.assertEqual(summary["skipped"], 1)
            self.assertFalse(any(row.get("event") == "claimed" for row in rows))


if __name__ == "__main__":
    unittest.main()
