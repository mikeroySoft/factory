"""Build one real reviewer prompt from a pinned factory checkout.

Imports factory.dispatch/factory.config from the checkout under test and calls the
production dispatch.review() with a capture argv as the configured reviewer, so the
prompt text is the contract's own, never a copy of it. Lifecycle/event writes land in
a scratch root, never in a live checkout.

usage: capture_contract.py <checkout> <scratch> <issue-number> <gate-report-file> <out-file> <source> <head>
"""
import inspect
import pathlib
import sys

checkout, scratch, number, gate_file, out, source, head = sys.argv[1:8]
sys.path.insert(0, checkout)
from factory import dispatch
from factory.config import Config

scratch = pathlib.Path(scratch)
capture = str(pathlib.Path(__file__).with_name("capture_prompt.py"))
dispatch.configure(Config(root=scratch, repo="mikeroySoft/factory", main="main",
                          reviewer=[sys.executable, capture, "{prompt}", out]))
gate_report = pathlib.Path(gate_file).read_text()
if "expected_head" in inspect.signature(dispatch.review).parameters:
    dispatch.review(pathlib.Path(source), int(number), gate_report, head)
else:
    dispatch.review(pathlib.Path(source), int(number), gate_report)
print(pathlib.Path(out).stat().st_size)
