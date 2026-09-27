import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "experiments" / "o1-observation" / "observer.py"
spec = importlib.util.spec_from_file_location("o1_observer", SCRIPT)
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def lc(ticket, root, at, outcome, stage="merge-eligibility", reason=None, execution=None, **extra):
    return {"event": "lifecycle", "kind": "exit", "ticket": ticket, "root_execution_id": root,
            "execution_id": execution or f"{root}-{stage}", "stage": stage, "at": at, "outcome": outcome,
            "reason": reason, "event_id": f"ev-{root}-{stage}-{outcome}", **extra}


def at(i):
    return f"2026-09-20T00:{i // 60:02d}:{i % 60:02d}Z"


class O1ObservationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.source = self.tmp / "events.jsonl"
        self.out = self.tmp / "runs"

    def write(self, rows, tail=b""):
        self.source.write_bytes(b"".join((r if isinstance(r, bytes) else json.dumps(r).encode()) + b"\n"
                                         for r in rows) + tail)

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = observer.main(list(argv))
        return code, buf.getvalue()

    def start(self, *extra):
        code, text = self.run_cli("start", "--source", str(self.source), "--output-dir", str(self.out), *extra)
        self.assertEqual(code, 0)
        return Path(json.loads(text)["run"])

    def fresh(self, cmd, run):
        proc = subprocess.run([sys.executable, str(SCRIPT), cmd, str(run)], capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)

    def twelve_plus(self):
        rows = []
        for i in range(14):  # 14 cases; latest 12 selected; nested stage exits must not multiply cases
            outcome = "merged" if i % 2 else "project_escalation"
            reason = None if i % 2 else "leak_scan_matches"
            for stage in ("merge", "merge-eligibility", "dispatcher"):
                rows.append(lc(100 + i, f"root{i}", at(i * 3), outcome, stage=stage, reason=reason))
            rows.append({"event": "merged" if i % 2 else "escalate", "ticket": 100 + i, "at": at(i * 3),
                         "reason": reason, "execution_id": f"root{i}-merge"})
        # Lifecycle-only terminal without a dispatcher record row: uncorroborated, never sampled.
        rows.append(lc(999, "fixture", at(100), "merged", stage="merge"))
        return rows

    def test_completed_run_is_reproducible_and_verifiable_in_fresh_process(self):
        self.write(self.twelve_plus())
        first, second = self.start(), self.start()
        code, rep = self.fresh("report", first)
        self.assertEqual(code, 0)
        self.assertTrue(rep["all_verified"])
        report = rep["report"]
        self.assertEqual(report["state"], "completed")
        self.assertEqual(report["disposition"], "pending")
        self.assertEqual([c["ticket"] for c in report["observations"]["cases"]], list(range(102, 114)))
        self.assertEqual(report["observations"]["category_counts"],
                         {"implementation_gate_review_failure": 6, "merged": 6})
        other = json.loads((second / "report.json").read_bytes())
        self.assertEqual(other["source"]["sha256"], report["source"]["sha256"])
        self.assertEqual(other["observations"]["cases"], report["observations"]["cases"])
        self.assertNotEqual(first, second)
        self.assertEqual(self.fresh("status", first)[1]["writer_active"], False)
        # Tampering with a retained artifact is detected.
        ev = first / "evidence.jsonl"
        os.chmod(ev, 0o644)
        ev.write_bytes(ev.read_bytes() + b"x")
        self.assertEqual(self.fresh("report", first)[0], 1)

    def test_cutoff_malformed_paths_and_free_text_are_inert(self):
        sentinel = self.tmp / "secret-do-not-open"
        sentinel.write_text("x")
        rows = [
            b"{not json", b"[1,2]",
            {"event": "escalate", "ticket": 1, "at": at(1), "reason": f"see {sentinel}; rm -rf /",
             "log": str(sentinel), "packet": str(sentinel)},
            {"event": "escalate", "ticket": 2, "at": "2026-09-28T00:00:00Z", "reason": "state_changed"},
            {"event": "merged", "ticket": 3, "at": "garbage"},
        ]
        self.write(rows, tail=b'{"event": "merged", "ticket": 4, "at": "')
        opened = []
        real_open = os.open

        def spy(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        with mock.patch("os.open", spy), mock.patch("subprocess.Popen", side_effect=AssertionError("subprocess")), \
                mock.patch("socket.socket", side_effect=AssertionError("network")):
            run = self.start()
        self.assertFalse([p for p in opened if "secret" in p])
        source_opens = [p for p in opened if p == str(self.source)]
        self.assertEqual(len(source_opens), 1)
        report = json.loads((run / "report.json").read_bytes())
        self.assertEqual(report["state"], "inconclusive")
        self.assertEqual(report["reason"], "fewer_cases_than_protocol")
        obs = report["observations"]
        self.assertEqual(obs["ledger"]["malformed"], 2)
        self.assertEqual(obs["ledger"]["after_cutoff"], 1)
        self.assertEqual(obs["ledger"]["untimed"], 1)
        self.assertFalse(report["source"]["complete"])
        self.assertGreater(report["source"]["discarded_partial_tail_bytes"], 0)
        [case] = obs["cases"]
        self.assertEqual((case["ticket"], case["category"]), (1, "unknown"))
        self.assertNotIn(str(sentinel), (run / "report.json").read_text())

    def test_retries_collapse_and_escalate_record_maps_to_lineage(self):
        rows = []
        for i in range(3):  # three dispatcher passes escalating the same way: one case, two retries
            rows.append(lc(7, f"r{i}", at(i), "project_escalation", reason="state_changed"))
            rows.append({"event": "escalate", "ticket": 7, "at": at(i), "reason": "state_changed",
                         "execution_id": f"r{i}-merge-eligibility"})
        rows.append(lc(7, "w", at(10), "project_escalation", stage="ticket", reason="REVISE verdict after 1 round"))
        rows.append(lc(7, "w", at(9), "product_feedback", stage="review", reason="REVISE"))
        rows.append({"event": "escalate", "ticket": 7, "at": at(10), "reason": "REVISE verdict after 1 round",
                     "execution_id": "w-ticket"})
        self.write(rows)
        report = json.loads((self.start() / "report.json").read_bytes())
        cases = report["observations"]["cases"]
        self.assertEqual([(c["retry_count"], c["category"]) for c in cases],
                         [(2, "dependency_ownership_wait"), (0, "implementation_gate_review_failure")])
        self.assertEqual(cases[0]["terminal_signal_count"], 6)
        self.assertEqual(cases[0]["lineage_source"], "root_execution_id")

    def test_missing_and_empty_sources(self):
        run = self.start()
        self.assertEqual(json.loads((run / "status.json").read_bytes())["state"], "invalid")
        self.assertEqual(json.loads((run / "report.json").read_bytes())["reason"], "source_missing")
        self.write([])
        run = self.start()
        self.assertEqual(json.loads((run / "report.json").read_bytes())["reason"], "no_qualifying_cases")

    def test_input_cap_captures_suffix_and_declares_coverage(self):
        self.write(self.twelve_plus())
        size = self.source.stat().st_size
        run = self.start("--max-input-bytes", str(size // 2))
        src = json.loads((run / "report.json").read_bytes())["source"]
        self.assertFalse(src["complete"])
        self.assertEqual(src["covered"][1], size)
        self.assertGreaterEqual(src["covered"][0], size - size // 2)
        self.assertEqual(self.source.read_bytes()[src["covered"][0] - 1:src["covered"][0]], b"\n")

    def test_time_and_output_caps_interrupt_and_keep_evidence(self):
        self.write(self.twelve_plus())
        run = self.start("--max-seconds", "0")
        self.assertEqual(json.loads((run / "report.json").read_bytes())["reason"], "time_limit")
        run = self.start("--max-output-bytes", "12000")
        report = json.loads((run / "report.json").read_bytes())
        self.assertEqual((report["state"], report["reason"]), ("interrupted", "output_limit"))
        self.assertTrue((run / "snapshot.json").exists() and (run / "evidence.jsonl").exists())
        self.assertEqual(self.fresh("report", run)[0], 0)
        with self.assertRaises(SystemExit):
            self.run_cli("start", "--source", str(self.source), "--output-dir", str(self.out),
                         "--max-seconds", "601")

    def test_waiting_stop_and_idempotent_terminal_stop(self):
        self.write(self.twelve_plus())
        seen = {}
        real_open, real_check = observer._open_source, observer._check_limits

        def opening(path):
            [run] = [p for p in self.out.iterdir()]
            seen["run"] = run
            seen["before_open"] = json.loads((run / "status.json").read_bytes())["state"]
            return real_open(path)

        def checking(run, started, max_seconds):
            if (run.path / "snapshot.json").exists() and "stopped" not in seen:
                seen["stopped"] = self.fresh("stop", run.path)  # real stop from a separate process
            return real_check(run, started, max_seconds)

        with mock.patch.object(observer, "_open_source", opening), \
                mock.patch.object(observer, "_check_limits", checking):
            run = self.start()
        self.assertEqual(seen["before_open"], "waiting")
        self.assertEqual(seen["stopped"][1]["stop"], "requested")
        report = json.loads((run / "report.json").read_bytes())
        self.assertEqual((report["state"], report["reason"]), ("interrupted", "stop_requested"))
        self.assertTrue(report["source"]["sha256"])
        self.assertEqual(self.fresh("stop", run)[1]["stop"], "already_terminal")
        self.assertEqual(self.fresh("stop", run)[1]["state"], "interrupted")

    def test_stale_run_stop_marks_interrupted(self):
        run = self.out / "stale"
        run.mkdir(parents=True)
        (run / "status.json").write_text(json.dumps({"state": "observing"}))
        self.assertEqual(self.fresh("stop", run)[1]["state"], "interrupted")

    def test_lineage_cap_is_three_runs(self):
        self.write([])
        for _ in range(3):
            self.start()
        code, _ = self.run_cli("start", "--source", str(self.source), "--output-dir", str(self.out))
        self.assertEqual(code, 2)
        self.assertEqual(len(list(self.out.iterdir())), 3)


if __name__ == "__main__":
    unittest.main()
