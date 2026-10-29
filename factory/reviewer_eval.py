"""Three-repeat gate for reviewer prompt and model changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from factory import config, lifecycle, stats

REPEATS = 3
EVENT = "reviewer-eval-summary"
BASELINE_NAME = "reviewer-eval-baseline.json"
CORPUS = Path(__file__).resolve().parents[1] / "experiments/reviewer-calibration"
_CONTRACT_PATHS = {
    config.CONFIG_NAME,
    "factory/config.py",
    "factory/dispatch.py",
    "factory/templates/factory.toml",
}
_SHA = re.compile(r"[0-9a-f]{40}")
_VERDICT = re.compile(r"(?m)^VERDICT: (APPROVE|REVISE)[ \t]*$")


def parsed_verdict(output: object) -> str | None:
    if not isinstance(output, str) or not output.strip():
        return None
    matches = list(_VERDICT.finditer(output.strip()))
    return matches[0].group(1) if len(matches) == 1 and matches[0].end() == len(output.strip()) else None


def grade_output(row: dict, oracle: dict, covered: set[str] | None) -> dict:
    """Attach one deterministic grade after the coverage judge has named oracle IDs."""
    verdict = parsed_verdict(row.get("output"))
    primary = {
        finding["id"]
        for finding in oracle.get("findings", [])
        if finding.get("severity") == "primary"
    }
    result = dict(row)
    result.update({
        "verdict": verdict,
        "expected_verdict": oracle.get("expected_verdict"),
        "covered": sorted(covered or ()),
        "passed": (
            row.get("returncode") == 0
            and not row.get("timeout")
            and verdict == oracle.get("expected_verdict")
            and covered is not None
            and primary <= covered
        ),
    })
    if covered is None:
        result["grade_missing"] = True
    return result


def _run_missing(row: dict) -> bool:
    return (
        row.get("returncode") != 0
        or bool(row.get("timeout"))
        or parsed_verdict(row.get("output")) is None
        or type(row.get("passed")) is not bool
        or bool(row.get("grade_missing"))
    )


def grade_runs(runs: list[dict]) -> str:
    """Map exactly three graded reviewer runs to the gate's per-case status."""
    if len(runs) != REPEATS or any(_run_missing(row) for row in runs):
        return "missing"
    passed = sum(row["passed"] for row in runs)
    if passed == REPEATS:
        return "always-pass"
    if passed == 0:
        return "always-fail"
    return "flaky"


def summarize(
    case_ids: list[str],
    rows: list[dict],
    baseline: dict | None,
    *,
    candidate: str,
    corpus_version: int,
) -> dict:
    grouped = {case: [row for row in rows if row.get("case") == case] for case in case_ids}
    statuses = {case: grade_runs(grouped[case]) for case in case_ids}
    baseline_statuses = (baseline or {}).get("statuses", {})
    flips = [
        case for case in case_ids
        if baseline_statuses.get(case) == "always-pass" and statuses[case] == "always-fail"
    ]
    missing = [case for case in case_ids if statuses[case] == "missing"]
    flaky = []
    for case in case_ids:
        if statuses[case] != "flaky":
            continue
        hashes = {
            hashlib.sha256(row["output"].encode()).hexdigest()
            for row in grouped[case]
        }
        flaky.append({"case": case, "tag": "judge-flaky" if len(hashes) == 1 else "agent-flaky"})
    return {
        "event": EVENT,
        "candidate": candidate,
        "corpus_version": corpus_version,
        "status": "completed",
        "verdict": "block" if missing or flips else "pass",
        "statuses": statuses,
        "missing": missing,
        "flips": flips,
        "flaky": flaky,
        "repeats": REPEATS,
    }


def format_report(summary: dict) -> str:
    def lines(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) or "- None"

    flaky = [f"{row['case']} ({row['tag']})" for row in summary.get("flaky", [])]
    return (
        f"## Reviewer calibration: {str(summary.get('verdict', 'block')).upper()}\n\n"
        f"Baseline: {'missing' if summary.get('baseline_missing') else 'available'}\n\n"
        f"### Stable pass→fail flips\n{lines(summary.get('flips', []))}\n\n"
        f"### Missing cases\n{lines(summary.get('missing', []))}\n\n"
        f"### Flaky cases\n{lines(flaky)}"
    )


def requires_eval(
    paths: list[str], base: dict, head: dict, review_policy: str = "REVIEW.md"
) -> bool:
    watched = _CONTRACT_PATHS | {review_policy.removeprefix("./")}
    touched = any(path.removeprefix("./") in watched for path in paths)
    return touched and (
        base.get("prompt_sha256") != head.get("prompt_sha256")
        or base.get("model") != head.get("model")
    )


def corpus_version(corpus: Path = CORPUS) -> int:
    value = json.loads((corpus / "splits.json").read_text()).get("version")
    if type(value) is not int or value < 1:
        raise ValueError("reviewer calibration corpus needs a positive integer version")
    return value


def case_ids(corpus: Path = CORPUS) -> list[str]:
    splits = json.loads((corpus / "splits.json").read_text())["splits"]
    return [case for split in ("train", "lockbox") for case in splits[split]]


