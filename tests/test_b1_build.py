import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "experiments" / "b1-build"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"b1_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build, case, sandbox = _load("build"), _load("case"), _load("sandbox")
SANDBOX_UNAVAILABLE = sandbox.available()
LIMITS = {"memory_bytes": 256 << 20, "cpu_cores": 2, "max_tasks": 32, "max_file_bytes": 1 << 20,
          "max_output_bytes": 64 << 20, "output_reserve_bytes": 1 << 16}


def issue(number, state="OPEN", labels=(), body=""):
    return {"number": number, "title": f"T{number}", "state": state, "body": body,
            "labels": [{"name": name} for name in labels]}


INITIATIVE_BODY = (
    "**Status**\nunderway\n\n**Plan**\nNOW: deliver #2 then #3.\nNEXT: #4 and #2 again.\n"
    "Prose naming #5 is not a tier line.\nLATER/conditional: #5.\n\n"
    "**Implementation links**\n#2 #3 #4 #5\n\n**Open decisions**\nNot #9.\n")


def frozen_issues():
    return {
        "initiative": issue(1, "CLOSED", body=INITIATIVE_BODY),
        "linked": {"2": issue(2, "CLOSED", ("factory-held",), "Blocked by: #7"),
                   "3": issue(3, "OPEN", ("factory-held",), "## Blocked by\nBlocked by: #2, #7\nblocked by: #8"),
                   "4": issue(4, "OPEN", (), "Text: Blocked by: #7 is not a line start."),
                   "5": issue(5, "OPEN")},
        "blockers": {"2": issue(2, "CLOSED"), "7": issue(7, "OPEN"), "8": issue(8, "CLOSED")},
    }


def baseline_for(oracle_rows, unavailable=()):
    """An existing-representation JSON that states exactly the oracle facts."""
    facts = {(k, n): v for k, n, v in oracle_rows}
    children, sources = [], [{"id": "S1", "label": "Initiative #1 plan"}]
    for number in (2, 3, 4, 5):
        state = facts[("state", number)]
        labels = ["factory-held"] if facts.get(("held", number)) else []
        blockers = [{"number": b, "state": "OPEN"} for b in facts.get(("open_blockers", number), [])]
        if number in unavailable:
            blockers = [{"number": 99, "unavailable": "github_failed"}]
        children.append({"number": number, "title": f"T{number}", "state": state, "labels": labels,
                         "blockers": blockers, "source": f"S{number + 10}"})
        sources.append({"id": f"S{number + 10}", "label": f"Implementation ticket #{number}"})
    tiers = {str(n): v for (k, n), v in facts.items() if k == "tier"}
    plan = {"number": 1, "title": "Init", "state": "CLOSED", "status": "underway", "owner": "o", "source": "S1",
            "revision": {"sha256": facts[("revision", 1)], "observed_at": "2026-10-03T00:00:00Z"},
            "children": children, "next": {"priorities": tiers, "action": {"kind": "decide", "experiment": "e", "sources": ["S1"]}}}
    return {"ok": True, "coverage": {"status": "complete"}, "errors": [], "sources": sources,
            "investigation": {"plans": [plan]}}


REV = "a" * 64


