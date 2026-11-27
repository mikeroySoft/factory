"""Published portability evidence stays private, compact, and internally consistent."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unittest


EXPERIMENTS = Path(__file__).resolve().parents[1] / "experiments"
EVIDENCE = EXPERIMENTS / "macos-portability-572"
FORBIDDEN = re.compile(rb"/(?:Users|home)/|/private/tmp/|/var/folders/")
# Existing out-of-scope fixtures are pinned, not blanket-exempted. New paths fail.
LEGACY_PATH_LINES = {
    "b1-build/case.py": "20716daf5633c57b0c0e3ea41d38494c296ef31e950f2e1a1164b8b3e6c72fa1",
    "b1-build/sandbox.py": "e36bd99b250aaa5977f51fd3ca17ddece7904832d66b48f1faae6910f3f22e1e",
    "reviewer-calibration/cases/62-after/packet.md": "52419c794219240df7ae4e1fdd4b0df1f840e570c5b44cb40cbe29ec4d43f1dc",
    "reviewer-calibration/cases/62-after/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/62-after/sources/issue.md": "67925f47fd3e6573d3a5cb3220e79c554a0aa30f6cf9a77c6b2b8739f303de97",
    "reviewer-calibration/cases/62-before/packet.md": "52419c794219240df7ae4e1fdd4b0df1f840e570c5b44cb40cbe29ec4d43f1dc",
    "reviewer-calibration/cases/62-before/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/62-before/sources/issue.md": "67925f47fd3e6573d3a5cb3220e79c554a0aa30f6cf9a77c6b2b8739f303de97",
    "reviewer-calibration/cases/79-after/packet.md": "5fe214b0f73f4ce7a1898463af3bf7c9837220f5900ca51d685e8982cf1da78a",
    "reviewer-calibration/cases/79-after/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/79-after/sources/issue.md": "4e2ddc92ddad514af29a633eeeb878425a28f687899c155302f115e47970577e",
    "reviewer-calibration/cases/79-before/packet.md": "5fe214b0f73f4ce7a1898463af3bf7c9837220f5900ca51d685e8982cf1da78a",
    "reviewer-calibration/cases/79-before/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/79-before/sources/issue.md": "4e2ddc92ddad514af29a633eeeb878425a28f687899c155302f115e47970577e",
    "reviewer-calibration/cases/8-approve/packet.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/8-approve/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/87-approve/packet.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/87-approve/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/9-approve/packet.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/cases/9-approve/sources/README.md": "73d6a21f04aaac233abaf68a937606bc822a573bb966a47ef247f414e5cbb07a",
    "reviewer-calibration/runs/issue36-repeatability/README.md": "44603bc88cb643c150a65e312fbbe64da66e93ad32127f96787a1ac4f1b2a473",
    "reviewer-calibration/runs/issue36-repeatability/config.json": "de2a5737ed2546ca821e33e0dacb1bc3f66cfd157bc1b1942db07badfd3aee8f"
}


class MacosPortabilityEvidenceTest(unittest.TestCase):
    def test_experiments_have_no_new_private_paths(self):
        for path in EXPERIMENTS.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            lines = [line for line in path.read_bytes().split(b"\n") if FORBIDDEN.search(line)]
            if lines:
                relative = path.relative_to(EXPERIMENTS).as_posix()
                with self.subTest(path=relative):
                    self.assertEqual(
                        hashlib.sha256(b"\n".join(lines)).hexdigest(),
                        LEGACY_PATH_LINES.get(relative),
                        "Absolute host paths in published experiment data",
                    )

    def test_portability_evidence_schema_and_report(self):
        before, after, metadata, failures = [
            json.loads((EVIDENCE / name).read_text())
            for name in ("before.json", "after.json", "metadata.json", "failures.json")
        ]
        report = (EVIDENCE / "REPORT.md").read_text()
        log = (EVIDENCE / "suite.log").read_bytes()
        digest = hashlib.sha256(log).hexdigest()
        self.assertEqual(metadata["suite_log_sha256"], digest)
        self.assertIn(digest, report)
        self.assertIn(f"**{len(log)} bytes**", report)
        self.assertEqual(failures["source_sha"], metadata["sha"]["stdout"].strip())
        self.assertIn(failures["source_sha"], report)
        self.assertEqual(metadata["suite_exit"], 1)
        self.assertEqual(metadata["checkout_status"]["stdout"], "")
        self.assertEqual(metadata["command"][-4:], ["unittest", "discover", "-s", "tests"])
        self.assertTrue(metadata["scrub"]["tool"])
        self.assertRegex(metadata["scrub"]["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(metadata["scrub"]["rules"])
        self.assertLess(before["at"], after["at"])
        self.assertEqual(set(before), {"at", *metadata["unchanged"]})
        for key, unchanged in metadata["unchanged"].items():
            with self.subTest(group=key):
                self.assertTrue(unchanged)
                self.assertEqual(before[key], after[key])
        self.assertEqual(before["launchctl_factory"]["launchctl_exit"], 0)
        self.assertTrue(before["launchctl_factory"]["rows"])
        for state in before["protected_files"].values():
            self.assertRegex(state["sha256"], r"^[a-f0-9]{64}$")
            self.assertTrue(state["resolved"].startswith("<home>/"))
            self.assertRegex(state["mode"], r"^0o[0-7]+$")
            self.assertIsInstance(state["mtime_ns"], int)
        engine = before["engine_files"]
        self.assertIn("bin/factory", engine)
        for path, sha in engine.items():
            self.assertRegex(path, r"^(bin/factory|lib/python[^/]+/site-packages/(factory/.*|factory-[^/]+\.dist-info/.*))$")
            self.assertRegex(sha, r"^[a-f0-9]{64}$")
        self.assertTrue(any("/factory/" in path for path in engine))
        for path, provenance in before["engine_provenance"].items():
            relative = path.split("/tools/factory/", 1)[1]
            self.assertIn(relative, engine)
            self.assertIn(str(Path(relative).with_name("RECORD")), engine)
            self.assertIn(provenance["vcs_info"]["commit_id"], report)
        self.assertIn(f"**{len(engine)}** retained engine-file", report)
        for key in ("benito_head", "benito_status", "benito_diff"):
            self.assertEqual(before[key]["exit"], 0)
            self.assertEqual(before[key]["stderr"], "")
        self.assertIn(before["benito_head"]["stdout"].strip(), report)
        self.assertEqual(before["benito_diff"]["stdout"], "")

        outcomes = failures["outcomes"]
        counts = Counter(row["outcome"] for row in outcomes)
        failed = {row["id"] for row in outcomes if row["outcome"] == "FAIL"}
        errored = {row["id"] for row in outcomes if row["outcome"] == "ERROR"}
        self.assertEqual(counts, {"FAIL": failures["failure_reports"], "ERROR": failures["error_reports"]})
        self.assertEqual(len(failed), failures["failed_methods"])
        self.assertEqual(len(errored), failures["errored_methods"])
        self.assertEqual(len(failed & errored), failures["methods_with_both_failure_and_error"])
        self.assertEqual(len(failed | errored), failures["unsuccessful_methods"])
        self.assertEqual(failures["outer_tests"], failures["passed_methods"] + len(failed | errored) + failures["skipped"])
        self.assertTrue(failures["nested_outcomes_excluded"])
        self.assertIn(f"**{failures['passed_methods']}**", report)
        self.assertIn(f"**{failures['unsuccessful_methods']}**", report)
        self.assertIn(f"**{failures['skipped']}**", report)
        self.assertIn(f"Ran {failures['outer_tests']} tests", log.decode())
        self.assertTrue(log.decode().rstrip().endswith(
            f"FAILED (failures={counts['FAIL']}, errors={counts['ERROR']}, skipped={failures['skipped']})"
        ))
        log_lines = log.decode().splitlines()
        for row in outcomes:
            self.assertEqual(log_lines[row["line"] - 1], f"{row['outcome']}: {row['name']}")
            self.assertTrue(row["traceback"])
            self.assertIn(row["traceback"], log.decode())
            self.assertRegex(row["ticket"], r"^https://github.com/mikeroySoft/factory/issues/\d+$")
        areas = sorted({row["area"] for row in outcomes})
        for area in areas:
            counts = Counter(row["outcome"] for row in outcomes if row["area"] == area)
            self.assertIn(f"| {area} | {counts['FAIL']} | {counts['ERROR']} |", report)


if __name__ == "__main__":
    unittest.main()
