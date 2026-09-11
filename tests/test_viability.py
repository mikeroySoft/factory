"""Observable viability-manager behavior with local GitHub and model stand-ins."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from factory import config, dispatch, evidence, manage


GH = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

path = Path(os.environ["VIABILITY_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state.setdefault("calls", []).append(args)

def save():
    path.write_text(json.dumps(state))

def labels(target):
    return {label["name"] for label in target.get("labels", [])}

def target(kind, number):
    rows = state["prs" if kind == "pr" else "issues"]
    return next(row for row in rows if row["number"] == number)

if args[:2] in (["pr", "list"], ["issue", "list"]):
    kind = args[0]
    wanted = args[args.index("--label") + 1]
    rows = state["prs" if kind == "pr" else "issues"]
    print(json.dumps([row for row in rows if row["state"].upper() == "OPEN" and wanted in labels(row)]))
    save()
    raise SystemExit

if args[:2] in (["pr", "view"], ["issue", "view"]):
    print(json.dumps(target(args[0], int(args[2]))))
    save()
    raise SystemExit

if args and args[0] == "api" and "/timeline" in args[1]:
    number = args[1].split("/")[-2]
    print(json.dumps([state["timelines"].get(number, [])]))
    save()
    raise SystemExit

if len(args) >= 3 and args[:2] in (["pr", "comment"], ["pr", "edit"], ["issue", "comment"], ["issue", "edit"]):
    kind, action, number = args[0], args[1], int(args[2])
    mutation = {"kind": kind, "action": action, "number": number, "args": args[3:]}
    state.setdefault("mutations", []).append(mutation)
    key = f"{kind} {action} {number}"
    if state.get("fail", {}).get(key, 0):
        state["fail"][key] -= 1
        mutation["failed"] = True
        save()
        print("fixture mutation failure", file=sys.stderr)
        raise SystemExit(1)
    row = target(kind, number)
    if action == "comment":
        row.setdefault("comments", []).append(args[args.index("--body") + 1])
    else:
        names = [label["name"] for label in row.get("labels", [])]
        for flag, operation in (("--remove-label", "remove"), ("--add-label", "add")):
            start = 0
            while flag in args[start:]:
                index = args.index(flag, start)
                name = args[index + 1]
                if operation == "remove":
                    names = [item for item in names if item != name]
                elif name not in names:
                    names.append(name)
                start = index + 2
        row["labels"] = [{"name": name} for name in names]
    save()
    raise SystemExit

save()
print("unexpected gh fixture call: " + repr(args), file=sys.stderr)
raise SystemExit(97)
'''

MODEL = r'''import json, sys
from pathlib import Path

state_path, prompt_path = map(Path, sys.argv[1:3])
state = json.loads(state_path.read_text())
prompt = prompt_path.read_text()
titles = [row["title"] for row in state["prs"] + state["issues"]]
title = next(title for title in titles if title in prompt)
state["model_runs"] = state.get("model_runs", 0) + 1
state.setdefault("model_order", []).append(title)
action = state.get("during", {}).pop(title, None)
rows = state["prs"] + state["issues"]
row = next(item for item in rows if item["title"] == title)
if action == "edit":
    row["body"] += " human edit"
    row["updatedAt"] = "2026-09-09T00:01:00Z"
    state["timelines"][str(row["number"])].append({"id": 9001, "event": "edited", "actor": {"login": "human"}})
elif action == "unlabel":
    row["labels"] = [label for label in row["labels"] if label["name"] != "needs-viability"]
    state["timelines"][str(row["number"])].append({"id": 9002, "event": "unlabeled", "label": {"name": "needs-viability"}, "actor": {"login": "human"}})
elif action == "head":
    row["headRefOid"] = "changed-head"
output = state["outputs"][title].pop(0)
if isinstance(output, str):
    output = {"stdout": output, "exit": 0}
state_path.write_text(json.dumps(state))
sys.stdout.write(output.get("stdout", ""))
sys.stderr.write(output.get("stderr", ""))
raise SystemExit(output.get("exit", 0))
'''


class ViabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state_path = self.root / "state.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.model = self.root / "model.py"
        self.model.write_text(MODEL)
        self.state = {
            "issues": [], "prs": [], "timelines": {}, "outputs": {},
            "calls": [], "mutations": [], "model_runs": 0,
        }
        self.save()
        self.enterContext(patch.dict(os.environ, {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "VIABILITY_STATE": str(self.state_path),
            "FACTORY_LIFECYCLE_CONTEXT": "",
        }))
        self.sources = self.enterContext(patch.object(
            evidence, "viability_sources", create=True, side_effect=self.viability_sources,
        ))
        self.configure()

    def configure(self, enabled: bool = True) -> None:
        manager = [sys.executable, str(self.model), str(self.state_path), "{prompt}", "{cwd}"] if enabled else None
        dispatch.configure(config.Config(self.repo, "acme/widgets", manager=manager))

    def save(self) -> None:
        self.state_path.write_text(json.dumps(self.state))

    def load(self) -> dict:
        self.state = json.loads(self.state_path.read_text())
        return self.state

    def viability_sources(self, cfg, target, kind="issue") -> list[dict]:
        return [{
            "id": "S1", "label": f"{kind} #{target['number']}: {target['title']}",
            "text": f"{target['title']}: {target['body']} Demand is documented.",
            "truncated": False, "url": target["url"],
        }]

    def add(self, kind: str, number: int, title: str, output: str | dict, *,
            labels: tuple[str, ...] = (), request: int | None = None, state: str = "OPEN") -> dict:
        opt_in = "needs-review" if kind == "pr" else "needs-viability"
        row = {
            "number": number, "title": title, "body": f"Scope for {title}", "state": state,
            "labels": [{"name": name} for name in (opt_in, *labels)],
            "url": f"https://github.invalid/acme/widgets/{'pull' if kind == 'pr' else 'issues'}/{number}",
            "updatedAt": "2026-09-09T00:00:00Z",
        }
        if kind == "pr":
            row["headRefOid"] = f"head-{number}"
        self.state["prs" if kind == "pr" else "issues"].append(row)
        request = request if request is not None else number * 100
        self.state["timelines"][str(number)] = [{
            "id": request, "event": "labeled", "label": {"name": opt_in},
            "actor": {"login": "maintainer"}, "created_at": "2026-09-09T00:00:00Z",
        }]
        self.state["outputs"][title] = [output]
        self.save()
        return row

    def events(self) -> list[dict]:
        path = self.repo / ".factory" / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def decisions(self) -> list[dict]:
        return [event for event in self.events() if event.get("event") == "viability"]

    def labels(self, kind: str, number: int) -> set[str]:
        state = self.load()
        rows = state["prs" if kind == "pr" else "issues"]
        return {label["name"] for label in next(row for row in rows if row["number"] == number)["labels"]}

    def test_prs_run_before_issues_and_build_only_queues_issue_triage(self) -> None:
        self.add("issue", 20, "Issue direction", "Useful [S1]\nVERDICT: BUILD")
        self.add("pr", 10, "PR direction", "Useful [S1]\nVERDICT: BUILD")

        manage.viability_pass()

        state = self.load()
        self.assertEqual(state["model_order"], ["PR direction", "Issue direction"])
        comments = [(m["kind"], m["number"]) for m in state["mutations"] if m["action"] == "comment"]
        self.assertEqual(comments, [("pr", 10), ("issue", 20)])
        self.assertEqual(self.labels("pr", 10), set())
        self.assertEqual(self.labels("issue", 20), {"needs-triage"})
        pr_edit = next(m for m in state["mutations"] if m["kind"] == "pr" and m["action"] == "edit")
        self.assertNotIn("--add-label", pr_edit["args"])
        self.assertFalse(any(call[:2] in (["pr", "merge"], ["pr", "review"]) for call in state["calls"]))
        decisions = self.decisions()
        self.assertEqual([(d["kind"], d.get("pr", d.get("ticket")), d["verdict"], d["request"])
                          for d in decisions], [("pr", 10, "BUILD", 1000), ("issue", 20, "BUILD", 2000)])

    def test_dont_build_and_defer_leave_issues_open_without_terminal_labels(self) -> None:
        self.add("issue", 21, "No demand", "Market is absent [S1]\nVERDICT: DONT_BUILD")
        self.add("issue", 22, "Need evidence", "Evidence is incomplete [S1]\nVERDICT: DEFER")

        manage.viability_pass()

        state = self.load()
        self.assertEqual({d["verdict"] for d in self.decisions()}, {"DONT_BUILD", "DEFER"})
        for number in (21, 22):
            self.assertEqual(self.labels("issue", number), set())
            row = next(row for row in self.load()["issues"] if row["number"] == number)
            self.assertEqual(row["state"], "OPEN")
        self.assertFalse(any(call[:2] in (["issue", "close"], ["issue", "reopen"])
                             for call in state["calls"]))

    def test_partial_mutation_failures_are_durable_and_never_replay(self) -> None:
        self.add("issue", 23, "Fragile edit", "Direction is sound [S1]\nVERDICT: BUILD", request=2301)
        self.add("issue", 24, "Fragile comment", "Direction is sound [S1]\nVERDICT: BUILD", request=2401)
        self.state["fail"] = {"issue edit 23": 1, "issue comment 24": 1}
        self.save()

        manage.viability_pass()
        manage.viability_pass()

        state = self.load()
        self.assertEqual(state["model_runs"], 2)
        attempts = {
            number: [(m["action"], m.get("failed", False)) for m in state["mutations"] if m["number"] == number]
            for number in (23, 24)
        }
        self.assertEqual(attempts, {
            23: [("comment", False), ("edit", True)],
            24: [("comment", True)],
        })
        self.assertEqual([(d["request"], d["verdict"]) for d in self.decisions()],
                         [(2301, "BUILD"), (2401, "BUILD")])

    def test_pr_replay_is_suppressed_until_a_new_label_request(self) -> None:
        self.add("pr", 24, "Reconsider PR", "Promising [S1]\nVERDICT: BUILD", request=2401)
        self.state["outputs"]["Reconsider PR"].append("Costs changed [S1]\nVERDICT: DONT_BUILD")
        self.save()

        manage.viability_pass()
        manage.viability_pass()
        state = self.load()
        self.assertEqual(state["model_runs"], 1)
        self.assertEqual(sum(m["action"] == "comment" for m in state["mutations"]), 1)
        row = state["prs"][0]
        row["labels"].append({"name": "needs-review"})
        state["timelines"]["24"].append({
            "id": 2402, "event": "labeled", "label": {"name": "needs-review"},
            "actor": {"login": "maintainer"}, "created_at": "2026-09-09T00:02:00Z",
        })
        self.state = state
        self.save()

        manage.viability_pass()

        state = self.load()
        self.assertEqual(state["model_runs"], 2)
        self.assertEqual([(d["request"], d["verdict"]) for d in self.decisions()],
                         [(2401, "BUILD"), (2402, "DONT_BUILD")])
        self.assertEqual(self.labels("pr", 24), set())
        pr_edits = [mutation for mutation in state["mutations"]
                    if mutation["kind"] == "pr" and mutation["action"] == "edit"]
        self.assertEqual(len(pr_edits), 2)
        self.assertFalse(any("--add-label" in mutation["args"] for mutation in pr_edits))

    def test_disabled_competing_and_dry_run_never_infer_mutate_or_persist(self) -> None:
        self.add("issue", 25, "Disabled", "Would build [S1]\nVERDICT: BUILD")
        self.configure(False)
        manage.viability_pass()
        self.assertEqual(self.load()["calls"], [])
        self.assertEqual(self.load()["model_runs"], 0)
        self.assertEqual(self.events(), [])

        self.configure()
        self.state = {"issues": [], "prs": [], "timelines": {}, "outputs": {},
                      "calls": [], "mutations": [], "model_runs": 0}
        competing = ("needs-triage", "needs-info", "ready-for-agent", "ready-for-human",
                     "factory-approved", "wontfix", "factory-held")
        for index, label in enumerate(competing, 30):
            self.add("issue", index, f"Competing {label}", "Would build [S1]\nVERDICT: BUILD", labels=(label,))
        self.add("pr", 40, "Competing PR", "Would build [S1]\nVERDICT: BUILD", labels=("ready-for-agent",))
        self.add("issue", 41, "Closed issue", "Would build [S1]\nVERDICT: BUILD", state="CLOSED")
        manage.viability_pass()
        self.assertEqual(self.load()["model_runs"], 0)
        self.assertEqual(self.load()["mutations"], [])
        self.assertEqual(self.events(), [])

        self.state = {"issues": [], "prs": [], "timelines": {}, "outputs": {},
                      "calls": [], "mutations": [], "model_runs": 0}
        self.add("issue", 42, "Dry issue", "Would build [S1]\nVERDICT: BUILD")
        self.add("pr", 43, "Dry PR", "Would build [S1]\nVERDICT: BUILD")
        self.sources.reset_mock()
        manage.viability_pass(dry_run=True)
        state = self.load()
        self.assertEqual(state["model_runs"], 0)
        self.assertEqual(state["mutations"], [])
        self.sources.assert_not_called()
        self.assertEqual(self.events(), [])
        self.assertFalse((self.repo / ".factory").exists())

    def test_held_ticket_runs_once_after_the_lock_is_released(self) -> None:
        self.add("issue", 44, "Wait for lock", "Useful [S1]\nVERDICT: BUILD", request=4401)
        lock_path = self.repo / ".factory" / "locks" / "44.lock"
        lock_path.parent.mkdir(parents=True)
        lock = lock_path.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manage.viability_pass()
            self.assertEqual(self.load()["model_runs"], 0)
            self.assertEqual(self.load()["mutations"], [])
            self.assertEqual(self.decisions(), [])
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

        manage.viability_pass()

        self.assertEqual(self.load()["model_runs"], 1)
        self.assertEqual(self.labels("issue", 44), {"needs-triage"})
        self.assertEqual([(d["request"], d["verdict"]) for d in self.decisions()], [(4401, "BUILD")])

    def test_human_changes_during_inference_prevent_stale_actions(self) -> None:
        cases = (("issue", 50, "edit"), ("issue", 51, "unlabel"), ("pr", 52, "head"))
        for kind, number, action in cases:
            with self.subTest(action=action):
                self.state = {"issues": [], "prs": [], "timelines": {}, "outputs": {},
                              "calls": [], "mutations": [], "model_runs": 0,
                              "during": {f"Stale {action}": action}}
                self.add(kind, number, f"Stale {action}", "Apparently useful [S1]\nVERDICT: BUILD")
                before = len(self.decisions())

                manage.viability_pass()

                state = self.load()
                self.assertEqual(state["model_runs"], 1)
                self.assertEqual(state["mutations"], [])
                self.assertEqual(len(self.decisions()), before)

    def test_bad_or_uncited_model_results_only_record_diagnostic_defers(self) -> None:
        outputs = {
            "Malformed": "Evidence [S1] but no decision",
            "Unknown verdict": "Evidence [S1]\nVERDICT: MAYBE",
            "Unknown citation": "Unsupported [S999]\nVERDICT: BUILD",
            "Uncited": "Looks useful\nVERDICT: BUILD",
            "Failed": {"stderr": "model unavailable", "exit": 2},
        }
        for index, (title, output) in enumerate(outputs.items(), 60):
            self.add("issue", index, title, output)

        manage.viability_pass()

        state = self.load()
        decisions = self.decisions()
        self.assertEqual(len(decisions), 5)
        self.assertEqual({decision["verdict"] for decision in decisions}, {"DEFER"})
        for row in state["issues"]:
            self.assertFalse({"needs-triage", "ready-for-agent"} & {label["name"] for label in row["labels"]})
        edits = [mutation for mutation in state["mutations"] if mutation["action"] == "edit"]
        self.assertFalse(any("--add-label" in mutation["args"] for mutation in edits))
        self.assertEqual(state["model_runs"], 5)


if __name__ == "__main__":
    unittest.main()