class OracleTest(unittest.TestCase):
    def test_facts_follow_the_declared_contracts(self):
        rows = build.derive_oracle(frozen_issues(), REV)
        facts = {(k, n): v for k, n, v in rows}
        self.assertEqual(facts[("revision", 1)], REV)
        # First tier line naming a ticket wins; non-tier prose and other sections never assign tiers.
        self.assertEqual(facts[("tier", 2)], "NOW")
        self.assertEqual(facts[("tier", 4)], "NEXT")
        self.assertEqual(facts[("tier", 5)], "LATER")
        self.assertNotIn(("state", 9), facts)
        # Closed tickets carry no held/blocker facts; open blockers only, line-start syntax only.
        self.assertNotIn(("held", 2), facts)
        self.assertEqual(facts[("open_blockers", 3)], [7])
        self.assertEqual(facts[("open_blockers", 4)], [])
        self.assertEqual((facts[("held", 3)], facts[("held", 5)]), (True, False))

    def test_only_open_tickets_blockers_are_fetched(self):
        root = issue(1, body="**Implementation links**\n#2 #3\n")
        linked = {2: issue(2, "CLOSED", body="Blocked by: #20"), 3: issue(3, body="Blocked by: #30")}
        fetched = []

        def fake(repo, number):
            fetched.append(number)
            return root if number == 1 else linked.get(number) or issue(number)
        with mock.patch.object(build, "gh_issue", side_effect=fake):
            frozen = build.freeze_issues("o/r", 1)
        self.assertEqual(fetched, [1, 2, 3, 30])
        self.assertEqual(sorted(frozen["blockers"]), ["30"])


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.rows = build.derive_oracle(frozen_issues(), REV)
        self.oracle = case.as_facts(json.loads(json.dumps(self.rows)))
        self.baseline = baseline_for(self.rows)
        self.sources = {s["id"]: s["label"] for s in self.baseline["sources"]}

    def score(self, text):
        stated, unresolved = case.candidate_facts(text, self.sources, 1)
        return case.compare(stated, self.oracle), unresolved

    def test_faithful_briefing_retains_every_fact(self):
        text = case.render(self.baseline, 1)
        result, unresolved = self.score(text)
        self.assertEqual((result["omitted"], result["unsupported"], unresolved), ([], [], []))
        self.assertEqual(result["retained"], len(self.oracle))
        base = case.compare(case.baseline_facts(self.baseline, 1), self.oracle)
        self.assertEqual((base["omitted"], base["unsupported"]), ([], []))

    def test_wrong_value_is_both_omitted_and_unsupported(self):
        text = case.render(self.baseline, 1).replace("- #4 OPEN tier=NEXT", "- #4 OPEN tier=NOW")
        result, _ = self.score(text)
        self.assertEqual(result["unsupported"], [["tier", 4, "NOW"]])
        self.assertEqual(result["omitted"], [["tier", 4, "NEXT"]])

    def test_dropped_line_and_bad_citation_are_reported(self):
        lines = case.render(self.baseline, 1).splitlines()
        lines = [line for line in lines if not line.startswith("- #5 ")]
        lines = [line.replace("[S13]", "[S12]") if line.startswith("- #3 ") else line for line in lines]
        result, unresolved = self.score("\n".join(lines))
        self.assertIn(["state", 5, "OPEN"], result["omitted"])
        self.assertEqual(len(unresolved), 1)
        self.assertTrue(unresolved[0].startswith("- #3 "))

    def test_unavailable_blocker_evidence_is_unknown_not_a_claim(self):
        baseline = baseline_for(self.rows, unavailable=(3,))
        text = case.render(baseline, 1)
        self.assertIn("open_blockers=unknown", text)
        result, _ = self.score(text)
        self.assertEqual(result["omitted"], [["open_blockers", 3, [7]]])
        self.assertEqual(result["unsupported"], [])
        base = case.compare(case.baseline_facts(baseline, 1), self.oracle)
        self.assertEqual((base["omitted"], base["unsupported"]), ([["open_blockers", 3, [7]]], []))


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "run").mkdir()
        (self.tmp / "out").mkdir()
        self.run = build.Run(self.tmp / "run", 1 << 20, 1 << 10)

    def settle(self, outcome, returncode=0):
        return build.settle(self.run, {"outcome": outcome, "returncode": returncode}, self.tmp / "out")[:2]

    def write_result(self, safety=True, validity=True):
        (self.tmp / "out" / "result.json").write_text(json.dumps(
            {"safety": {"ok": safety}, "validity": {"ok": validity}, "product": None}))

    def test_boundary_failures_never_complete(self):
        self.assertEqual(self.settle("launch_failed"), ("invalid", "isolation_launch_failed"))
        self.assertEqual(self.settle("limits_mismatch"), ("invalid", "isolation_limits_mismatch"))
        self.assertEqual(self.settle("time_limit"), ("interrupted", "time_limit"))
        self.assertEqual(self.settle("killed"), ("interrupted", "killed"))
        self.assertEqual(self.settle("stopped"), ("interrupted", "stop_requested"))
        self.assertEqual(self.settle("exited", 3), ("invalid", "payload_exit:3"))
        self.assertEqual(self.settle("exited"), ("invalid", "result_missing_or_malformed"))

    def test_isolation_breach_invalidates_a_finished_payload(self):
        self.write_result(safety=False)
        self.assertEqual(self.settle("exited"), ("invalid", "isolation_breach"))

    def test_wrong_result_shape_is_malformed_not_a_crash(self):
        (self.tmp / "out" / "result.json").write_text(json.dumps({"safety": "ok", "validity": {"ok": True}}))
        self.assertEqual(self.settle("exited"), ("invalid", "result_missing_or_malformed"))

    def test_output_over_budget_is_not_retained(self):
        (self.tmp / "out" / "result.json").write_bytes(b"x" * (1 << 20))
        self.assertEqual(self.settle("exited"), ("interrupted", "output_limit"))
        self.assertFalse((self.tmp / "run" / "result.json").exists())