def contract_fingerprint(root: Path) -> dict:
    checkout_cfg = config.load(root, worktree=True)
    model = config.command_model(checkout_cfg.reviewer)
    head = config.git(root, "rev-parse", "HEAD")
    runner = root / "experiments/reviewer-calibration/run_tools.py"
    proc = subprocess.run(
        [sys.executable, str(runner), "--all", "--capture-only", head],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "reviewer prompt capture failed")
    prompt_sha256 = json.loads(proc.stdout)
    if not isinstance(prompt_sha256, dict):
        raise TypeError("reviewer prompt capture did not return a hash map")
    policy = root / checkout_cfg.review_policy
    policy_bytes = policy.read_bytes() if policy.is_file() else b""
    prompt_sha256 = {
        case: hashlib.sha256(f"{digest}\0".encode() + policy_bytes).hexdigest()
        for case, digest in prompt_sha256.items()
    }
    return {"prompt_sha256": prompt_sha256, "model": model}


def trigger_for_checkout(
    paths: list[str], base_root: Path, head_root: Path, review_policy: str = "REVIEW.md"
) -> bool:
    watched = _CONTRACT_PATHS | {review_policy.removeprefix("./")}
    if not any(path.removeprefix("./") in watched for path in paths):
        return False
    return requires_eval(
        paths,
        contract_fingerprint(base_root),
        contract_fingerprint(head_root),
        review_policy,
    )


def baseline_path(cfg: config.Config) -> Path:
    return cfg.factory / BASELINE_NAME


