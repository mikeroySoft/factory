"""Bounded reviewer calibration run.

For each (contract, case, sample): capture the reviewer prompt from the pinned checkout
through the production dispatch.review() code path, append the frozen packet envelope,
and answer it with one bounded, no-tools model process. Records prompt/packet SHA256 and
the raw reviewer output. Adjudication against cases/<id>/oracle.json is done separately.

usage: run_calibration.py <out-dir> <samples> [contract ...]
"""
import concurrent.futures as cf
import hashlib, json, pathlib, subprocess, sys, tempfile, threading, time

EXP = pathlib.Path(__file__).parent
REPO = "/home/mike/dev/mikeroysoft/factory"
CHECKOUT_ROOT = pathlib.Path("/tmp/cal-checkout")
CONTRACTS = {"baseline": "cadb9981524ab5506693e27f56ff8ac4ef911da6",
             "issue36": "1947fc4e3de23e71f64342f2c9cd2570d845db5e"}
CASES = ["12-before", "12-after", "20-before", "20-after"]
MODEL = "anthropic/claude-fable-5-1"          # the repo's configured reviewer model
FLAGS = ["-p", "--mode", "text", "--no-session", "--no-tools", "--no-extensions",
         "--no-skills", "--no-rules", "--no-lsp", "--no-pty", "--no-title",
         "--thinking", "low", "--hide-thinking", "--max-time", "300"]
TIMEOUT = 420
CHECKOUT_LOCK = threading.Lock()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def checkout(commit: str) -> pathlib.Path:
    """A pinned, content-addressed clone of the contract under test.

    Provisioned on demand and keyed by commit, so a reaped /tmp or a moved branch
    cannot silently change which contract a run measured. Serialized and idempotent:
    concurrent runs share one clone per commit, and a clone already at that commit is
    never checked out again.
    """
    dest = CHECKOUT_ROOT / commit[:12]
    with CHECKOUT_LOCK:
        if not (dest / ".git").exists():
            subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", REPO, str(dest)],
                           check=True, capture_output=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest,
                              capture_output=True, text=True).stdout.strip()
        if head != commit:
            subprocess.run(["git", "checkout", "--quiet", "--detach", commit], cwd=dest,
                           check=True, capture_output=True)
    return dest


def capture(contract: str, case: str) -> str:
    """The contract's own prompt text, built by its own dispatch.review()."""
    with tempfile.TemporaryDirectory(prefix="cal-capture-") as scratch:
        out = pathlib.Path(scratch) / "prompt.txt"
        subprocess.run([sys.executable, str(EXP / "capture_contract.py"),
                        str(checkout(CONTRACTS[contract])),
                        scratch, case.split("-")[0],
                        str(EXP / "cases" / case / "sources/gate-report.md"), str(out)],
                       check=True, capture_output=True, text=True, cwd=scratch)
        return out.read_text()


def review(contract: str, case: str, sample: int) -> dict:
    prompt = capture(contract, case)
    packet = (EXP / "cases" / case / "packet.md").read_text()
    full = prompt + "\n\n" + packet
    with tempfile.TemporaryDirectory(prefix="cal-review-") as d:
        path = pathlib.Path(d) / "review.md"
        path.write_text(full)
        started = time.monotonic()
        proc = subprocess.run(["omp", *FLAGS, "--model", MODEL, "@" + str(path)],
                              cwd=d, capture_output=True, text=True, timeout=TIMEOUT,
                              stdin=subprocess.DEVNULL)
        elapsed = round(time.monotonic() - started, 1)
    return {"contract": contract, "contract_commit": CONTRACTS[contract], "case": case,
            "sample": sample, "returncode": proc.returncode, "seconds": elapsed,
            "prompt_sha256": sha(prompt), "packet_sha256": sha(packet), "input_sha256": sha(full),
            "output": proc.stdout.strip(), "stderr": proc.stderr.strip()[-2000:]}


def main() -> int:
    out_dir = pathlib.Path(sys.argv[1]); out_dir.mkdir(parents=True, exist_ok=True)
    samples = int(sys.argv[2])
    contracts = sys.argv[3:] or list(CONTRACTS)
    jobs = [(c, case, s) for c in contracts for case in CASES for s in range(1, samples + 1)]
    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for r in pool.map(lambda j: review(*j), jobs):
            results.append(r)
            (out_dir / f"{r['contract']}.{r['case']}.s{r['sample']}.md").write_text(r["output"] + "\n")
            print(f"{r['contract']:9} {r['case']:10} s{r['sample']} rc={r['returncode']} {r['seconds']}s", flush=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(r["returncode"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
