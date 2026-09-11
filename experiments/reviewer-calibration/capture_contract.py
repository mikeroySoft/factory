"""Build one real reviewer prompt from a pinned factory checkout.

Imports factory.dispatch/factory.config from the checkout under test and calls the
production dispatch.review() with a capture argv as the configured reviewer, so the
prompt text is the contract's own, never a copy of it. Lifecycle/event writes land in
a scratch root, never in a live checkout.

usage: capture_contract.py <checkout> <scratch> <issue-number> <gate-report-file> <out-file>
"""
import inspect, pathlib, subprocess, sys

checkout, scratch, number, gate_file, out = sys.argv[1:6]
sys.path.insert(0, checkout)
from factory import dispatch                      # noqa: E402
from factory.config import Config                 # noqa: E402

scratch = pathlib.Path(scratch)
capture = str(pathlib.Path(__file__).with_name("capture_prompt.py"))
dispatch.configure(Config(root=scratch, repo="mikeroySoft/factory", main="main",
                          reviewer=[sys.executable, capture, "{prompt}", out]))
gate_report = pathlib.Path(gate_file).read_text()
if "expected_head" in inspect.signature(dispatch.review).parameters:
    subprocess.run(["git", "init", "--quiet"], cwd=scratch, check=True,
                   capture_output=True)
    subprocess.run([
        "git", "-c", "user.name=Reviewer Calibration",
        "-c", "user.email=reviewer-calibration@example.invalid",
        "-c", "commit.gpgSign=false", "commit", "--allow-empty", "--quiet",
        "-m", "review capture",
    ], cwd=scratch, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=scratch, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    dispatch.review(scratch, int(number), gate_report, head)
else:
    dispatch.review(scratch, int(number), gate_report)
print(pathlib.Path(out).stat().st_size)
