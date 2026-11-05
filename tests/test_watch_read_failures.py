from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, dispatch, lifecycle, watch


REPO = "acme/widgets"


class WatchReadFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, REPO, watch_engine_repo=REPO)
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.calls = self.root / "gh-calls.jsonl"
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(
            "#!/usr/bin/python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"calls = Path({str(self.calls)!r})\n"
            "args = sys.argv[1:]\n"
            "with calls.open('a') as out:\n"
            "    out.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['issue', 'view']:\n"
            "    if args[2] == '102':\n"
            "        print('GraphQL: access denied', file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    print(json.dumps({'state': 'OPEN', 'closedAt': None, 'body': ''}))\n"
            "elif args[:2] == ['issue', 'list']:\n"
            "    print('[]')\n"
            "else:\n"
            "    raise SystemExit('unexpected gh command: ' + ' '.join(args))\n"
        )
        gh.chmod(0o755)
        self.enterContext(mock.patch.dict(os.environ, {
            lifecycle.CONTEXT_ENV: "",
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        }))
        self.enterContext(mock.patch.object(watch, "failed_units", return_value=[]))
        self.enterContext(mock.patch.object(watch, "runtime_errors", return_value=[]))

    def journal(self, **fields: object) -> None:
        lifecycle.append(dispatch.EVENTS, {
            "event": "watch", "at": lifecycle._now(), "repo": REPO, **fields,
        })

    def rows(self) -> list[dict]:
        return lifecycle.read_events(dispatch.EVENTS)

    def gh_calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    @staticmethod
    def signal(fingerprint: str) -> dict:
        return {
            "fingerprint": fingerprint,
            "kind": "lifecycle",
            "stage": "worker",
            "key": "RuntimeError: boom",
            "rows": [],
            "excerpt": None,
            "log": None,
            "latest": None,
            "command": "factory watch --dry-run",
        }

    def test_failed_middle_signal_read_is_skipped_and_later_signal_continues(self) -> None:
        for number in (101, 102, 103):
            self.journal(fingerprint=f"fp-{number}", issue=number)
        before = [row.copy() for row in self.rows()]

        with mock.patch.object(watch, "signals", return_value=[
            self.signal("fp-101"), self.signal("fp-102"), self.signal("fp-103"),
        ]):
            watch.run_pass(self.cfg)

        calls = self.gh_calls()
        self.assertEqual(
            [call[2] for call in calls if call[:2] == ["issue", "view"]],
            ["101", "102", "103"],
        )
        self.assertFalse(any(call[:2] in (["issue", "create"], ["issue", "comment"],
                                          ["issue", "edit"], ["issue", "close"])
                             for call in calls))
        self.assertEqual([row for row in self.rows() if row["event"] == "watch"], before)
        skips = [row for row in self.rows() if row.get("event") == "read-skip"]
        self.assertEqual([(row.get("fingerprint"), row["code"]) for row in skips], [
            ("fp-102", "gh_read_failed"),
        ])


if __name__ == "__main__":
    unittest.main()
