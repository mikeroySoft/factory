"""Tools-enabled variant: does the reviewer find the defect when it can read the repo?

The main run (`run_calibration.py`) answers the captured prompt with a no-tools process and
an inlined packet, which measures the contract text. Both arms missed both primary defects
there. This variant separates the two candidate explanations:

  (a) the contract does not ask for enough, or
  (b) the packet withheld evidence the defect needs.

So it runs the production path instead: the real reviewer argv, tools enabled, inside a
throwaway clone checked out at the case head with `refs/remotes/origin/main` set to the
case's review base — so `git diff origin/main..HEAD` resolves exactly as it does in a
worker worktree, and the reviewer can read any file and the live issue itself. No packet
envelope is appended.

Deliberate deviations from production, applied identically to every run: `--mode text`,
`--no-title`, `--max-time`, and `--auto-approve` (a print-mode approval prompt would
otherwise hang the run and confound the measurement). Each run reports whether the
reviewer mutated its clone.

usage: run_tools.py <out-dir> <samples> [contract ...]
"""
import concurrent.futures as cf
import hashlib, json, pathlib, shutil, subprocess, sys, tempfile, time

from run_calibration import checkout

EXP = pathlib.Path(__file__).parent
REPO = "/home/mike/dev/mikeroysoft/factory"
CONTRACTS = {"baseline": "cadb9981524ab5506693e27f56ff8ac4ef911da6",
             "landed": "29d21d625bd92b9f3aa6d8ac0c8da1386233f284"}
CHECKOUT_ROOT = pathlib.Path("/tmp/cal-checkout")
CASES = ["12-before", "20-before"]                 # the defect-bearing heads
MODEL = "anthropic/claude-fable-5-1"               # the repo's configured reviewer model
TIMEOUT = 900


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def oracle(case: str) -> dict:
    return json.loads((EXP / "cases" / case / "oracle.json").read_text())


def capture(contract: str, case: str) -> str:
    with tempfile.TemporaryDirectory(prefix="cal-capture-") as scratch:
        out = pathlib.Path(scratch) / "prompt.txt"
        subprocess.run([sys.executable, str(EXP / "capture_contract.py"),
                        str(checkout(CONTRACTS[contract])), scratch, case.split("-")[0],
                        str(EXP / "cases" / case / "sources/gate-report.md"), str(out)],
                       check=True, capture_output=True, text=True, cwd=scratch)
        return out.read_text()


def worktree(case: str, dest: pathlib.Path) -> None:
    """A worker-shaped checkout: head under review, origin/main = the review base."""
    o = oracle(case)
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", REPO, str(dest)],
                   check=True, capture_output=True)
    for args in (["update-ref", "refs/remotes/origin/main", o["review_base"]],
                 ["checkout", "--quiet", "--detach", o["head"]]):
        subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True)


def review(contract: str, case: str, sample: int) -> dict:
    prompt = capture(contract, case)
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"cal-tools-{case}-"))
    wt = root / "repo"
    try:
        worktree(case, wt)
        started = time.monotonic()
        proc = subprocess.run(["omp", "-p", "--mode", "text", "--no-session", "--no-title",
                               "--auto-approve", "--max-time", "600", "--model", MODEL, prompt],
                              cwd=wt, capture_output=True, text=True, timeout=TIMEOUT,
                              stdin=subprocess.DEVNULL)
        elapsed = round(time.monotonic() - started, 1)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=wt,
                               capture_output=True, text=True).stdout.strip()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return {"contract": contract, "contract_commit": CONTRACTS[contract], "case": case,
            "sample": sample, "tools": True, "returncode": proc.returncode, "seconds": elapsed,
            "prompt_sha256": sha(prompt), "worktree_dirty": dirty[:2000],
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
            print(f"{r['contract']:9} {r['case']:10} s{r['sample']} rc={r['returncode']} "
                  f"{r['seconds']}s dirty={bool(r['worktree_dirty'])}", flush=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if all(r["returncode"] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
