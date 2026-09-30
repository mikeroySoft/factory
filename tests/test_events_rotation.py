import fcntl
import gzip
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from factory import lifecycle


class RotationTest(unittest.TestCase):
    def setUp(self):
        # An inherited dispatcher context would redirect Execution rows to the real journal.
        context = mock.patch.dict(os.environ, {lifecycle.CONTEXT_ENV: ""})
        context.start()
        self.addCleanup(context.stop)

    def test_rotation_keeps_record_rows_and_open_executions(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(lifecycle, "MAX_BYTES", 20_000), \
                mock.patch.object(lifecycle, "RETENTION", 2):
            path = Path(tmp) / "events.jsonl"
            lock = Path(tmp) / "held.lock"
            open_exec = lifecycle.Execution(path, "work", ticket=1)
            open_exec.emit("enter")
            open_exec.emit("lock_acquired", lock=str(lock))
            records = []
            for i in range(400):
                row = {"event": "record", "n": i, "pad": "x" * 40}
                records.append(row)
                lifecycle.append(path, row)
                closed = lifecycle.Execution(path, "check", ticket=i)
                closed.emit("enter")
                closed.emit("exit")

            self.assertLessEqual(path.stat().st_size, lifecycle.MAX_BYTES)
            segments = sorted(Path(tmp).glob("events.jsonl.*.gz"))
            self.assertTrue(segments)
            self.assertLessEqual(len(segments), 2)
            for segment in segments:
                with gzip.open(segment, "rb") as handle:
                    handle.read()
            got = [row for row in lifecycle.read_events(path) if row.get("event") == "record"]
            self.assertEqual(got, records)
            (active,) = [e for e in lifecycle.observe(path) if e["execution_id"] == open_exec.execution_id]
            self.assertEqual(active["state"], "active")
            self.assertEqual(active["events"][-1]["locks"][0]["path"], str(lock.absolute()))

    def test_row_appended_during_rotation_is_intact(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(lifecycle, "MAX_BYTES", 2_000):
            path = Path(tmp) / "events.jsonl"
            real = lifecycle._rotate
            started, release = threading.Event(), threading.Event()

            def slow(*args):
                started.set()
                release.wait(5)
                real(*args)

            with mock.patch.object(lifecycle, "_rotate", slow):
                writer = threading.Thread(
                    target=lambda: lifecycle.append(path, {"event": "record", "pad": "y" * 3000}))
                writer.start()
                started.wait(5)
            late = threading.Thread(target=lambda: lifecycle.append(path, {"event": "late"}))
            late.start()
            release.set()
            writer.join()
            late.join()
            rows = lifecycle.read_events(path)
            self.assertEqual([r["event"] for r in rows], ["record", "late"])
            self.assertIn(b'{"event":"late"}\n', path.read_bytes())

    def test_readers_include_segments_beyond_current_retention(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(lifecycle, "RETENTION", 4):
            path = Path(tmp) / "events.jsonl"
            lifecycle.append(path, {"event": "record", "n": 0})
            for n in range(1, 11):
                with gzip.open(Path(tmp) / f"events.jsonl.{n}.gz", "wb") as segment:
                    segment.write(json.dumps({"event": "record", "n": n}).encode() + b"\n")
            expected = list(range(10, 0, -1)) + [0]
            self.assertEqual([row["n"] for row in lifecycle.read_events(path)], expected)
            with lifecycle.journal_snapshot(path, create=False) as (rows, _):
                self.assertEqual([row["n"] for row in rows], expected)

    def test_sparse_segments_survive_retention_change(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(lifecycle, "MAX_BYTES", 200), \
                mock.patch.object(lifecycle, "RETENTION", 4):
            path = Path(tmp) / "events.jsonl"
            for n in (1, 9):
                with gzip.open(Path(tmp) / f"events.jsonl.{n}.gz", "wb") as segment:
                    segment.write(json.dumps({"event": "record", "n": n}).encode() + b"\n")
            lifecycle.append(path, {"event": "record", "n": 0, "pad": "x" * 300})
            self.assertEqual([row["n"] for row in lifecycle.read_events(path)], [9, 1, 0])
            self.assertEqual(sorted(p.name for p in Path(tmp).glob("events.jsonl.*.gz")),
                             ["events.jsonl.1.gz", "events.jsonl.2.gz", "events.jsonl.4.gz"])

    def test_lowering_retention_preserves_all_audit_rows(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(lifecycle, "MAX_BYTES", 2_000), \
                mock.patch.object(lifecycle, "RETENTION", 8):
            path = Path(tmp) / "events.jsonl"
            lock = Path(tmp) / "held.lock"
            lock.touch()
            with lock.open("rb") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                execution = lifecycle.Execution(path, "worker", ticket=7)
                execution.emit("enter")
                execution.emit("lock_acquired", lock=str(lock))
                records = []
                for n in range(55):
                    row = {"event": "record", "n": n, "pad": "x" * 550}
                    records.append(row)
                    lifecycle.append(path, row)
                    with lifecycle.scope(path, "check", ticket=n):
                        pass
                self.assertEqual(len(list(Path(tmp).glob("events.jsonl.*.gz"))), 8)
                with mock.patch.object(lifecycle, "RETENTION", 4):
                    self.assertEqual(
                        [row for row in lifecycle.read_events(path) if row.get("event") == "record"],
                        records,
                    )
                    for n in range(55, 60):
                        row = {"event": "record", "n": n, "pad": "y" * 550}
                        records.append(row)
                        lifecycle.append(path, row)
                    segments = list(Path(tmp).glob("events.jsonl.*.gz"))
                    self.assertEqual(len(segments), 4)
                    self.assertEqual(
                        [row for row in lifecycle.read_events(path) if row.get("event") == "record"],
                        records,
                    )
                    for segment in segments:
                        with gzip.open(segment, "rb") as archive:
                            self.assertTrue(archive.read())
                    (state,) = [entry for entry in lifecycle.observe(path)
                                if entry["execution_id"] == execution.execution_id]
                    self.assertEqual(state["state"], "active")
                    self.assertEqual(state["evidence"]["locks"][0]["state"], "held")


if __name__ == "__main__":
    unittest.main()
