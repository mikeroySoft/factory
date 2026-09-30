import gzip
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


if __name__ == "__main__":
    unittest.main()
