"""Bounded reviewer calibration run and cross-model replay.

Each run captures the production reviewer prompt, appends the frozen packet, and
answers it with one bounded, no-tools model process. ``--models`` compares three
repeats per model; grading uses the repeat runner added by issue #429.

usage: run_calibration.py [out-dir] [samples] [contract ...] [--model MODEL]
       run_calibration.py [out-dir] --models MODEL,MODEL [contract ...]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import pathlib
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from factory import config, stats
from factory.dispatch import _omp_output

EXP = pathlib.Path(__file__).parent
REPO = EXP.parents[1]
CHECKOUT_ROOT = pathlib.Path("/tmp/cal-checkout")
CONTRACTS = {
    "baseline": "cadb9981524ab5506693e27f56ff8ac4ef911da6",
    "issue36": "1947fc4e3de23e71f64342f2c9cd2570d845db5e",
}
SPLITS_VERSION = 2
REPEATS = 3
NOISE_MARGIN = 0.0833
FLAGS = [
    "-p", "--mode", "json", "--no-session", "--no-tools", "--no-extensions",
    "--no-skills", "--no-rules", "--no-lsp", "--no-pty", "--no-title",
    "--thinking", "low", "--hide-thinking", "--max-time", "300",
]
TIMEOUT = 420
CHECKOUT_LOCK = threading.Lock()


def load_splits(path: pathlib.Path = EXP / "splits.json") -> dict:
    value = json.loads(path.read_text())
    if value.get("version") != SPLITS_VERSION:
        raise ValueError(
            f"splits.json version {SPLITS_VERSION} required; found {value.get('version')!r}"
        )
    splits = value.get("splits")
    if not isinstance(splits, dict):
        raise TypeError("splits.json needs a splits object")
    return splits


def _cases_from_splits(splits: dict | None = None) -> list[str]:
    splits = splits or load_splits()
    seen, out = set(), []
    for name in ("train", "selection", "lockbox", "fresh"):
        for case in splits.get(name, []):
            if case not in seen:
                seen.add(case)
                out.append(case)
    return out


CASES = _cases_from_splits()


def configured_reviewer_model(root: pathlib.Path = REPO) -> str:
    model = config.command_model(config.load(root, worktree=True).reviewer)
    if not model:
        raise ValueError("configured reviewer command has no explicit --model")
    return model


def contract_commit(contract: str) -> str:
    commit = CONTRACTS.get(contract, contract)
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise ValueError(f"unknown calibration contract {contract!r}")
    return commit


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def checkout(commit: str) -> pathlib.Path:
    """Return a pinned, content-addressed clone of the contract under test."""
    dest = CHECKOUT_ROOT / commit[:12]
    with CHECKOUT_LOCK:
        if not (dest / ".git").exists():
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", "--no-checkout", str(REPO), str(dest)],
                check=True,
                capture_output=True,
            )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if head != commit or not (dest / "factory" / "dispatch.py").is_file():
            subprocess.run(
                ["git", "checkout", "--quiet", "--force", "--detach", commit],
                cwd=dest,
                check=True,
                capture_output=True,
            )
    return dest


def capture(contract: str, case: str) -> str:
    """Build the contract's own prompt through its own dispatch.review()."""
    with tempfile.TemporaryDirectory(prefix="cal-capture-") as scratch:
        scratch_path = pathlib.Path(scratch)
        out, source = scratch_path / "prompt.txt", scratch_path / "source"
        oracle = json.loads((EXP / "cases" / case / "oracle.json").read_text())
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", "--no-checkout", str(REPO), str(source)],
            check=True,
            capture_output=True,
        )
        for args in (
            ["update-ref", "refs/remotes/origin/main", oracle["review_base"]],
            ["checkout", "--quiet", "--detach", oracle["head"]],
        ):
            subprocess.run(["git", *args], cwd=source, check=True, capture_output=True)
        commit = contract_commit(contract)
        subprocess.run(
            [
                sys.executable,
                str(EXP / "capture_contract.py"),
                str(checkout(commit)),
                scratch,
                case.split("-")[0],
                str(EXP / "cases" / case / "sources/gate-report.md"),
                str(out),
                str(source),
                oracle["head"],
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=scratch,
        )
        return out.read_text()


def review(contract: str, case: str, sample: int, model: str) -> dict:
    prompt = capture(contract, case)
    packet = (EXP / "cases" / case / "packet.md").read_text()
    full = prompt + "\n\n" + packet
    with tempfile.TemporaryDirectory(prefix="cal-review-") as directory:
        path = pathlib.Path(directory) / "review.md"
        path.write_text(full)
        started = time.monotonic()
        proc = subprocess.run(
            ["omp", *FLAGS, "--model", model, "@" + str(path)],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        elapsed = round(time.monotonic() - started, 1)
    output, usage = _omp_output(proc.stdout)
    return {
        "contract_commit": contract_commit(contract),
        "returncode": proc.returncode,
        "seconds": elapsed,
        "prompt_sha256": sha(prompt),
        "packet_sha256": sha(packet),
        "input_sha256": sha(full),
        "usage": usage,
        "output": output,
        "stderr": proc.stderr.strip()[-2000:],
    }


Client = Callable[[str, str, int, str], dict]


def run_experiment(
    out_dir: pathlib.Path,
    samples: int,
    contracts: list[str],
    model: str,
    client: Client = review,
) -> list[dict]:
    splits = load_splits()
    cases = _cases_from_splits(splits)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps({
        "model": model,
        "samples": samples,
        "contracts": contracts,
        "splits_version": SPLITS_VERSION,
    }, indent=2) + "\n")
    jobs = [
        (contract, case, sample)
        for contract in contracts
        for case in cases
        for sample in range(1, samples + 1)
    ]
    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        calls = pool.map(lambda job: client(*job, model), jobs)
        for (contract, case, sample), result in zip(jobs, calls, strict=True):
            row = {
                **result,
                "contract": contract,
                "contract_commit": result.get("contract_commit", contract_commit(contract)),
                "case": case,
                "sample": sample,
                "model": model,
            }
            results.append(row)
            (out_dir / f"{contract}.{case}.s{sample}.md").write_text(
                str(row.get("output", "")).strip() + "\n"
            )
            print(
                f"{contract:9} {case:10} s{sample} rc={row.get('returncode')} "
                f"{row.get('seconds', 0)}s",
                flush=True,
            )
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def verdict(
    *,
    train_mean: float,
    train_pstdev: float,
    lockbox_mean: float,
    lockbox_pstdev: float,
    baseline_train_mean: float,
    baseline_lockbox_mean: float,
    stable_flips: int,
    margin: float = NOISE_MARGIN,
) -> str:
    if train_pstdev > margin or lockbox_pstdev > margin:
        return "inconclusive"
    if (
        train_mean + margin < baseline_train_mean
        or lockbox_mean < baseline_lockbox_mean
        or stable_flips
    ):
        return "worse"
    return "viable"


def _split_quality(rows: list[dict], cases: list[str]) -> dict:
    values = []
    for sample in range(1, REPEATS + 1):
        passed = [
            row.get("passed")
            for row in rows
            if row.get("sample") == sample and row.get("case") in cases
        ]
        if len(passed) != len(cases) or any(type(value) is not bool for value in passed):
            raise ValueError(f"repeat {sample} is missing graded cases")
        values.append(sum(passed) / len(passed))
    return {
        "mean": round(statistics.mean(values), 4),
        "pstdev": round(statistics.pstdev(values), 4),
    }


def _statuses(rows: list[dict], cases: list[str]) -> dict[str, str]:
    statuses = {}
    for case in cases:
        passed = [row.get("passed") for row in rows if row.get("case") == case]
        if len(passed) != REPEATS or any(type(value) is not bool for value in passed):
            raise ValueError(f"{case} needs exactly {REPEATS} graded repeats")
        statuses[case] = (
            "always-pass" if all(passed)
            else "always-fail" if not any(passed)
            else "flaky"
        )
    return statuses


def _usage_total(rows: list[dict], key: str) -> int | None:
    values = [
        row.get("usage", {}).get(key)
        for row in rows
        if isinstance(row.get("usage"), dict)
    ]
    return sum(values) if len(values) == len(rows) and all(type(value) is int for value in values) else None


def _report_markdown(report: dict) -> str:
    lines = [
        "# Reviewer model replay",
        "",
        f"- Frozen split: `splits.json` version {report['splits_version']}",
        f"- Repeats per case: {REPEATS}",
        "",
        "| Model | Train mean ± pstdev | Lockbox mean ± pstdev | Stable pass→fail | Prompt tokens | Completion tokens | Cost | Wall seconds | Verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["models"]:
        cost = "unpriced" if row["unpriced"] else f"${row['cost']:.6f}"
        lines.append(
            f"| `{row['model']}` | {row['train']['mean']:.4f} ± {row['train']['pstdev']:.4f} "
            f"| {row['lockbox']['mean']:.4f} ± {row['lockbox']['pstdev']:.4f} "
            f"| {len(row['stable_flips'])} | {row['prompt_tokens']} "
            f"| {row['completion_tokens']} | {cost} | {row['wall_seconds']:.3f} "
            f"| {row['verdict']} |"
        )
    return "\n".join(lines) + "\n"


def replay_models(
    models: list[str],
    out_dir: pathlib.Path,
    runner: Callable[[str, pathlib.Path], list[dict]],
    prices: dict[str, dict],
    splits_path: pathlib.Path = EXP / "splits.json",
) -> dict:
    splits = load_splits(splits_path)
    if not models or any(not model.strip() for model in models) or len(set(models)) != len(models):
        raise ValueError("--models needs unique, non-empty model names")
    cases = _cases_from_splits(splits)
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index, model in enumerate(models, 1):
        started = time.monotonic()
        rows = runner(model, out_dir / f"model-{index}")
        elapsed = round(time.monotonic() - started, 3)
        usages = [row.get("usage") for row in rows if isinstance(row.get("usage"), dict)]
        costs = [stats.priced(usage, prices) for usage in usages]
        summaries.append({
            "model": model,
            "train": _split_quality(rows, splits["train"]),
            "lockbox": _split_quality(rows, splits["lockbox"]),
            "statuses": _statuses(rows, cases),
            "prompt_tokens": _usage_total(rows, "prompt_tokens"),
            "completion_tokens": _usage_total(rows, "completion_tokens"),
            "cost": (
                round(sum(costs), 6)
                if len(costs) == len(rows) and all(cost is not None for cost in costs)
                else None
            ),
            "unpriced": not (
                len(costs) == len(rows) and all(cost is not None for cost in costs)
            ),
            "wall_seconds": elapsed,
        })
    baseline = summaries[0]
    output = []
    for summary in summaries:
        flips = [
            case
            for case in cases
            if baseline["statuses"][case] == "always-pass"
            and summary["statuses"][case] == "always-fail"
        ]
        output.append({
            key: value for key, value in summary.items() if key != "statuses"
        } | {
            "stable_flips": flips,
            "verdict": verdict(
                train_mean=summary["train"]["mean"],
                train_pstdev=summary["train"]["pstdev"],
                lockbox_mean=summary["lockbox"]["mean"],
                lockbox_pstdev=summary["lockbox"]["pstdev"],
                baseline_train_mean=baseline["train"]["mean"],
                baseline_lockbox_mean=baseline["lockbox"]["mean"],
                stable_flips=len(flips),
            ),
        })
    report = {"splits_version": SPLITS_VERSION, "repeats": REPEATS, "models": output}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "report.md").write_text(_report_markdown(report))
    return report


def _grade_rows(rows: list[dict]) -> list[dict]:
    try:
        from factory import reviewer_eval
    except ImportError as exc:
        raise RuntimeError("--models requires the repeat-3 runner from issue #429") from exc
    cfg = config.load(REPO, worktree=True)
    graded = []
    for row in rows:
        oracle = json.loads((EXP / "cases" / row["case"] / "oracle.json").read_text())
        if row.get("returncode") != 0 or reviewer_eval.parsed_verdict(row.get("output")) is None:
            covered = None
        else:
            covered, _judge = reviewer_eval._judge(
                cfg.second_reviewer, row["output"], oracle, REPO, TIMEOUT
            )
        graded.append(reviewer_eval.grade_output(row, oracle, covered))
    return graded


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", nargs="?")
    parser.add_argument("samples", nargs="?", type=int, default=REPEATS)
    parser.add_argument("contracts", nargs="*")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--model")
    group.add_argument("--models")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    matrix_runner: Callable[[str, pathlib.Path], list[dict]] | None = None,
    prices: dict[str, dict] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    load_splits()
    if args.models:
        if args.samples != REPEATS:
            raise ValueError(f"--models requires exactly {REPEATS} samples")
        models = [model.strip() for model in args.models.split(",")]
        stamp = datetime.now(UTC).date()
        out_dir = pathlib.Path(args.out_dir or EXP / "runs" / f"model-replay-{stamp}")
        contracts = args.contracts or [config.git(REPO, "rev-parse", "HEAD")]

        if matrix_runner is None:
            def matrix_runner(model: str, model_out: pathlib.Path) -> list[dict]:
                import run_tools

                run_tools.CASES = CASES
                return _grade_rows(
                    run_tools.run_experiment(model_out, REPEATS, contracts, model)
                )

        replay_models(
            models,
            out_dir,
            matrix_runner,
            config.load(REPO, worktree=True).prices if prices is None else prices,
        )
        return 0
    if args.out_dir is None:
        _parser().error("out_dir is required without --models")
    model = args.model or configured_reviewer_model()
    rows = run_experiment(
        pathlib.Path(args.out_dir),
        args.samples,
        args.contracts or list(CONTRACTS),
        model,
    )
    return 0 if all(row.get("returncode") == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
