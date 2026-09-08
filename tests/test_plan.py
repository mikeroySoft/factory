"""`factory plan` reads initiative issues through the real CLI against an isolated gh adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlencode

from factory import config, lifecycle, onboard, plan
from test_evidence import GH, READ_GUARD, REPO

ROOT = Path(__file__).resolve().parents[1]
PREFIX = f"repos/{REPO}/"
TEMPLATE = (onboard.TEMPLATES / "initiative.md").read_text()
BODY = TEMPLATE.split("---\n", 2)[2]  # issue body GitHub creates from the template


def issue(number, body, labels=(), title="Issue"):
    return {"number": number, "title": title, "state": "open", "body": body,
            "html_url": f"https://github.com/{REPO}/issues/{number}", "updated_at": "2026-01-02T03:04:05Z",
            "labels": [{"name": name} for name in labels]}


class TemplateTest(unittest.TestCase):
    def test_template_round_trips_through_reader_and_adds_only_initiative(self):
        self.assertIn("labels: initiative\n", TEMPLATE)
        self.assertNotIn(config.LABEL_TRIAGE, TEMPLATE.split("---\n", 2)[1])
        parsed = plan.parse(BODY)
        self.assertEqual(parsed["problems"], [])
        self.assertEqual((parsed["status"], parsed["owner"], parsed["links"]), ("proposed", "github-login", []))
        self.assertEqual(set(parsed["sections"]), set(plan.SECTIONS))

    def test_reader_names_missing_owner_and_bad_status_without_inferring(self):
        parsed = plan.parse(BODY.replace("**Owner**\n@github-login\n", "").replace("proposed", "done"))
        self.assertEqual(parsed["owner"], None)
        self.assertEqual(parsed["status"], None)
        self.assertTrue(parsed["malformed"])
        self.assertEqual(set(parsed["problems"]), {"missing section: Owner", "invalid owner: expected exactly one GitHub login",
                                                   f"invalid status: expected one of {', '.join(plan.STATUSES)}"})
        self.assertEqual(plan.parse(BODY.replace("- #N", "- #53 and #7, not #53"))["links"], [7, 53])

    def test_reader_reports_section_and_link_truncation_explicitly(self):
        links = " ".join(f"#{n}" for n in range(1, plan.LINKS + 2))
        parsed = plan.parse(BODY.replace("- #N", links).replace("**Outcome**\n", "**Outcome**\n" + "x" * (plan.SECTION_CAP + 1) + "\n"))
        self.assertEqual(parsed["links"], list(range(1, plan.LINKS + 1)))
        self.assertEqual(len(parsed["sections"]["Outcome"]), plan.SECTION_CAP)
        self.assertEqual(parsed["problems"], [f"truncated section: Outcome cut to {plan.SECTION_CAP} characters",
                                              f"truncated links: only the first {plan.LINKS} of {plan.LINKS + 1} implementation links are followed"])


class CliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        tools, guard = self.directory / "bin", self.directory / "guard"
        tools.mkdir()
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(READ_GUARD)
        (tools / "git").symlink_to(shutil.which("git"))
        (tools / "gh").write_text(f"#!{sys.executable} -S\n" + GH)
        (tools / "gh").chmod(0o755)
        self.responses_path, self.calls_path = self.directory / "responses.json", self.directory / "calls.jsonl"
        self.env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(guard), str(ROOT))), "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": str(tools), "HOME": str(self.directory / "home"), "XDG_CONFIG_HOME": str(self.directory / "host"),
                    "GH_CONFIG_DIR": str(self.directory / "gh"), "GH_TOKEN": "", "GITHUB_TOKEN": "", lifecycle.CONTEXT_ENV: "",
                    "EVIDENCE_RESPONSES": str(self.responses_path), "EVIDENCE_CALLS": str(self.calls_path)}
        subprocess.run(["git", "-C", str(self.root), "init", "-q", "-b", "main"], check=True)
        (self.root / ".factory.toml").write_text(f'[repo]\nslug = "{REPO}"\n')
        self.initiative = issue(52, BODY.replace("- #N", "- #53"), ["initiative"], "Collaboration")
        self.ordinary = issue(7, "**Scope**\nA ticket.", ["ready-for-agent"], "Ticket")
        self.child = issue(53, "**Scope**\nChild.\n\nProgramme: #52", ["ready-for-agent"], "Child")
        self.no_owner = issue(60, BODY.replace("**Owner**\n@github-login\n", ""), ["initiative"], "Ownerless")
        self.responses = {}
        for row in (self.initiative, self.ordinary, self.child, self.no_owner):
            self.responses[PREFIX + f"issues/{row['number']}"] = {"json": row}

    def page(self, number, value=None, **extra):
        query = urlencode(sorted({"labels": "initiative", "state": "all", "sort": "updated", "direction": "desc",
                                  "per_page": plan.PAGE_SIZE, "page": number}.items()))
        self.responses[PREFIX + "issues?" + query] = {"json": value, **extra}

    def run_plan(self, *argv, code=0):
        self.responses_path.write_text(json.dumps(self.responses))
        proc = subprocess.run([sys.executable, "-B", "-m", "factory.cli", "plan", *argv], cwd=self.root, env=self.env,
                              capture_output=True, timeout=60)
        self.assertNotIn(b"EVIDENCE_FORBIDDEN:", proc.stderr, proc.stderr.decode())
        self.assertEqual(proc.returncode, code, (proc.stdout.decode(), proc.stderr.decode()))
        data = json.loads(proc.stdout)
        self.assertEqual((data["schema_version"], data["ok"], data["scope"]["repository"]), (1, code == 0, None if code == 2 else REPO))
        self.assertIsInstance(data["observed_at"], str)
        for src in data["sources"]:
            self.assertTrue((src["url"] + "/").startswith(f"https://api.github.com/{PREFIX}"))
        calls = [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []
        self.assertTrue(all(call["method"] == "GET" for call in calls), calls)
        return data


class PlanCliTest(CliCase):
    def test_list_reports_plans_partial_second_page_and_malformed(self):
        full = [self.initiative] + [self.ordinary] * (plan.PAGE_SIZE - 1)
        self.page(1, full)
        self.page(2, None, exit=1, stderr="HTTP 502")
        data = self.run_plan("list", code=1)
        self.assertEqual([row["number"] for row in data["plans"]], [52])
        self.assertEqual(data["coverage"]["status"], "partial")
        self.assertEqual(data["error"]["code"], "partial_collection")
        self.assertEqual([(err["code"], err["source"].rsplit("&", 1)[1]) for err in data["errors"]], [("github_unavailable", "page=2")])
        self.assertTrue(any("page 2" in note for note in data["coverage"]["notices"]))

        self.page(1, [self.initiative, self.ordinary, self.no_owner])
        data = self.run_plan("list")
        self.assertEqual(data["coverage"]["status"], "complete")
        by_number = {row["number"]: row for row in data["plans"]}
        self.assertEqual(set(by_number), {52, 60})
        self.assertEqual((by_number[52]["status"], by_number[52]["owner"], by_number[52]["links"], by_number[52]["malformed"]),
                         ("proposed", "github-login", [53], False))
        self.assertEqual((by_number[60]["owner"], by_number[60]["malformed"]), (None, True))
        self.assertIn("missing section: Owner", by_number[60]["problems"])

    def test_inspect_reads_children_without_inferring_status_and_rejects_ordinary(self):
        self.child["state"] = "closed"
        self.responses[PREFIX + "issues/53"] = {"json": self.child}
        data = self.run_plan("inspect", "52")
        self.assertEqual(data["plan"]["status"], "proposed")
        self.assertEqual(data["plan"]["children"], [{"number": 53, "title": "Child", "state": "CLOSED",
                                                     "url": f"https://github.com/{REPO}/issues/53", "labels": ["ready-for-agent"]}])
        self.assertEqual(data["plan"]["sections"]["Outcome"], "The observable end state this initiative delivers.")

        data = self.run_plan("inspect", "7", code=1)
        self.assertEqual((data["plan"], data["error"]["code"]), (None, "not_initiative"))

        data = self.run_plan("inspect", "60", code=1)
        self.assertEqual((data["plan"]["owner"], data["plan"]["malformed"], data["coverage"]["status"]), (None, True, "partial"))
        self.assertEqual([err["code"] for err in data["errors"]], ["malformed_initiative"])

        del self.responses[PREFIX + "issues/53"]
        data = self.run_plan("inspect", "52", code=1)
        self.assertEqual(data["plan"]["children"], [{"number": 53, "unavailable": "github_unavailable"}])

    def test_usage_errors_exit_two(self):
        for bad in ("x", "²", "0"):
            data = self.run_plan("inspect", bad, code=2)
        self.assertEqual(data["error"]["code"], "invalid_request")
        self.assertFalse(self.calls_path.exists())


COLLAB = '''
[collaboration]
fallback = "repo-owner"
[collaboration.reasons]
ci = "ci-owner"
[collaboration.components]
"src/auth/" = "auth-owner"
"src/authentication" = "authn-owner"
"docs" = "@example/docs"
'''


class RouteCliTest(CliCase):
    def route(self, number, reason, *paths, code=0):
        argv = ["route", str(number), "--reason", reason]
        for path in paths:
            argv += ["--path", path]
        data = self.run_plan(*argv, code=code)
        if code != 2:
            self.assertEqual(data["scope"], {"repository": REPO, "issue": number, "reason": reason})
            self.assertEqual(data["route"]["reason"], reason)
            return data["route"]
        return data

    def configure(self, text=COLLAB):
        (self.root / ".factory.toml").write_text(f'[repo]\nslug = "{REPO}"\n{text}')

    def test_declared_decision_owner_wins_and_invalid_syntax_is_invalid_not_rerouted(self):
        self.configure()
        self.child["body"] += "\n\n**Decision owner**\n\n@lead"
        route = self.route(53, "ci")
        self.assertEqual((route["status"], route["owner"], route["source"]), ("selected", "lead", "decision_owner"))
        self.assertEqual(route["revision"]["issue_updated_at"], "2026-01-02T03:04:05Z")
        self.child["body"] = self.child["body"].replace("@lead", "not a login")
        route = self.route(53, "ci")
        self.assertEqual((route["status"], route["owner"], route["source"]), ("invalid", None, "decision_owner"))
        self.assertEqual(route["provenance"][-1]["step"], "decision_owner")

    def test_requirements_use_initiative_owner_then_fall_back(self):
        self.configure()
        route = self.route(53, "requirements")
        self.assertEqual((route["status"], route["owner"], route["source"]), ("selected", "github-login", "initiative"))
        self.assertEqual(route["revision"]["initiative_updated_at"], "2026-01-02T03:04:05Z")
        route = self.route(7, "requirements")
        self.assertEqual((route["owner"], route["source"]), ("repo-owner", "fallback"))
        self.assertEqual([s["outcome"] for s in route["provenance"]], ["absent", "absent", "absent", "skipped", "selected"])
        # initiative owner is a requirements source only
        route = self.route(53, "ci")
        self.assertEqual((route["owner"], route["source"]), ("ci-owner", "reason"))
        self.assertEqual((route["provenance"][1]["step"], route["provenance"][1]["outcome"]), ("initiative", "skipped"))

    def test_component_prefixes_are_exact_and_ambiguity_yields_candidates(self):
        self.configure()
        route = self.route(7, "implementation", "src/auth/login.py", "src/auth/x/y.py")
        self.assertEqual((route["status"], route["owner"], route["source"]), ("selected", "auth-owner", "component"))
        route = self.route(7, "implementation", "src/authentication/x.py")
        self.assertEqual(route["owner"], "authn-owner")
        route = self.route(7, "implementation", "src/authx/x.py")
        self.assertEqual((route["owner"], route["source"]), ("repo-owner", "fallback"))
        route = self.route(7, "implementation", "src/auth/a.py", "src/authentication/b.py")
        self.assertEqual((route["status"], route["owner"], route["candidates"]), ("candidates", None, ["auth-owner", "authn-owner"]))
        self.assertEqual(route["paths"], ["src/auth/a.py", "src/authentication/b.py"])

    def test_missing_paths_and_unknown_reason_do_not_guess(self):
        self.configure()
        route = self.route(7, "implementation")
        self.assertEqual((route["status"], route["owner"], route["provenance"][-1]["step"]), ("unassigned", None, "component"))
        route = self.route(7, "unknown")
        self.assertEqual((route["status"], route["provenance"][-1]["outcome"]), ("unassigned", "skipped"))

    def test_missing_configuration_leaves_only_ticket_sources(self):
        route = self.route(7, "ci")
        self.assertEqual((route["status"], route["provenance"][-1]["step"]), ("unassigned", "configuration"))
        route = self.route(53, "requirements")
        self.assertEqual(route["owner"], "github-login")

    def test_team_destination_rejected_on_user_repo_and_unknown_when_unreadable(self):
        self.configure()
        data = self.run_plan("route", "7", "--reason", "implementation", "--path", "docs/a.md", code=1)
        route = data["route"]
        self.assertEqual((route["status"], route["owner"], route["verification"], data["coverage"]["status"]),
                         ("selected", "@example/docs", "unknown", "partial"))
        self.assertEqual(route["provenance"][-1]["outcome"], "unknown")
        self.responses["repos/" + REPO] = {"json": {"owner": {"type": "User"}}}
        route = self.route(7, "implementation", "docs/a.md")
        self.assertEqual((route["status"], route["owner"], route["verification"]), ("invalid", None, "verified"))
        self.responses["repos/" + REPO] = {"json": {"owner": {"type": "Organization"}}}
        route = self.route(7, "implementation", "docs/a.md")
        self.assertEqual((route["owner"], route["verification"]), ("@example/docs", "verified"))

    def test_bad_configuration_and_usage_exit_two(self):
        self.configure('[collaboration.reasons]\nrequirements = "@org/"\n')
        self.assertEqual(self.route(7, "ci", code=2)["error"]["code"], "invalid_scope")
        self.configure('[collaboration.components]\n"../x" = "a"\n')
        self.assertEqual(self.route(7, "ci", code=2)["error"]["code"], "invalid_scope")
        for argv in (["route", "7"], ["route", "7", "--reason", "docs"], ["route", "7", "--reason", "ci", "--path"]):
            self.assertEqual(self.run_plan(*argv, code=2)["error"]["code"], "invalid_request")


if __name__ == "__main__":
    unittest.main()
