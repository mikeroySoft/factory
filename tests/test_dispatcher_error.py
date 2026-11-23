"""#895: an escalated ticket error must not fail an otherwise healthy dispatch pass."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest

from factory import lifecycle
from tests import test_dispatch_lifecycle as fixture


class DispatcherErrorTest(unittest.TestCase):
    setUp = fixture.LocalCLI.setUp
    git = fixture.LocalCLI.git
    write_config = fixture.LocalCLI.write_config
    commit_config = fixture.LocalCLI.commit_config

    def test_escalated_worktree_error_does_not_fail_two_ticket_pass(self):
        self.state.write_text(json.dumps({"reviews": 1}))  # healthy ticket is approved first time
        cfg = self.repo / ".factory.toml"
        cfg.write_text(cfg.read_text().replace("[dispatch]\n", "[dispatch]\nmax_active = 2\n"))
        self.commit_config()
        kept = self.repo / ".factory" / "wt-6-kept"
        self.git("worktree", "add", str(kept), "-b", "agent/6")
        original_head = self.git("-C", str(kept), "rev-parse", "HEAD").stdout

        # Keep the normal local worker/reviewer/PR fixture for #7; persist #6's actual
        # GitHub-facing transitions so escalation labels and comment remain observable.
        gh = self.bin / "gh"
        gh.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ["SMOKE_STATE"])
s = json.loads(p.read_text())
a = sys.argv[1:]
bad = {"number": 6, "title": "retained branch", "body": "Acceptance: reuse branch",
       "comments": [], "labels": [{"name": "ready-for-agent"}], "assignees": [],
       "state": "OPEN", "createdAt": "2026-01-01T00:00:00Z"}
if a[:2] == ["issue", "list"] and "ready-for-agent" in a:
    healthy = {**bad, "number": 7, "title": "local change", "createdAt": "2026-01-02T00:00:00Z"}
    print(json.dumps([bad, healthy]))
    sys.exit(0)
if a[:3] == ["issue", "view", "6"]:
    print(json.dumps(bad))
    sys.exit(0)
if a[:3] == ["issue", "edit", "6"]:
    s.setdefault("edits", []).append(a)
    p.write_text(json.dumps(s))
    sys.exit(0)
if a[:3] == ["issue", "comment", "6"]:
    s.setdefault("comments", []).append(a[a.index("--body") + 1])
    p.write_text(json.dumps(s))
    print("https://example.invalid/issues/6#issuecomment-60")
    sys.exit(0)
''' + fixture.GH)

        # The real CLI must return 0, not merely finish #7 before re-raising #6.
        result = subprocess.run(
            [sys.executable, "-m", "factory", "dispatch"], cwd=self.repo, env=self.env,
            capture_output=True, text=True, timeout=60, check=False,
        )
        rows = lifecycle.read_events(self.events)
        escalated = [r for r in rows if r.get("event") == "escalate"]
        self.assertEqual([(r["ticket"], r["code"]) for r in escalated], [(6, "dispatcher_error")])
        reason = escalated[0]["reason"]
        wt = self.repo / ".factory" / "wt-6"
        self.assertTrue(reason.startswith(
            f"dispatcher error: CalledProcessError(128, "
            f"['git', 'worktree', 'add', '{wt}', 'agent/6'],"),
            reason)
        self.assertIn("'agent/6' is already used by worktree", reason)
        self.assertTrue(reason.endswith(f"; worktree kept at {wt}"), reason)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["edits"][-1], [
            "issue", "edit", "6", "--repo", "local/smoke", "--remove-assignee", "@me",
            "--remove-label", "ready-for-agent", "--add-label", "ready-for-human",
        ])
        packet = self.repo / ".factory" / "escalations" / "6.md"
        self.assertEqual(state["comments"], [
            f"Factory dispatcher escalating: {reason}.\n\nEscalation packet: `{packet}`",
        ])
        self.assertTrue(any(r.get("event") == "approved" and r.get("ticket") == 7 for r in rows),
                        result.stdout + result.stderr)
        self.assertEqual(self.git("-C", str(kept), "rev-parse", "HEAD").stdout, original_head)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