def load_baseline(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def baseline_ready(cfg: config.Config) -> bool:
    baseline = load_baseline(baseline_path(cfg))
    return bool(baseline and baseline.get("corpus_version") == corpus_version())


def write_baseline(path: Path, summary: dict, accepted_main_sha: str) -> bool:
    """Replace the single baseline only with evidence produced at accepted main."""
    if summary.get("candidate") != accepted_main_sha or summary.get("status") != "completed":
        return False
    value = {
        "main_sha": accepted_main_sha,
        "corpus_version": summary["corpus_version"],
        "statuses": summary["statuses"],
        "prompt_sha256": summary.get("prompt_sha256", {}),
        "model": summary.get("model"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
    return True


def result_for_head(head: str, rows: list[dict]) -> dict | None:
    return next((
        row for row in reversed(rows)
        if row.get("event") == EVENT and row.get("candidate") == head
        and row.get("status") == "completed"
    ), None)


def merge_allowed(triggered: bool, head: str, rows: list[dict]) -> bool:
    if not triggered:
        return True
    result = result_for_head(head, rows)
    return bool(result and result.get("verdict") == "pass")


def _coverage_prompt(output: str, oracle: dict) -> str:
    primary = [
        {key: finding.get(key) for key in ("id", "defect", "evidence")}
        for finding in oracle.get("findings", [])
        if finding.get("severity") == "primary"
    ]
    return (
        "Grade only primary-defect coverage. For each oracle finding, include its id only when "
        "the reviewer output reports that concrete defect as blocking. Return exactly one JSON "
        "object: {\"covered\":[\"D1\"]}.\n\n"
        f"ORACLE:\n{json.dumps(primary, ensure_ascii=False)}\n\n"
        f"REVIEWER OUTPUT:\n{output}"
    )


def _parse_coverage(output: str, allowed: set[str]) -> set[str] | None:
    candidates = [output.strip(), *re.findall(r"```(?:json)?\s*(.*?)```", output, re.DOTALL)]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        covered = value.get("covered") if isinstance(value, dict) else None
        if isinstance(covered, list) and all(isinstance(item, str) for item in covered):
            result = set(covered)
            return result if result <= allowed else None
    return None


def _judge(
    command: list[str], output: str, oracle: dict, cwd: Path, timeout: float
) -> tuple[set[str] | None, dict]:
    primary = {
        finding["id"] for finding in oracle.get("findings", [])
        if finding.get("severity") == "primary"
    }
    if not primary:
        return set(), {"seconds": 0.0, "returncode": 0}
    prompt = _coverage_prompt(output, oracle)
    expanded = config.expand(command, prompt=prompt)
    structured = Path(expanded[0]).name == "omp"
    if structured:
        for index, argument in enumerate(expanded[1:], 1):
            if argument == "--mode" and index + 1 < len(expanded):
                expanded[index + 1] = "json"
                break
            if argument.startswith("--mode="):
                expanded[index] = "--mode=json"
                break
        else:
            expanded[1:1] = ["--mode", "json"]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            expanded,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=max(0.001, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, {"seconds": round(time.monotonic() - started, 3), "timeout": True}
    judged, usage = proc.stdout.strip(), None
    if structured:
        from factory.dispatch import _omp_output

        judged, usage = _omp_output(proc.stdout)
    covered = _parse_coverage(judged, primary) if proc.returncode == 0 else None
    return covered, {
        "seconds": round(time.monotonic() - started, 3),
        "returncode": proc.returncode,
        "output": judged,
        "stderr": proc.stderr.strip()[-2000:],
        "usage": usage,
    }


def _run_runner(checkout: Path, out: Path, candidate: str, timeout: float) -> tuple[list[dict], bool]:
    runner = checkout / "experiments/reviewer-calibration/run_tools.py"
    command = [sys.executable, str(runner), "--all", str(out), str(REPEATS), candidate]
    proc = subprocess.Popen(
        command,
        cwd=checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        proc.communicate(timeout=max(0.001, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGTERM)
        proc.communicate()
    try:
        rows = json.loads((out / "results.json").read_text())
    except (OSError, json.JSONDecodeError):
        rows = []
    return rows if isinstance(rows, list) else [], timed_out


def _append(cfg: config.Config, row: dict) -> None:
    cfg.factory.mkdir(parents=True, exist_ok=True)
    lifecycle.append(cfg.factory / "events.jsonl", {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **row,
    })


def run(cfg: config.Config, checkout: Path, *, dry_run: bool = False) -> dict | None:
    cases = case_ids()
    version = corpus_version()
    if dry_run:
        print(
            f"reviewer calibration v{version}: {len(cases)} cases x {REPEATS} repeats = "
            f"{len(cases) * REPEATS} reviewer runs"
        )
        print(
            f"budget: {cfg.reviewer_eval_max_cases} cases, "
            f"{cfg.reviewer_eval_budget_min} minutes"
        )
        return None
    if len(cases) > cfg.reviewer_eval_max_cases:
        raise config.ConfigError(
            f"reviewer eval case budget exceeded ({len(cases)} > {cfg.reviewer_eval_max_cases})"
        )
    candidate = config.git(checkout, "rev-parse", "HEAD")
    if not _SHA.fullmatch(candidate):
        raise ValueError("reviewer eval candidate must be a full lowercase commit SHA")
    started = time.monotonic()
    deadline = started + cfg.reviewer_eval_budget_min * 60
    out = cfg.factory / "reviewer-eval-runs" / candidate
    out.mkdir(parents=True, exist_ok=True)
    rows, timed_out = _run_runner(checkout, out, candidate, deadline - time.monotonic())
    checkout_cfg = config.load(checkout, worktree=True)
    graded = []
    for row in rows:
        case = row.get("case")
        oracle_path = CORPUS / "cases" / str(case) / "oracle.json"
        try:
            oracle = json.loads(oracle_path.read_text())
        except (OSError, json.JSONDecodeError):
            graded.append({**row, "passed": None, "grade_missing": True})
            continue
        if timed_out or row.get("returncode") != 0 or parsed_verdict(row.get("output")) is None:
            graded.append(grade_output({**row, "timeout": timed_out}, oracle, None))
            continue
        covered, judge = _judge(
            checkout_cfg.second_reviewer,
            row["output"],
            oracle,
            checkout,
            deadline - time.monotonic(),
        )
        graded.append({**grade_output(row, oracle, covered), "judge": judge})
    main_sha = config.git(cfg.root, "rev-parse", "HEAD")
    baseline = load_baseline(baseline_path(cfg))
    if baseline and baseline.get("corpus_version") != version:
        baseline = None
    summary = summarize(cases, graded, baseline, candidate=candidate, corpus_version=version)
    if baseline is None and candidate != main_sha:
        summary.update({"verdict": "block", "baseline_missing": True})
    prompt_sha256 = {
        case: next(
            (row.get("prompt_sha256") for row in rows if row.get("case") == case), None
        )
        for case in cases
    }
    usages = [
        usage
        for row in rows
        if isinstance((usage := row.get("usage")), dict)
    ] + [
        usage
        for row in graded
        if isinstance((usage := row.get("judge", {}).get("usage")), dict)
    ]
    costs = [stats.priced(usage, cfg.prices) for usage in usages]
    summary.update({
        "prompt_sha256": prompt_sha256,
        "model": config.command_model(checkout_cfg.reviewer),
        "judge_model": config.command_model(checkout_cfg.second_reviewer),
        "seconds": round(time.monotonic() - started, 3),
        "usage": {
            key: sum(
                value for usage in usages
                if type(value := usage.get(key)) is int
            )
            for key in ("prompt_tokens", "completion_tokens")
        },
        "cost": round(sum(costs), 6) if costs and all(
            cost is not None for cost in costs
        ) else None,
        "cost_measured": bool(costs and all(cost is not None for cost in costs)),
        "results": str(out / "results.json"),
    })
    _append(cfg, summary)
    if not summary["missing"]:
        write_baseline(baseline_path(cfg), summary, main_sha)
    print(format_report(summary))
    return summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory reviewer-eval",
        description="Run the frozen reviewer calibration corpus three times.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    journal_cfg = config.load()
    checkout = Path(config.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    checkout_cfg = config.load(checkout, worktree=True)
    if checkout_cfg.repo != journal_cfg.repo:
        raise config.ConfigError(
            "reviewer-eval must run from a worktree of the configured repository"
        )
    cfg = replace(checkout_cfg, root=journal_cfg.root)
    summary = run(cfg, checkout, dry_run=args.dry_run)
    return int(bool(summary and summary.get("verdict") == "block"))
