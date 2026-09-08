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


class PlanCliTest(unittest.TestCase):
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
            self.assertTrue(src["url"].startswith(f"https://api.github.com/{PREFIX}"))
        calls = [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []
        self.assertTrue(all(call["method"] == "GET" for call in calls), calls)
        return data

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


if __name__ == "__main__":
    unittest.main()