class StartFailureTest(unittest.TestCase):
    def test_host_failure_during_freeze_still_settles_the_run(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        timeout = build.subprocess.TimeoutExpired(["gh"], 60)
        with mock.patch.object(build, "committed_source", return_value="0" * 40), \
                mock.patch.object(build.sandbox, "available", return_value=None), \
                mock.patch.object(build, "checkout", return_value="1" * 64), \
                mock.patch.object(build, "freeze_issues", side_effect=timeout), \
                mock.patch("builtins.print"):
            build.main(["start", "--output-dir", str(tmp)])
        (run,) = tmp.iterdir()
        status = json.loads((run / "status.json").read_text())
        report = json.loads((run / "report.json").read_text())
        self.assertEqual((status["state"], status["reason"]), ("invalid", "producer_error:TimeoutExpired"))
        self.assertTrue(status["out_of_lineage"])
        self.assertEqual(report["evidence"], {})
        self.assertEqual(sorted(status["artifacts"]), ["protocol.json", "report.json"])


@unittest.skipIf(SANDBOX_UNAVAILABLE, f"bubblewrap/systemd user scope unavailable: {SANDBOX_UNAVAILABLE}")
class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir=f"/run/user/{__import__('os').getuid():d}"))
        self.addCleanup(shutil.rmtree, self.tmp)
        for name in ("in", "out", "ev"):
            (self.tmp / name).mkdir()

    def go(self, code, seconds=20, **kw):
        return sandbox.run([sandbox.PYTHON, "-I", "-B", "-c", code], src=HERE, inp=self.tmp / "in",
                           out=self.tmp / "out", limits=LIMITS, seconds=seconds, evidence_dir=self.tmp / "ev", **kw)

    def test_every_required_denial_holds_inside(self):
        host = {"this test file": __file__, "temp root": str(self.tmp)}
        code = ("import sys,json; sys.path.insert(0,'/src'); import case;"
                f"open('/out/p.json','w').write(json.dumps(case.probes({host!r})))")
        result = self.go(code)
        self.assertEqual((result["outcome"], result["returncode"]), ("exited", 0))
        self.assertTrue(result["limits"]["ok"], result["limits"])
        rows = json.loads((self.tmp / "out" / "p.json").read_text())
        self.assertEqual([r["probe"] for r in rows if not r["denied"]], [])
        self.assertIn("host path: this test file", [r["probe"] for r in rows])

    def test_time_memory_and_file_limits_end_the_payload(self):
        self.assertEqual(self.go("import time; time.sleep(60)", seconds=2)["outcome"], "time_limit")
        # Touched pages only: an untouched calloc'd buffer is never charged to the memory cgroup.
        self.assertEqual(self.go("b=[]\nwhile True: b.append(b'x'*(32<<20))")["outcome"], "killed")
        big = self.go("open('/out/big','wb').write(b'x'*(2<<20))")
        self.assertEqual(big["outcome"], "exited")
        self.assertNotEqual(big["returncode"], 0)
        self.assertLessEqual((self.tmp / "out" / "big").stat().st_size, LIMITS["max_file_bytes"])

    def test_stop_request_and_limit_mismatch(self):
        stop = self.tmp / "stop-request"
        stop.touch()
        self.assertEqual(self.go("import time; time.sleep(60)", stop_file=stop)["outcome"], "stopped")
        wrong = {**sandbox.expected_limits(LIMITS), "pids.max": "1"}
        with mock.patch.object(sandbox, "expected_limits", return_value=wrong):
            result = self.go("open('/out/ran','w').write('1')")
        self.assertEqual(result["outcome"], "limits_mismatch")
        self.assertFalse((self.tmp / "out" / "ran").exists())


if __name__ == "__main__":
    unittest.main()
