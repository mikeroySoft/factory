"""Build one real reviewer prompt from a pinned factory checkout.

Imports factory.dispatch/factory.config from the checkout under test and calls the
production dispatch.review() with a capture argv as the configured reviewer, so the
prompt text is the contract's own, never a copy of it. Lifecycle/event writes land in
a scratch root, never in a live checkout.

usage: capture_contract.py <checkout> <scratch> <issue-number> <gate-report-file> <out-file>
"""
import pathlib, sys

checkout, scratch, number, gate_file, out = sys.argv[1:6]
sys.path.insert(0, checkout)
from factory import dispatch                      # noqa: E402
from factory.config import Config                 # noqa: E402

scratch = pathlib.Path(scratch)
capture = str(pathlib.Path(__file__).with_name("capture_prompt.py"))
dispatch.configure(Config(root=scratch, repo="mikeroySoft/factory", main="main",
                          reviewer=[sys.executable, capture, "{prompt}", out]))
dispatch.review(scratch, int(number), pathlib.Path(gate_file).read_text())
print(pathlib.Path(out).stat().st_size)
