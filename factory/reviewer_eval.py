"""Three-repeat gate for reviewer prompt and model changes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from factory import config, lifecycle, stats

REPEATS = 3
EVENT = "reviewer-eval-summary"
BASELINE_NAME = "reviewer-eval-baseline.json"
CORPUS_PATH = Path("experiments/reviewer-calibration")
_CONTRACT_PATHS = {
    config.CONFIG_NAME,
    "factory/config.py",
    "factory/dispatch.py",
    "factory/templates/factory.toml",
}
_SHA = re.compile(r"[0-9a-f]{40}")
_SETTINGS_QUERY = """
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root))
from factory import config
cfg = config.load(root, worktree=True)
model = config.command_model(cfg.reviewer)
if not model:
    raise SystemExit("configured reviewer command has no explicit --model")
print(json.dumps({"model": model, "review_policy": cfg.review_policy}))
"""


def parsed_verdict(output: object) -> str | None:
    if not isinstance(output, str) or not output.strip():
        return None
    from factory.dispatch import _parse_review_output

    _, verdict, parsed, _, reason = _parse_review_output(output.strip(), 5)
    return verdict if parsed and reason is None else None


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
        {
            "case": case,
            "baseline": baseline_statuses[case],
            "head": statuses[case],
        }
        for case in case_ids
        if case in baseline_statuses and baseline_statuses[case] != statuses[case]
    ]
    regressions = [
        row["case"]
        for row in flips
        if row["baseline"] == "always-pass" and row["head"] == "always-fail"
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
        "verdict": "block" if missing or regressions else "pass",
        "statuses": statuses,
        "missing": missing,
        "flips": flips,
        "regressions": regressions,
        "flaky": flaky,
        "repeats": REPEATS,
    }


def format_report(summary: dict) -> str:
    def lines(values: list[str]) -> str:
        return "\n".join(f"- `{value}`" for value in values) or "- None"

    flaky = [f"{row['case']} ({row['tag']})" for row in summary.get("flaky", [])]
    flips = [
        f"{row['case']} ({row['baseline']} → {row['head']})"
        for row in summary.get("flips", [])
    ]
    return (
        f"## Reviewer calibration: {str(summary.get('verdict', 'block')).upper()}\n\n"
        f"Baseline: {'missing' if summary.get('baseline_missing') else 'available'}\n\n"
        f"### Status flips\n{lines(flips)}\n\n"
        f"### Stable pass→fail regressions\n{lines(summary.get('regressions', []))}\n\n"
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


def corpus_version(corpus: Path) -> int:
    value = json.loads((corpus / "splits.json").read_text()).get("version")
    if type(value) is not int or value < 1:
        raise ValueError("reviewer calibration corpus needs a positive integer version")
    return value


def case_ids(corpus: Path) -> list[str]:
    splits = json.loads((corpus / "splits.json").read_text())["splits"]
    return [case for split in ("train", "lockbox") for case in splits[split]]


def _isolated_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop(lifecycle.CONTEXT_ENV, None)
    return env


def _checkout_settings(root: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _SETTINGS_QUERY, str(root)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=_isolated_env(),
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "reviewer configuration capture failed")
    value = json.loads(proc.stdout)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("model"), str)
        or not isinstance(value.get("review_policy"), str)
    ):
        raise TypeError("reviewer configuration capture did not return model and policy")
    return value


def contract_fingerprint(root: Path) -> dict:
    settings = _checkout_settings(root)
    head = config.git(root, "rev-parse", "HEAD")
    runner = root / CORPUS_PATH / "run_tools.py"
    proc = subprocess.run(
        [sys.executable, str(runner), "--all", "--capture-only", head],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=_isolated_env(),
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "reviewer prompt capture failed")
    prompt_sha256 = json.loads(proc.stdout)
    if not isinstance(prompt_sha256, dict):
        raise TypeError("reviewer prompt capture did not return a hash map")
    return {"prompt_sha256": prompt_sha256, "model": settings["model"]}


def trigger_for_checkout(
    paths: list[str],
    base_root: Path,
    head_root: Path,
    review_policy: str = "REVIEW.md",
    *,
    expected_head: str | None = None,
) -> bool:
    watched = _CONTRACT_PATHS | {review_policy.removeprefix("./")}
    if not any(path.removeprefix("./") in watched for path in paths):
        return False
    if (
        expected_head is not None
        and config.git(head_root, "rev-parse", "HEAD") != expected_head
    ):
        raise RuntimeError(f"reviewer calibration checkout is not at PR head {expected_head}")
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

def baseline_digest(baseline: dict | None) -> str | None:
    if baseline is None:
        return None
    payload = json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _baseline_matches(baseline: dict | None, version: int, fingerprint: dict) -> bool:
    return bool(
        baseline
        and baseline.get("corpus_version") == version
        and baseline.get("prompt_sha256") == fingerprint.get("prompt_sha256")
        and baseline.get("model") == fingerprint.get("model")
    )


def _is_ancestor(root: Path, ancestor: str | None, descendant: str | None) -> bool:
    if (
        not isinstance(ancestor, str) or not _SHA.fullmatch(ancestor)
        or not isinstance(descendant, str) or not _SHA.fullmatch(descendant)
    ):
        return False
    if ancestor == descendant:
        return True
    proc = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def _merged_improvement_pending(
    baseline: dict, rows: list[dict], root: Path, *, main_sha: str | None = None,
) -> bool:
    digest = baseline_digest(baseline)
    improved = {
        row.get("candidate")
        for row in rows
        if row.get("event") == EVENT
        and row.get("status") == "completed"
        and row.get("baseline_sha256") == digest
        and any(
            flip.get("baseline") in ("always-fail", "flaky")
            and flip.get("head") == "always-pass"
            for flip in row.get("flips", [])
            if isinstance(flip, dict)
        )
    }
    return any(
        row.get("event") == "merged"
        and row.get("head") in improved
        and not _is_ancestor(
            root, row.get("merge_sha", ""), main_sha or baseline.get("main_sha", "")
        )
        for row in rows
    )


def baseline_ready(cfg: config.Config) -> bool:
    baseline = load_baseline(baseline_path(cfg))
    return bool(
        _baseline_matches(
            baseline,
            corpus_version(cfg.root / CORPUS_PATH),
            contract_fingerprint(cfg.root),
        )
        and not _merged_improvement_pending(
            baseline, lifecycle.read_events(cfg.factory / "events.jsonl"), cfg.root
        )
    )


def write_baseline(
    path: Path, summary: dict, accepted_main_sha: str, *, root: Path | None = None,
) -> bool:
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
    # Serialize the ancestry check with replacement: overlapping main runs may
    # finish in the opposite order, but baseline history must never move back.
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = load_baseline(path)
        if previous and not _is_ancestor(
            root or path.parent.parent, previous.get("main_sha", ""), accepted_main_sha
        ):
            return False
        if previous and _merged_improvement_pending(
            previous,
            lifecycle.read_events(path.parent / "events.jsonl"),
            root or path.parent.parent,
            main_sha=accepted_main_sha,
        ):
            return False
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


def merge_allowed(
    triggered: bool,
    head: str,
    rows: list[dict],
    expected_baseline_sha256: str | None,
) -> bool:
    if not triggered:
        return True
    result = result_for_head(head, rows)
    return bool(
        expected_baseline_sha256
        and result
        and result.get("baseline_sha256") == expected_baseline_sha256
        and result.get("verdict") == "pass"
    )


def _candidate_accepted(cfg: config.Config, candidate: str) -> bool | None:
    from factory.eval import _candidate_accepted as accepted

    return accepted(cfg, candidate)


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
        from factory.dispatch import without_github

        clean = {key: value for key, value in _isolated_env().items() if not key.startswith("GIT_")}
        with tempfile.TemporaryDirectory(prefix="factory-review-") as review_dir, \
                without_github(clean) as env:
            proc = subprocess.run(
                expanded, cwd=Path(review_dir), env=env,
                capture_output=True, text=True, check=False, timeout=max(0.001, timeout),
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
    result_path = out / "results.json"
    result_path.unlink(missing_ok=True)
    runner = checkout / "experiments/reviewer-calibration/run_tools.py"
    command = [sys.executable, str(runner), "--all", str(out), str(REPEATS), candidate]
    timed_out = False
    # The parent owns cleanup even when SIGTERM prevents the runner's atexit
    # handlers from running. No shared checkout/cache directory is removed.
    with tempfile.TemporaryDirectory(prefix="factory-cal-suite-") as temporary:
        proc = subprocess.Popen(
            command,
            cwd=checkout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env={**_isolated_env(), "FACTORY_CAL_CHECKOUT_PARENT": temporary},
        )
        try:
            proc.communicate(timeout=max(0.001, timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            proc.communicate()
    try:
        rows = json.loads(result_path.read_text())
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
    corpus = checkout / CORPUS_PATH
    cases = case_ids(corpus)
    version = corpus_version(corpus)
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
    checkout_settings = _checkout_settings(checkout)
    graded = []
    for row in rows:
        case = row.get("case")
        oracle_path = corpus / "cases" / str(case) / "oracle.json"
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
    baseline = load_baseline(baseline_path(cfg))
    if not _baseline_matches(
        baseline,
        version,
        contract_fingerprint(cfg.root) if baseline is not None else {},
    ):
        baseline = None
    summary = summarize(cases, graded, baseline, candidate=candidate, corpus_version=version)
    summary["baseline_sha256"] = baseline_digest(baseline)
    accepted = _candidate_accepted(cfg, candidate)
    if accepted is not None:
        summary["accepted"] = accepted
    if baseline is None and accepted is not True:
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
        "model": checkout_settings["model"],
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
    if not summary["missing"] and accepted is True:
        write_baseline(baseline_path(cfg), summary, candidate, root=cfg.root)
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
