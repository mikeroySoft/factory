"""Tools-enabled variant: does the reviewer find the defect when it can read the repo?

The main run (`run_calibration.py`) answers the captured prompt with a no-tools process and
an inlined packet, which measures the contract text. Both arms missed both primary defects
there. This variant separates the two candidate explanations:

  (a) the contract does not ask for enough, or
  (b) the packet withheld evidence the defect needs.

So it runs the production path instead: the real reviewer argv, tools enabled, in a
throwaway review directory containing `review.diff` and a `snapshot` clone at the case
head. The clone's `refs/remotes/origin/main` is the case review base, so both the captured
policy and diff come from the same refs production uses. The reviewer can read any file
and the live issue itself. No packet envelope is appended.

Deliberate deviations from production, applied identically to every run: `--mode text`,
`--no-title`, `--max-time`, and `--auto-approve` (a print-mode approval prompt would
otherwise hang the run and confound the measurement). Each run reports whether the
reviewer mutated its clone.

usage: run_tools.py [--all] <out-dir> <samples> [contract ...]
  --all  score every frozen case, not just REVISE-oracle heads
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

from run_calibration import CASES as CORPUS
from run_calibration import checkout, configured_reviewer_model
from run_calibration import contract_commit as _contract_commit

from factory.dispatch import _omp_output

EXP = pathlib.Path(__file__).parent
REPO = str(EXP.parents[1])
CONTRACTS = {"baseline": "cadb9981524ab5506693e27f56ff8ac4ef911da6",
             "landed": "29d21d625bd92b9f3aa6d8ac0c8da1386233f284",
             # #237: the deployed contract at the time of the claim, and that contract plus
             # one appended cross-file tracing sentence in dispatch.review() (nothing else).
             "current": "4390f481ed6743694d35193049dc0242eb4c7784",
             "candidate": "70fea0e9cc314916acbe4494e4dc0d0dff21b361",
             "review-baseline": "eb611cc794da4008ffaba73cac6108a2ed8af0a1",
             "review-tally": "1bc93aa2abaf34607b88ee53428a22d5453f5c12",
             "review-current": "f8dc1f31324a39e2121b8cbbd783c6d74f8b6317",
             "review-convergence": "d4d07101a532e622653236b3ec6ae47643d18bc4"}


def _defect_cases() -> list[str]:
    """Heads whose oracle expects REVISE (primary defects)."""
    cases = []
    for case_dir in sorted((EXP / "cases").iterdir()):
        oracle = json.loads((case_dir / "oracle.json").read_text())
        if oracle.get("expected_verdict") == "REVISE":
            cases.append(case_dir.name)
    return cases


# Defect-bearing heads only by default (the §6 question); `--all` scores the whole frozen
# corpus so APPROVE cases feed `unnecessary_revise` for a promotion claim (#237).
CASES = CORPUS if "--all" in sys.argv else _defect_cases()
TIMEOUT = 900

def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def oracle(case: str) -> dict:
    return json.loads((EXP / "cases" / case / "oracle.json").read_text())


def capture(contract: str, case: str, source: pathlib.Path) -> str:
    with tempfile.TemporaryDirectory(prefix="cal-capture-") as scratch:
        out = pathlib.Path(scratch) / "prompt.txt"
        subprocess.run([sys.executable, str(EXP / "capture_contract.py"),
                        str(checkout(contract_commit(contract))), scratch, case.split("-")[0],
                        str(EXP / "cases" / case / "sources/gate-report.md"), str(out),
                        str(source), oracle(case)["head"]],
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


def review(contract: str, case: str, sample: int, model: str) -> dict:
    root = pathlib.Path(tempfile.mkdtemp(prefix=f"cal-tools-{case}-"))
    wt = root / "snapshot"
    try:
        worktree(case, wt)
        diff = subprocess.run(["git", "diff", "origin/main..HEAD"], cwd=wt, check=True,
                              capture_output=True, text=True).stdout
        (root / "review.diff").write_text(diff)
        prompt = capture(contract, case, wt)
        started = time.monotonic()
        proc = subprocess.run(["omp", "-p", "--mode", "json", "--no-session", "--no-title",
                               "--auto-approve", "--max-time", "600", "--model", model, prompt],
                              cwd=root, capture_output=True, text=True, timeout=TIMEOUT,
                              stdin=subprocess.DEVNULL, check=False)
        output, usage = _omp_output(proc.stdout)
        elapsed = round(time.monotonic() - started, 1)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=wt,
                               capture_output=True, text=True, check=False).stdout.strip()
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return {"contract": contract, "contract_commit": contract_commit(contract), "case": case,
            "sample": sample, "tools": True, "model": model, "usage": usage,
            "returncode": proc.returncode, "seconds": elapsed,
            "prompt_sha256": sha(prompt), "worktree_dirty": dirty[:2000],
            "output": output, "stderr": proc.stderr.strip()[-2000:]}


def contract_commit(contract: str) -> str:
    return _contract_commit(CONTRACTS.get(contract, contract))


def run_experiment(
    out_dir: pathlib.Path,
    samples: int,
    contracts: list[str],
    model: str,
) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps({
        "model": model, "samples": samples, "contracts": contracts, "splits_version": 2,
    }, indent=2) + "\n")
    jobs = [(c, case, s) for c in contracts for case in CASES for s in range(1, samples + 1)]
    results = []
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(lambda job: review(*job, model), jobs):
            results.append(result)
            (out_dir / f"{result['contract']}.{result['case']}.s{result['sample']}.md").write_text(
                result["output"] + "\n"
            )
            print(f"{result['contract']:9} {result['case']:10} s{result['sample']} "
                  f"rc={result['returncode']} {result['seconds']}s "
                  f"dirty={bool(result['worktree_dirty'])}", flush=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("out_dir")
    parser.add_argument("samples", type=int)
    parser.add_argument("contracts", nargs="*")
    args = parser.parse_args(argv)
    global CASES
    CASES = CORPUS if args.all else _defect_cases()
    model = args.model or configured_reviewer_model(pathlib.Path(REPO))
    rows = run_experiment(
        pathlib.Path(args.out_dir),
        args.samples,
        args.contracts or list(CONTRACTS),
        model,
    )
    return 0 if all(row["returncode"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
