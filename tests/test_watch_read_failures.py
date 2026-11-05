from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
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


class WatchRetryAndClosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cfg = config.Config(self.root, REPO, watch_engine_repo=REPO)
        self.cfg.factory.mkdir()
        dispatch.configure(self.cfg)
        self.calls = self.root / "policy-gh-calls.jsonl"
        self.state = self.root / "policy-gh-state.json"
        binary = self.root / "policy-bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(
            "#!/usr/bin/python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"calls = Path({str(self.calls)!r})\n"
            f"state_path = Path({str(self.state)!r})\n"
            "args = sys.argv[1:]\n"
            "with calls.open('a') as out:\n"
            "    out.write(json.dumps(args) + '\\n')\n"
            "state = json.loads(state_path.read_text())\n"
            "if args[:2] == ['issue', 'view']:\n"
            "    failures = state.get('failures', {}).get(args[2], [])\n"
            "    if failures:\n"
            "        failure = failures.pop(0)\n"
            "        state_path.write_text(json.dumps(state))\n"
            "        print(failure, file=sys.stderr)\n"
            "        raise SystemExit(1)\n"
            "    print(json.dumps(state['issues'][args[2]]))\n"
            "elif args[:2] == ['issue', 'comment']:\n"
            "    pass\n"
            "elif args[:2] == ['issue', 'edit']:\n"
            "    state['issues'][args[2]]['body'] = args[args.index('--body') + 1]\n"
            "    state_path.write_text(json.dumps(state))\n"
            "elif args[:2] == ['issue', 'close']:\n"
            "    state['issues'][args[2]]['state'] = 'CLOSED'\n"
            "    state_path.write_text(json.dumps(state))\n"
            "elif args[:2] == ['issue', 'list']:\n"
            "    print('[]')\n"
            "elif args[:2] == ['api', 'rate_limit']:\n"
            "    print(json.dumps({'resources': {'graphql': {'remaining': 5000}, "
            "'core': {'remaining': 5000}}}))\n"
            "else:\n"
            "    raise SystemExit('unexpected gh command: ' + ' '.join(args))\n"
        )
        gh.chmod(0o755)
        self.state.write_text(json.dumps({"issues": {}, "failures": {}}))
        self.enterContext(mock.patch.dict(os.environ, {
            lifecycle.CONTEXT_ENV: "",
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        }))
        self.enterContext(mock.patch.object(watch, "failed_units", return_value=[]))
        self.enterContext(mock.patch.object(watch, "runtime_errors", return_value=[]))

    def save_state(self, issues: dict, failures: dict | None = None) -> None:
        self.state.write_text(json.dumps({"issues": issues, "failures": failures or {}}))

    def journal(self, fingerprint: str, issue: int, age: timedelta) -> dict:
        row = {
            "event": "watch",
            "fingerprint": fingerprint,
            "issue": issue,
            "repo": REPO,
            "at": (datetime.now(UTC) - age).isoformat().replace("+00:00", "Z"),
        }
        lifecycle.append(dispatch.EVENTS, row)
        return row

    def rows(self, event: str) -> list[dict]:
        return [row for row in lifecycle.read_events(dispatch.EVENTS) if row.get("event") == event]

    def gh_calls(self, *prefix: str) -> list[list[str]]:
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return [call for call in calls if call[:len(prefix)] == list(prefix)]

    def test_recovered_network_read_is_processed_after_retry(self) -> None:
        from factory import reads

        fingerprint = "retry-after-reset"
        self.journal(fingerprint, 201, timedelta(days=2))
        self.save_state({
            "201": {
                "state": "OPEN",
                "closedAt": None,
                "body": f"Fingerprint: {fingerprint}\n\n## Observed by\n- `{REPO}`\n",
            },
        }, {"201": ["error connecting to api.github.com"]})
        signal = WatchReadFailureTest.signal(fingerprint)

        with mock.patch.object(watch, "signals", return_value=[signal]), \
                mock.patch.object(reads.time, "sleep") as sleep:
            self.assertEqual(watch.run_pass(self.cfg), [signal])

        self.assertEqual([call[2] for call in self.gh_calls("issue", "view")], ["201", "201"])
        self.assertEqual([call[2] for call in self.gh_calls("issue", "comment")], ["201"])
        sleep.assert_called_once()
        self.assertEqual(self.rows("read-skip"), [])
        [summary] = self.rows("read-summary")
        self.assertEqual((summary["failed"], summary["skipped"], summary["retries"]), (0, 0, 1))

    def test_closure_read_failure_is_journaled_without_closing_or_stage_success(self) -> None:
        filing = self.journal("closure-read", 301, timedelta(days=2))
        self.save_state({
            "301": {
                "state": "OPEN",
                "assignees": [],
                "labels": [{"name": config.LABEL_AGENT}],
                "comments": [],
            },
        }, {"301": ["GraphQL: access denied"]})

        with mock.patch.object(watch.config, "load", return_value=self.cfg), \
                mock.patch.object(watch, "signals", return_value=[]):
            self.assertEqual(watch.main([]), 0)

        self.assertEqual(self.gh_calls("issue", "close"), [])
        self.assertEqual(self.rows("watch"), [filing])
        [skip] = self.rows("read-skip")
        self.assertEqual(
            (skip.get("fingerprint"), skip.get("issue"), skip["code"]),
            ("closure-read", 301, "gh_read_failed"),
        )
        self.assertIsNone(lifecycle.last_successful_passes(dispatch.EVENTS)["watch"])

    def test_unrelated_skip_during_successful_read_does_not_skip_signal(self) -> None:
        from factory import reads

        fingerprint = "successful-read"
        self.journal(fingerprint, 401, timedelta(days=2))
        self.save_state({
            "401": {
                "state": "OPEN",
                "closedAt": None,
                "body": f"Fingerprint: {fingerprint}\n\n## Observed by\n- `{REPO}`\n",
            },
        })
        signal = WatchReadFailureTest.signal(fingerprint)
        real_gh_json = dispatch.gh_json

        def gh_json(args, env=None):
            if args[:3] == ["issue", "view", "401"]:
                reads.skip(reads.ReadFailure(
                    "gh_read_failed", mock.Mock(returncode=1, stderr="unrelated read failure"),
                ), fingerprint="unrelated")
            return real_gh_json(args, env)

        with mock.patch.object(watch, "signals", return_value=[signal]), \
                mock.patch.object(dispatch, "gh_json", side_effect=gh_json):
            self.assertEqual(watch.run_pass(self.cfg), [signal])

        self.assertEqual([call[2] for call in self.gh_calls("issue", "comment")], ["401"])
        [skip] = self.rows("read-skip")
        self.assertEqual(skip.get("fingerprint"), "unrelated")

    def test_escape_attribution_read_failure_is_skipped_before_signals(self) -> None:
        from factory import reads, stats

        self.cfg.watch_bands = [mock.Mock(metric="revert_rate_by_tier")]
        fingerprint = "after-attribution-failure"
        self.journal(fingerprint, 501, timedelta(days=2))
        self.save_state({
            "501": {
                "state": "OPEN",
                "closedAt": None,
                "body": f"Fingerprint: {fingerprint}\n\n## Observed by\n- `{REPO}`\n",
            },
        })
        signal = WatchReadFailureTest.signal(fingerprint)
        failure = reads.ReadFailure(
            "gh_read_failed", mock.Mock(returncode=1, stderr="escape attribution unavailable"),
        )

        with mock.patch.object(stats, "escapes", side_effect=failure), \
                mock.patch.object(watch, "signals", return_value=[signal]):
            self.assertEqual(watch.run_pass(self.cfg), [signal])

        self.assertEqual([call[2] for call in self.gh_calls("issue", "comment")], ["501"])
        [skip] = self.rows("read-skip")
        self.assertEqual(skip.get("operation"), "escape-attribution")
