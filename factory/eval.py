"""Replay the frozen worker eval suite with one configured worker profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from statistics import mean

from factory import config, dispatch, learn, lifecycle, promotion, stats

MANIFEST = Path(__file__).with_name("eval-suite.json")
REPEATS = promotion.MIN_REPEATS
_SHA = re.compile(r"[0-9a-f]{40}")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_TRIGGER_FILES = {
    config.CONFIG_NAME,
    config.LESSONS_NAME,
    "AGENTS.md",
    "CLAUDE.md",
    "factory/brief.py",
    "factory/config.py",
    "factory/dispatch.py",
    "factory/learn.py",
    "factory/templates/agent_task.md",
}


@dataclass(frozen=True)
class EvalCase:
    id: str
    repo: str
    ticket: int
    base: str
    split: str
    title: str
    labels: tuple[str, ...]
    issue: dict
    accepted: dict


@dataclass(frozen=True)
class Manifest:
    version: int
    cases: tuple[EvalCase, ...]


def _manifest(document: object) -> Manifest:
    if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] < 1:
        raise ValueError("eval manifest needs a positive integer version")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list) or not 20 <= len(raw_cases) <= 50:
        raise ValueError("eval manifest needs 20-50 cases")
    cases = []
    ids = set()
    for index, row in enumerate(raw_cases):
        where = f"eval manifest case {index}"
        if not isinstance(row, dict):
            raise TypeError(f"{where} must be an object")
        repo, ticket = row.get("repo"), row.get("ticket")
        case_id = row.get("id")
        if not isinstance(repo, str) or not _REPO.fullmatch(repo):
            raise ValueError(f"{where} has invalid repo")
        if type(ticket) is not int or ticket < 1 or case_id != f"{repo}#{ticket}":
            raise ValueError(f"{where} has invalid id or ticket")
        if case_id in ids:
            raise ValueError(f"duplicate eval case {case_id}")
        ids.add(case_id)
        if not isinstance(row.get("base"), str) or not _SHA.fullmatch(row["base"]):
            raise ValueError(f"{case_id} has invalid base revision")
        if row.get("split") not in ("train", "lockbox"):
            raise ValueError(f"{case_id} split must be train or lockbox")
        if not isinstance(row.get("title"), str) or not row["title"]:
            raise ValueError(f"{case_id} needs a title")
        labels, issue, accepted = row.get("labels"), row.get("issue"), row.get("accepted")
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise ValueError(f"{case_id} labels must be text")
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("title"), str)
            or not isinstance(issue.get("body"), str)
            or not isinstance(issue.get("comments"), list)
        ):
            raise TypeError(f"{case_id} needs a frozen issue title, body and comments")
        if (
            not isinstance(accepted, dict)
            or type(accepted.get("merged_pr")) is not int
            or not isinstance(accepted.get("merge_commit"), str)
            or not _SHA.fullmatch(accepted["merge_commit"])
            or accepted.get("gate") != "PASS"
            or accepted.get("review") != "APPROVE"
        ):
            raise ValueError(f"{case_id} needs the accepted merged PR, commit, gate PASS and reviewer APPROVE")
        cases.append(EvalCase(
            case_id, repo, ticket, row["base"], row["split"], row["title"],
            tuple(labels), issue, accepted,
        ))
    if {case.split for case in cases} != {"train", "lockbox"}:
        raise ValueError("eval manifest needs train and lockbox cases")
    return Manifest(document["version"], tuple(cases))


def load_manifest(path: Path = MANIFEST, *, previous: Manifest | None = None) -> Manifest:
    try:
        current = _manifest(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read eval manifest {path}: {exc}") from exc
    if previous is not None:
        old = {case.id for case in previous.cases if case.split == "lockbox"}
        new = {case.id for case in current.cases if case.split == "lockbox"}
        if missing := sorted(old - new):
            raise ValueError(f"lockbox cases never move out: {', '.join(missing)}")
        if current != previous and current.version <= previous.version:
            raise ValueError("changed eval manifest must increase its version")
    return current


def _validate_history(path: Path, current: Manifest) -> None:
    try:
        root = Path(subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip())
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, subprocess.CalledProcessError, ValueError):
        return
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"origin/main:{relative}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        try:
            previous = _manifest(json.loads(result.stdout))
        except json.JSONDecodeError as exc:
            raise ValueError(f"origin/main has an invalid eval manifest: {exc}") from exc
        load_manifest(path, previous=previous)


def requires_eval(paths: list[str]) -> bool:
    for path in paths:
        normalized = path.removeprefix("./")
        if normalized in _TRIGGER_FILES:
            return True
        if normalized.startswith((".omp/skills/", ".agents/skills/")) or normalized.endswith("/SKILL.md"):
            return True
    return False


def merge_allowed(paths: list[str], head: str, rows: list[dict]) -> bool:
    if not requires_eval(paths):
        return True
    return any(
        row.get("event") == "eval-summary"
        and row.get("candidate") == head
        and row.get("status") == "completed"
        and row.get("verdict") == "keep"
        for row in rows
    )


def _case_config(controller: config.Config, target: config.Config, profile: str) -> config.Config:
    if profile not in controller.workers:
        raise config.ConfigError(f"eval profile {profile!r} is not configured")
    return replace(
        target,
        workers={"default": controller.workers[profile]},
        worker_when={},
        worker_select_label={},
        worker_env={"default": controller.worker_env.get(profile, {})},
        worker_version={"default": controller.worker_version.get(profile)} if profile in controller.worker_version else {},
        worker_usage={"default": controller.worker_usage.get(profile, {})} if profile in controller.worker_usage else {},
        worker_transcript={"default": controller.worker_transcript[profile]} if profile in controller.worker_transcript else {},
        worker_wrap=controller.worker_wrap,
        prices=controller.prices,
    )


def _prime_globals(cfg: config.Config, profile: str) -> dict:
    env = cfg.worker_environment(profile, cfg.factory / "eval-prompt.md", cfg.root)
    directory = env.get("PRIME_AGENT_CODING_AGENT_DIR")
    path = Path(directory) / "harness" / "harness_state.json" if directory else None
    if path is None or not path.is_file():
        return {}
    try:
        entries = json.loads(path.read_text()).get("entries", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    return {
        kind: {
            key: value for key, value in values.items()
            if isinstance(value, dict) and value.get("scope") == "global"
        }
        for kind, values in entries.items()
        if isinstance(values, dict)
    }


def _fingerprint(
    cfg: config.Config, profile: str, context_root: Path | None = None,
) -> tuple[str, dict]:
    root = context_root or cfg.root
    files = {}
    paths = learn.worker_context_paths(root) | {Path("factory/templates/agent_task.md")}
    for relative in sorted(paths):
        path = root / relative
        candidates = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
        for candidate in candidates:
            if candidate.is_file():
                files[candidate.relative_to(root).as_posix()] = hashlib.sha256(
                    candidate.read_bytes(),
                ).hexdigest()
    harness = _prime_globals(cfg, profile)
    value = {
        "command": cfg.workers[profile],
        "environment": cfg.worker_env.get(profile, {}),
        "version": cfg.worker_version.get(profile),
        "routing": cfg.worker_when.get(profile),
        "files": files,
        "prime_global": harness,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), harness


def _quality(metrics: dict) -> float:
    if not metrics.get("approved"):
        return 0.0
    penalty = 0.1 * (not metrics.get("first_gate_pass"))
    penalty += 0.05 * max(0, int(metrics.get("review_rounds", 0)) - 1)
    penalty += 0.02 * int(metrics.get("revise_findings", 0))
    return round(max(0.0, 1.0 - penalty), 6)


def _at() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(cfg: config.Config, row: dict) -> None:
    cfg.factory.mkdir(parents=True, exist_ok=True)
    lifecycle.append(cfg.factory / "events.jsonl", {"at": _at(), **row})


def _summary_splits(rows: list[dict], manifest: Manifest) -> dict:
    result = {}
    for split in ("train", "lockbox"):
        scores = []
        for repeat in range(1, REPEATS + 1):
            values = [row["score"] for row in rows if row["split"] == split and row["repeat"] == repeat]
            scores.append(mean(values))
        result[split] = {
            "cases": sum(case.split == split for case in manifest.cases),
            "scores": scores,
            "mean": mean(scores),
            "noise": promotion.noise(scores),
        }
    return result


def _baseline(cfg: config.Config, profile: str, version: int) -> dict | None:
    try:
        rows = lifecycle.read_events(cfg.factory / "events.jsonl")
    except OSError:
        return None
    return next((
        row for row in reversed(rows)
        if row.get("event") == "eval-summary"
        and row.get("profile") == profile
        and row.get("manifest_version") == version
        and row.get("status") in ("baseline", "completed")
        and isinstance(row.get("splits"), dict)
    ), None)


def _not_tested(
    cfg: config.Config,
    *,
    run_id: str,
    profile: str,
    manifest: Manifest,
    candidate: str,
    trigger: str,
    reason: str,
    rows: list[dict],
    started: float,
    fingerprint: str,
    harness: dict,
) -> dict:
    summary = {
        "event": "eval-summary", "run": run_id, "profile": profile,
        "manifest_version": manifest.version, "candidate": candidate, "trigger": trigger,
        "status": "not_tested", "verdict": None, "reason": reason,
        "cases": len(rows), "repeats": REPEATS, "tokens": sum(
            value for row in rows
            if isinstance((value := row.get("reported_tokens")), (int, float))
        ),
        "seconds": round(time.monotonic() - started, 3), "splits": {},
        "fingerprint": fingerprint, "prime_global": harness,
    }
    _append(cfg, summary)
    print(f"eval {profile}: not_tested ({reason})")
    return summary


def run(
    cfg: config.Config,
    profile: str,
    manifest_path: Path = MANIFEST,
    *,
    dry_run: bool = False,
    scheduled: bool = False,
    candidate: str | None = None,
    context_root: Path | None = None,
) -> dict | None:
    manifest = load_manifest(manifest_path)
    _validate_history(manifest_path, manifest)
    if profile not in cfg.workers:
        raise config.ConfigError(f"eval profile {profile!r} is not configured")
    train = sum(case.split == "train" for case in manifest.cases)
    lockbox = len(manifest.cases) - train
    if dry_run:
        print(f"eval suite v{manifest.version}, profile {profile}: {len(manifest.cases)} cases x {REPEATS} repeats = {len(manifest.cases) * REPEATS} replays")
        print(f"budget: {cfg.eval_max_cases} cases, {cfg.eval_max_tokens} tokens, {cfg.eval_budget_min} minutes")
        for case in manifest.cases:
            print(f"{case.split}: {case.repo}#{case.ticket} @ {case.base[:12]} — {case.title}")
        print(f"splits: train {train}, lockbox {lockbox}")
        return None

    candidate = candidate or subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not _SHA.fullmatch(candidate):
        raise ValueError("eval candidate must be a full lowercase commit SHA")
    run_id = uuid.uuid4().hex
    trigger = "scheduled" if scheduled else "change"
    started = time.monotonic()
    fingerprint, harness = _fingerprint(cfg, profile, context_root)
    rows = []
    if len(manifest.cases) > cfg.eval_max_cases:
        return _not_tested(
            cfg, run_id=run_id, profile=profile, manifest=manifest, candidate=candidate,
            trigger=trigger, reason=f"case budget exceeded ({len(manifest.cases)} > {cfg.eval_max_cases})",
            rows=rows, started=started, fingerprint=fingerprint, harness=harness,
        )
    deadline = started + cfg.eval_budget_min * 60
    used_tokens = 0
    for repeat in range(1, REPEATS + 1):
        for case in manifest.cases:
            if time.monotonic() >= deadline:
                return _not_tested(
                    cfg, run_id=run_id, profile=profile, manifest=manifest, candidate=candidate,
                    trigger=trigger, reason=f"wall-time budget exhausted ({cfg.eval_budget_min} min)",
                    rows=rows, started=started, fingerprint=fingerprint, harness=harness,
                )
            try:
                target = stats.resolve(case.repo)
                replay_cfg = _case_config(cfg, target, profile)
                dispatch.configure(replay_cfg)
                metrics = learn.replay_ticket(
                    replay_cfg,
                    learn.ReplayCase(case.ticket, case.base, case.title, case.labels, case.issue),
                    None,
                    deadline,
                    context_root if case.repo == cfg.repo and context_root is not None else target.root,
                )
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - unavailable evidence is not escalation
                _append(cfg, {
                    "event": "eval", "run": run_id, "profile": profile, "case": case.id,
                    "repo": case.repo, "ticket": case.ticket, "base": case.base,
                    "split": case.split, "repeat": repeat, "status": "not_tested",
                    "reason": f"{type(exc).__name__}: {exc}",
                })
                return _not_tested(
                    cfg, run_id=run_id, profile=profile, manifest=manifest, candidate=candidate,
                    trigger=trigger, reason=f"replay failed for {case.id}: {type(exc).__name__}: {exc}",
                    rows=rows, started=started, fingerprint=fingerprint, harness=harness,
                )
            finally:
                dispatch.configure(cfg)
            reported = metrics.get("reported_tokens", metrics.get("tokens"))
            if isinstance(reported, (int, float)):
                used_tokens += int(reported)
            row = {
                "event": "eval", "run": run_id, "profile": profile, "case": case.id,
                "repo": case.repo, "ticket": case.ticket, "base": case.base,
                "split": case.split, "repeat": repeat, "status": "completed",
                "score": _quality(metrics), **metrics,
            }
            _append(cfg, row)
            rows.append(row)
            if used_tokens > cfg.eval_max_tokens:
                return _not_tested(
                    cfg, run_id=run_id, profile=profile, manifest=manifest, candidate=candidate,
                    trigger=trigger, reason=f"token budget exceeded ({used_tokens} > {cfg.eval_max_tokens})",
                    rows=rows, started=started, fingerprint=fingerprint, harness=harness,
                )

    splits = _summary_splits(rows, manifest)
    baseline = _baseline(cfg, profile, manifest.version)
    verdict = reason = None
    status = "baseline"
    claim = None
    if baseline is not None:
        claim = {
            "contract_version": promotion.CONTRACT_VERSION,
            "objective": "regression",
            "stage": "worker",
            "change": {"kind": "worker-config", "profile": profile},
            "baseline": baseline["candidate"],
            "candidate": candidate,
            "splits": {
                split: {
                    "baseline": baseline["splits"][split]["scores"],
                    "candidate": splits[split]["scores"],
                }
                for split in ("train", "lockbox")
            },
        }
        verdict, reason = promotion.decide(claim)
        status = "completed"
    summary = {
        "event": "eval-summary", "run": run_id, "profile": profile,
        "manifest_version": manifest.version, "candidate": candidate, "trigger": trigger,
        "status": status, "verdict": verdict, "reason": reason,
        "cases": len(manifest.cases), "repeats": REPEATS,
        "tokens": used_tokens, "seconds": round(time.monotonic() - started, 3),
        "splits": splits, "claim": claim, "fingerprint": fingerprint,
        "prime_global": harness, "flagged": bool(scheduled and verdict == "revert"),
    }
    _append(cfg, summary)
    print(f"eval {profile}: {verdict or 'baseline'}" + (f" ({reason})" if reason else ""))
    return summary

def run_if_changed(cfg: config.Config, dry_run: bool = False) -> dict | None:
    """Run the configured profile when its worker context differs from the latest eval."""
    manifest = load_manifest()
    fingerprint, _ = _fingerprint(cfg, cfg.eval_profile)
    latest = _baseline(cfg, cfg.eval_profile, manifest.version)
    if latest is not None and latest.get("fingerprint") == fingerprint:
        return None
    if dry_run:
        print(f"eval {cfg.eval_profile}: would run (worker context changed)")
        return None
    return run(cfg, cfg.eval_profile)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory eval", description="Replay the frozen worker eval suite.")
    parser.add_argument("--profile", required=True, help="configured worker profile to run")
    parser.add_argument("--dry-run", action="store_true", help="list cases and budget without running workers")
    parser.add_argument("--scheduled", action="store_true", help="record this as the scheduled drift run")
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    journal_cfg = config.load()
    checkout = Path(config.git(Path.cwd(), "rev-parse", "--show-toplevel"))
    checkout_cfg = config.load(checkout, worktree=True)
    if checkout_cfg.repo != journal_cfg.repo:
        raise config.ConfigError("eval must run from a worktree of the configured repository")
    cfg = replace(checkout_cfg, root=journal_cfg.root)
    dispatch.configure(cfg)
    summary = run(
        cfg, args.profile, args.manifest, dry_run=args.dry_run, scheduled=args.scheduled,
        context_root=checkout,
    )
    return int(bool(summary and (
        summary.get("status") == "not_tested" or summary.get("verdict") == "revert"
    )))
