"""Admission boundaries and journal receipts with real slot flocks and commands."""
from __future__ import annotations

import fcntl
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import config, gate, lifecycle


class MutationSlotOwnershipTest(unittest.TestCase):
    def test_only_pr_mutation_checks_record_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tier in ("attempt", "pr", "release"):
                for name, producer in (("mutation", False), ("zlint", False), ("renamed", True)):
                    with self.subTest(tier=tier, name=name):
                        marker = root / "executed"
                        marker.unlink(missing_ok=True)
                        command = [sys.executable, "-c",
                                   "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                                   str(marker), *(["scripts/mutation.py"] if producer else [])]
                        cfg = config.Config(root=root, repo="acme/widgets", mutation_slots=1,
                                            checks=[config.Check(name=name, run=command, when=tier)])
                        report = root / "report.md"
                        # Real CLI values are not necessarily interned Python literals.
                        cli_tier = tier.encode().decode()
                        with mock.patch.object(config, "load", return_value=cfg), \
                                mock.patch.object(gate, "MUTATION_SLOT_PREFIX", root / "slot"), \
                                mock.patch.object(gate, "toolchain", return_value={
                                    "rocm": "n/a", "kernel": "test", "amdgpu": "n/a"}):
                            code = gate.main(["--tier", cli_tier, "--protected-override",
                                              "--skip", "conflict-markers,leak-scan",
                                              "--report", str(report)])
                        self.assertEqual(code, 0, report.read_text())
                        self.assertTrue(marker.exists(), "configured check did not execute")
                        self.assertEqual("Mutation slot wait:" in report.read_text(),
                                         tier == "pr" and (name == "mutation" or producer))

    def test_wait_duration_and_single_paired_journal_wait(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "events.jsonl"
            attempts = 0
            with (root / "slot-0.lock").open("a") as holder:
                fcntl.flock(holder, fcntl.LOCK_EX)

                def retry(_seconds):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 3:
                        fcntl.flock(holder, fcntl.LOCK_UN)

                with lifecycle.scope(journal, "gate-check") as execution, \
                        mock.patch.object(gate, "MUTATION_SLOT_PREFIX", root / "slot"), \
                        mock.patch.object(gate.time, "sleep", side_effect=retry), \
                        mock.patch.object(gate.time, "monotonic", side_effect=[100.0, 220.0]), \
                        gate._mutation_slot(1, execution) as waited:
                    self.assertEqual(waited, 120.0, "report must receive elapsed admission seconds")
            rows = [json.loads(row) for row in journal.read_text().splitlines()]
            waits = [row for row in rows if row["kind"] == "wait"]
            self.assertEqual(len(waits), 1, "one contention episode must not emit repeated waits")
            self.assertEqual(waits[0]["wait"]["reason"], "mutation_slot")
            self.assertEqual(waits[0]["wait"]["details"], {"slots": 1})
            self.assertEqual([row["wait_event_id"] for row in rows if row["kind"] == "wait_end"],
                             [waits[0]["event_id"]])
            resources = [row for row in rows if row["kind"] in {"lock_acquired", "lock_released"}]
            self.assertEqual([row["kind"] for row in resources], ["lock_acquired", "lock_released"])
            self.assertTrue(all(row["blocking"] for row in resources))
            self.assertEqual(resources[0]["acquisition_id"], resources[1]["acquisition_id"])

    def test_failed_launch_releases_slot_and_restores_child_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with lifecycle.scope(root / "events.jsonl", "gate-check") as execution, \
                    mock.patch.object(gate, "MUTATION_SLOT_PREFIX", root / "slot"):
                with self.assertRaises(FileNotFoundError), gate._mutation_slot(1, execution):
                    gate.run([str(root / "missing-command")])
                # A stale inherited descriptor makes the next Popen fail with EBADF.
                passed, output = gate.run([sys.executable, "-c", "print('next check')"])
                self.assertTrue(passed, output)
                self.assertEqual(output.strip(), "next check")
                with (root / "slot-0.lock").open("a") as slot:
                    fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
