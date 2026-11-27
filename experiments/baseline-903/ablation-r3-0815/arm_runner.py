"""Run the unchanged 3b78a952 harness and also record which tests failed/errored (r3-0735).

usage: arm_runner.py <arm> <out-dir>   (cwd = the scratch tree at 69b50b7d)
Writes <out-dir>/result.json (harness) and <out-dir>/outcomes.json (harness fields + test ids).
"""
import json
import signal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_run

_captured = {}
_original_run = unittest.TextTestRunner.run


def _recording_run(self, test):
    result = _original_run(self, test)
    _captured["result"] = result
    return result


def _restore_default_signals() -> None:
    """A non-interactive shell's `cmd &` starts children with SIGINT/SIGQUIT ignored; the gate
    (launchd) starts them at their defaults. Restore those so signal tests see the gate's state."""
    if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    if signal.getsignal(signal.SIGQUIT) == signal.SIG_IGN:
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def main() -> int:
    _restore_default_signals()
    arm, out = sys.argv[1], Path(sys.argv[2])
    unittest.TextTestRunner.run = _recording_run
    sys.argv = ["ablation_run.py", arm, str(out)]
    rc = ablation_run.main()
    result = _captured["result"]
    data = json.loads((out / "result.json").read_text())
    data.update({
        "failure_ids": sorted(t.id() for t, _ in result.failures),
        "error_ids": sorted(t.id() for t, _ in result.errors),
        "skipped_ids": sorted(t.id() for t, _ in result.skipped),
        "tracebacks": {t.id(): tb[-4000:] for t, tb in [*result.failures, *result.errors]},
        "harness_rc": rc,
    })
    (out / "outcomes.json").write_text(json.dumps(data, indent=2) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
