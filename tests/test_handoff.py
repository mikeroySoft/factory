"""#56: durable routed human handoffs through the real `factory manage` CLI against a stateful `gh` stand-in.

The stub remembers comments (with ids and URLs, as GitHub does), labels and the issue body, and
can crash the runner after a comment is created, refuse to create one, or refuse the timeline read.
No live notification, model or GitHub call is involved.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory import config, dispatch, handoff, lifecycle  # noqa: E402

REPO = "acme/widgets"
ISSUE_URL = f"https://github.com/{REPO}/issues/7"
PR_URL = f"https://github.com/{REPO}/pull/9"
AT = "2026-01-01T00:00:00Z"

GH = '''#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
state_path = Path(os.environ["HANDOFF_STATE"])
s = json.loads(state_path.read_text())
a = sys.argv[1:]
with open(os.environ["HANDOFF_CALLS"], "a") as log:
    log.write(json.dumps(a) + "\\n")
issue = s["issue"]

def save():
    state_path.write_text(json.dumps(s))

def flag(name):
    return a[a.index(name) + 1] if name in a else None

if a[:2] == ["issue", "list"]:
    print(json.dumps([issue] if flag("--label") in [l["name"] for l in issue["labels"]] else []))
elif a[:2] == ["issue", "view"]:
    print(json.dumps(issue))
elif a[:2] == ["pr", "list"]:
    print("[]")
elif a[:2] == ["pr", "view"]:
    if not s.get("pr"):
        print("no pull requests found for branch", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(s["pr"]))
elif a[0] == "api" and a[1].endswith("/timeline"):
    if s.get("fail_timeline"):
        print("HTTP 502", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps([s["timeline"]]))
elif a[0] == "api" and "--method" in a:
    endpoint = a[-1]
    if s.get("fail_read"):
        print("HTTP 502", file=sys.stderr)
        raise SystemExit(1)
    if endpoint == "repos/acme/widgets/issues/7":
        print(json.dumps({"number": 7, "title": issue["title"], "body": issue["body"], "state": "open",
                          "html_url": issue["url"], "updated_at": "2026-01-01T00:00:00Z", "labels": issue["labels"]}))
    elif endpoint == "repos/acme/widgets":
        print(json.dumps({"owner": {"type": s.get("owner_type", "User")}}))
    else:
        print("HTTP 404", file=sys.stderr)
        raise SystemExit(1)
elif a[:2] == ["issue", "comment"]:
    body = flag("--body")
    if s.get("fail_comment") and "factory-handoff" in body:
        print("HTTP 502", file=sys.stderr)
        raise SystemExit(1)
    s["next_id"] = s.get("next_id", 100) + 1
    cid = s["next_id"]
    at = "2026-01-01T00:00:%02dZ" % (cid - 100)
    url = f"{issue['url']}#issuecomment-{cid}"
    s["timeline"].append({"event": "commented", "id": cid, "body": body, "created_at": at, "updated_at": at,
                          "html_url": url, "user": {"login": "runner"}})
    save()
    if s.get("crash_on_comment") and "factory-handoff" in body:
        os.kill(os.getppid(), signal.SIGKILL)
        time.sleep(2)
    print(url)
elif a[:2] == ["issue", "edit"]:
    names = [l["name"] for l in issue["labels"]]
    for i, token in enumerate(a):
        if token == "--add-label" and a[i + 1] not in names:
            names.append(a[i + 1])
        if token == "--remove-label" and a[i + 1] in names:
            names.remove(a[i + 1])
    issue["labels"] = [{"name": n} for n in names]
    save()
else:
    raise SystemExit("unexpected external operation: " + repr(a))
'''

MANAGER = '''import os, sys
from pathlib import Path
Path(os.environ["MANAGER_RAN"]).write_text("ran")
mode = os.environ.get("MANAGER_MODE", "HUMAN")
if mode == "fail":
    sys.stderr.write("transport exploded\\n")
    raise SystemExit(1)
print("DECISION: " + mode + "\\nDiagnosis for the human: the change needs a product call.")
'''

COLLAB = '''
[collaboration]
fallback = "repo-owner"
[collaboration.reasons]
ci = "ci-owner"
[collaboration.components]
"src/auth" = "auth-owner"
"docs" = "docs-owner"
'''


class HandoffCli(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        binder = base / "bin"
        binder.mkdir()
        (binder / "gh").write_text(GH)
        (binder / "gh").chmod(0o755)
        self.manager = base / "manager.py"
        self.manager.write_text(MANAGER)
        self.state_path, self.calls_path, self.ran = base / "state.json", base / "calls.jsonl", base / "manager-ran"
        self.env = {**os.environ, "PATH": str(binder) + os.pathsep + os.environ["PATH"], "PYTHONPATH": str(ROOT),
                    "XDG_CONFIG_HOME": str(base / "xdg"), "HOME": str(base / "home"), "GH_TOKEN": "", "GITHUB_TOKEN": "",
                    "HANDOFF_STATE": str(self.state_path), "HANDOFF_CALLS": str(self.calls_path),
                    "MANAGER_RAN": str(self.ran), lifecycle.CONTEXT_ENV: ""}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.repo, check=True)
        self.configure()
        self.factory = self.repo / ".factory"
        (self.factory / "escalations").mkdir(parents=True)
        self.packet = self.factory / "escalations" / "7.md"
        self.packet.write_text("# Escalation #7\n\nworker log at /tmp/runner-only/logs/7-attempt-1.log\n")
        self.state = {
            "issue": {"id": "I_7", "number": 7, "title": "Fix gate", "url": ISSUE_URL, "state": "OPEN",
                      "body": "**Scope**\nA ticket.", "labels": [{"name": "ready-for-human"}]},
            "pr": {"url": PR_URL, "files": [{"path": "src/auth/login.py"}]},
            "timeline": [],
            "next_id": 100,
        }
        # As `dispatch.escalate` leaves things: the journaled row, the packet, its recorded comment.
        self.escalate("gate failed after 3 attempts", 1)

    def configure(self, manager: bool = True, rounds: int = 1, collaboration: str = COLLAB, manager_cmd=None) -> None:
        text = f'[repo]\nslug = "{REPO}"\n'
        if manager:
            command = manager_cmd or [sys.executable, str(self.manager), "{prompt}", "{cwd}"]
            text += f"[manager]\ncommand = {json.dumps(command)}\nrounds = {rounds}\n"
        (self.repo / config.CONFIG_NAME).write_text(text + collaboration)

    def reset(self) -> None:
        (self.factory / "events.jsonl").unlink(missing_ok=True)
        self.calls_path.unlink(missing_ok=True)
        self.state["timeline"] = []
        self.state["issue"]["labels"] = [{"name": "ready-for-human"}]

    def escalate(self, reason: str, round_number: int, at: str = AT) -> None:
        self.state["next_id"] += 1
        cid = self.state["next_id"]
        url = f"{ISSUE_URL}#issuecomment-{cid}"
        self.state["timeline"] += [
            {"event": "unlabeled", "label": {"name": "ready-for-agent"}, "created_at": at, "actor": {"login": "runner"}},
            {"event": "labeled", "label": {"name": "ready-for-human"}, "created_at": at, "actor": {"login": "runner"}},
            {"event": "commented", "id": cid, "created_at": at, "updated_at": at, "html_url": url, "user": {"login": "runner"},
             "body": f"Factory dispatcher escalating: {reason}.\n\nEscalation packet: `{self.packet}`"},
        ]
        with (self.factory / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": at, "event": "escalate", "ticket": 7, "reason": reason, "round": round_number,
                                     "packet": str(self.packet), "log": "/tmp/runner-only/logs/7-attempt-3.log"}) + "\n")
            stream.write(json.dumps({"at": at, "event": "comment", "ticket": 7, "kind": "escalation", "round": round_number,
                                     "comment": cid, "url": url}) + "\n")

    def manage(self, *argv: str, mode: str = "HUMAN", expect: int = 0, **flags) -> subprocess.CompletedProcess:
        self.state.update(flags)
        self.state_path.write_text(json.dumps(self.state))
        self.ran.unlink(missing_ok=True)
        proc = subprocess.run([sys.executable, "-m", "factory", "manage", *argv], cwd=self.repo, capture_output=True,
                              text=True, env={**self.env, "MANAGER_MODE": mode}, timeout=120)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        self.state = json.loads(self.state_path.read_text())
        for key in flags:
            self.state.pop(key, None)
        return proc

    def events(self, kind: str) -> list[dict]:
        return [e for e in lifecycle.read_events(self.factory / "events.jsonl") if e.get("event") == kind]

    def calls(self, *prefix: str) -> list[list[str]]:
        rows = [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []
        return [row for row in rows if row[:len(prefix)] == list(prefix)]

    def requests(self) -> list[dict]:
        return [item for item in self.state["timeline"] if item.get("event") == "commented" and "factory-handoff" in item["body"]]

    def test_manager_human_publishes_one_public_routed_request(self) -> None:
        self.manage(mode="HUMAN")
        self.assertTrue(self.ran.exists())
        requests = self.requests()
        self.assertEqual(len(requests), 1)
        body = requests[0]["body"]
        self.assertIn("Factory handoff request `7/1`", body)
        self.assertIn("@auth-owner — you own this decision (component)", body)
        self.assertIn("gate failed after 3 attempts", body)
        self.assertIn("the manager asked for a human decision", body)
        manager_comment = next(i for i in self.state["timeline"] if i.get("body", "").startswith("Factory manager:"))
        for link in (PR_URL, f"{ISSUE_URL}#issuecomment-101", manager_comment["html_url"]):
            self.assertIn(link, body)
        # Public-safe: no runner paths, packet text or log paths reach GitHub.
        for local in (str(self.repo), "/tmp/runner-only", "7-attempt", "worker log at"):
            self.assertNotIn(local, body)
        self.assertNotIn("Diagnosis for the human", body)  # the manager's prose is linked, not restated
        rows = lifecycle.read_events(self.factory / "events.jsonl")
        intent, receipt = self.events("handoff"), self.events("comment")[-1]
        self.assertEqual([(i["request"], i["target"], i["round"]) for i in intent], [("7/1", "@auth-owner", 1)])
        self.assertEqual((receipt["kind"], receipt["request"], receipt["comment"], receipt["target"]),
                         ("handoff", "7/1", requests[0]["id"], "@auth-owner"))
        self.assertLess(rows.index(intent[0]), rows.index(receipt))  # intent journaled before the comment exists
        self.assertEqual(self.calls("issue", "edit"), [])  # nothing assigned or relabelled
        for _ in range(2):
            self.manage()
            self.assertFalse(self.ran.exists())  # the decided round is never replayed
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual(len(self.events("handoff")), 1)
        self.assertEqual([i["name"] for i in self.state["issue"]["labels"]], ["ready-for-human"])

    def test_crash_after_comment_creation_reconciles_to_the_same_request(self) -> None:
        self.manage(mode="HUMAN", crash_on_comment=True, expect=-9)
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual([e["request"] for e in self.events("handoff")], ["7/1"])
        self.assertFalse(any(e["kind"] == "handoff" for e in self.events("comment")))
        self.manage()
        receipts = [e for e in self.events("comment") if e["kind"] == "handoff"]
        self.assertEqual([(r["request"], r["comment"], r.get("reconciled")) for r in receipts],
                         [("7/1", self.requests()[0]["id"], True)])
        self.assertEqual(len(self.requests()), 1)
        self.manage()
        self.assertEqual((len(self.requests()), len(self.events("handoff"))), (1, 1))

    def test_failed_lookup_never_posts_another_request(self) -> None:
        proc = self.manage(mode="HUMAN", fail_comment=True)
        self.assertIn("publication uncertain", proc.stdout)
        self.assertEqual((len(self.requests()), len(self.events("handoff"))), (0, 1))
        proc = self.manage(fail_timeline=True)
        self.assertIn("GitHub could not be read; nothing posted", proc.stdout)
        self.assertEqual(len(self.requests()), 0)
        self.assertEqual(len(self.calls("issue", "comment")), 2)  # manager comment + the refused attempt
        self.manage()  # reconciled: the comment is provably absent, so the same request is posted once
        requests = self.requests()
        self.assertEqual(len(requests), 1)
        self.assertIn(self.events("handoff")[0]["token"], requests[0]["body"])
        receipt = [e for e in self.events("comment") if e["kind"] == "handoff"]
        self.assertEqual([(r["request"], r["comment"]) for r in receipt], [("7/1", requests[0]["id"])])
        self.manage()
        self.assertEqual(len(self.requests()), 1)

    def test_restart_keeps_the_human_decision_owner_override(self) -> None:
        self.manage(mode="HUMAN")
        self.assertIn("@auth-owner", self.requests()[0]["body"])
        # A human claims the decision in the issue body; nothing but GitHub holds that fact.
        self.state["issue"]["body"] += "\n\n**Decision owner**\n\n@lead"
        self.state["timeline"].append({"event": "edited", "created_at": "2026-01-01T00:01:00Z", "actor": {"login": "lead"}})
        self.manage()  # a fresh process: the override is read, not overwritten
        requests = self.requests()
        self.assertEqual(len(requests), 2)
        self.assertIn("the decision owner is now @lead (decision_owner", requests[1]["body"])
        self.assertIn("previously @auth-owner", requests[1]["body"])
        self.assertEqual([r["target"] for r in self.events("comment") if r["kind"] == "handoff"], ["@auth-owner", "@lead"])
        self.assertIn("@lead", self.state["issue"]["body"])
        self.assertEqual(self.calls("issue", "edit"), [])
        self.manage()
        self.assertEqual(len(self.requests()), 2)
        self.assertFalse(self.ran.exists())

    def test_machine_comments_are_not_takeover_but_human_comment_or_edit_is(self) -> None:
        self.configure(rounds=2)
        self.manage(mode="RETRY")  # only the recorded escalation comment is on the timeline
        self.assertTrue(self.ran.exists())
        self.assertEqual([e["decision"] for e in self.events("manage")], ["RETRY"])
        self.assertEqual([l["name"] for l in self.state["issue"]["labels"]], ["ready-for-agent"])
        for activity in (
            {"event": "commented", "id": 555, "body": "I will take this", "created_at": "2026-01-01T00:00:30Z",
             "updated_at": "2026-01-01T00:00:30Z", "user": {"login": "maintainer"}},
            {"event": "edited", "created_at": "2026-01-01T00:00:30Z", "actor": {"login": "maintainer"}},
            "edit-machine-comment",
        ):
            with self.subTest(activity=activity):
                self.reset()
                self.escalate("gate failed again", 1)
                if activity == "edit-machine-comment":
                    self.state["timeline"][-1]["updated_at"] = "2026-01-01T00:00:45Z"
                else:
                    self.state["timeline"].append(activity)
                self.manage(mode="RETRY")
                self.assertFalse(self.ran.exists())
                self.assertEqual(self.events("manage"), [])
                self.assertEqual(self.requests(), [])
                self.assertEqual([l["name"] for l in self.state["issue"]["labels"]], ["ready-for-human"])

    def test_each_terminal_manager_path_publishes_once_and_recovery_never_notifies(self) -> None:
        proc = self.manage("--dry-run", mode="HUMAN")
        self.assertIn("routing stays advisory", proc.stdout)
        self.assertEqual((self.requests(), self.events("handoff"), self.calls("issue", "comment")), ([], [], []))
        cases = [
            ("manager transport failure", dict(mode="fail"),
             "the manager could not run, so there is no automatic diagnosis"),
            ("manager configuration error", dict(manager_cmd=["omp", "-p", "{prompt}", "--cwd", "{cwd}"]),
             "the manager could not run, so there is no automatic diagnosis"),
            ("rounds exhausted", dict(round_number=2), "the manager's 1 allowed round(s) are exhausted"),
            ("no manager", dict(manager=False), "no manager is configured, so nothing recovers automatically"),
        ]
        for name, setup, expected in cases:
            with self.subTest(path=name):
                self.reset()
                self.configure(manager=setup.get("manager", True), manager_cmd=setup.get("manager_cmd"))
                self.escalate("PR #9: CI failed (unit); `factory-approved` label removed", setup.get("round_number", 1))
                self.manage(mode=setup.get("mode", "HUMAN"))
                requests = self.requests()
                self.assertEqual(len(requests), 1)
                body = requests[0]["body"]
                self.assertIn(expected, body)
                self.assertIn("@ci-owner — you own this decision (reason)", body)
                self.assertNotIn("transport exploded", body)
                self.assertNotIn("Diagnosis for the human", body)
                if "could not run" in expected:
                    self.assertEqual([e["reason"] for e in self.events("escalate")][-1], "manager_failed")
                if name in ("rounds exhausted", "no manager"):
                    self.assertFalse(self.ran.exists())
                self.manage(mode=setup.get("mode", "HUMAN"))
                self.assertEqual(len(self.requests()), 1)
                self.assertEqual(self.calls("issue", "edit"), [])

    def test_local_paths_candidates_unassigned_and_unverified_team_are_public_safe(self) -> None:
        self.reset()
        self.escalate(f"PR #9: rebase onto moved main conflicts; worktree {self.factory}/wt-7", 1)
        self.state["pr"] = {"url": PR_URL, "files": [{"path": "src/auth/a.py"}, {"path": "docs/b.md"}]}
        self.manage(mode="HUMAN")
        body = self.requests()[0]["body"]
        self.assertIn("worktree (local path withheld)", body)
        self.assertNotIn(str(self.factory), body)
        self.assertIn("@auth-owner, @docs-owner — candidates (component)", body)
        self.assertEqual(self.events("handoff")[0]["target"], "@auth-owner @docs-owner")

        with self.subTest(route="unassigned then claimed"):
            self.reset()
            self.state["pr"] = None
            self.escalate("wall-clock budget (30 min) exceeded", 1)
            self.manage(mode="HUMAN")
            body = self.requests()[0]["body"]
            self.assertIn("No decision owner could be determined (unassigned)", body)
            self.assertNotIn("@repo-owner", body)  # implementation without change paths never guesses
            self.assertNotIn("Pull request:", body)
            self.state["issue"]["body"] += "\n\n**Decision owner**\n\n@lead"
            self.manage()
            self.assertIn("the decision owner is now @lead", self.requests()[1]["body"])
            self.state["issue"]["body"] = "**Scope**\nA ticket."

        with self.subTest(route="team on a user-owned repository"):
            self.reset()
            self.configure(collaboration='[collaboration.reasons]\nci = "@acme/ci"\n')
            self.escalate("PR #9: CI failed (unit)", 1)
            self.manage(mode="HUMAN")
            body = self.requests()[0]["body"]
            self.assertIn("The routed owner declaration is invalid", body)
            self.assertIn("team destinations are rejected for a user-owned repository", body)
            self.assertNotIn("@acme/ci —", body)
            self.assertEqual(self.events("handoff")[0]["target"], "invalid")

    def test_unreadable_routing_defers_publication_until_the_ticket_can_be_read(self) -> None:
        proc = self.manage(mode="HUMAN", fail_read=True)
        self.assertIn("deferred; the ticket could not be read for routing", proc.stdout)
        self.assertEqual((self.requests(), self.events("handoff")), ([], []))
        self.manage()
        self.assertEqual(len(self.requests()), 1)
        self.assertIn("@auth-owner — you own this decision", self.requests()[0]["body"])

    def test_initiative_records_never_receive_requests(self) -> None:
        self.configure(manager=False)  # terminal at once; the handoff pass itself must refuse
        self.state["issue"]["labels"] = [{"name": "ready-for-human"}, {"name": "initiative"}]
        proc = self.manage()
        self.assertIn("refused", proc.stdout)
        self.assertEqual((self.requests(), self.calls("issue", "comment"), self.calls("issue", "edit")), ([], [], []))
        self.assertFalse(self.ran.exists())


class ReceiptTest(unittest.TestCase):
    def test_escalate_journals_the_comment_it_created_and_nothing_when_gh_fails(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dispatch.configure(config.Config(root, REPO))
            outputs = iter([subprocess.CompletedProcess([], 0, f"{ISSUE_URL}#issuecomment-4242\n", ""),
                            subprocess.CompletedProcess([], 1, "", "boom")])

            def run(cmd, *args, **kwargs):
                return next(outputs) if cmd[:3] == ["gh", "issue", "comment"] else subprocess.CompletedProcess(cmd, 0, "", "")

            with patch.object(dispatch, "run", side_effect=run):
                dispatch.escalate(7, "gate failed", None)
                dispatch.escalate(7, "gate failed again", None)
            rows = lifecycle.read_events(dispatch.EVENTS)
            self.assertEqual([(e["kind"], e["comment"], e["round"], e["url"]) for e in rows if e.get("event") == "comment"],
                             [("escalation", 4242, 1, f"{ISSUE_URL}#issuecomment-4242")])
            self.assertEqual([e["round"] for e in rows if e.get("event") == "escalate"], [1, 2])

    def test_terminal_reasons_and_public_redaction(self) -> None:
        cfg = config.Config(Path("/nonexistent"), REPO, manager=["m"], manager_rounds=1)
        escalation = {"event": "escalate", "round": 1}
        self.assertIsNone(handoff.terminal(cfg, [escalation], escalation))
        frontier_row = {"event": "manage", "round": 1, "pr": 9, "decision": "FIX"}
        self.assertIn("spent by its PR #9 FIX decision", handoff.terminal(cfg, [escalation, frontier_row], escalation))
        self.assertIsNone(handoff.terminal(cfg, [escalation, {**frontier_row, "round": 2}], escalation))
        self.assertIsNone(handoff.terminal(cfg, [escalation, {"event": "manage", "round": 1, "decision": "RETRY"}], escalation))
        self.assertIn("asked for a human", handoff.terminal(cfg, [escalation, {"event": "manage", "round": 1, "decision": "HUMAN"}], escalation))
        self.assertIn("could not run", handoff.terminal(
            cfg, [escalation, {"event": "escalate", "round": 1, "reason": "manager_failed"},
                  {"event": "manage", "round": 1, "decision": "HUMAN"}], escalation))
        self.assertIn("exhausted", handoff.terminal(cfg, [], {"round": 2}))
        self.assertIn("no manager", handoff.terminal(config.Config(Path("/nonexistent"), REPO), [], escalation))
        self.assertEqual(handoff.public("see https://github.com/acme/widgets/pull/9 and /home/me/.factory/wt-7 or a/b"),
                         "see https://github.com/acme/widgets/pull/9 and (local path withheld) or a/b")


if __name__ == "__main__":
    unittest.main()
