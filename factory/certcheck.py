"""Independent merge-certificate verifier and first-parent main audit."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_PASS_CONCLUSIONS = {"success", "neutral", "skipped"}
_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str | None = None

    def value(self) -> dict[str, str]:
        result = {"status": self.status}
        if self.reason is not None:
            result["reason"] = self.reason
        return result


def canonical(value: dict) -> bytes:
    """Canonical certificate bytes; the trailing newline is part of the digest."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run(argv: list[str], root: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, check=check)


def _git(root: Path, *args: str, check: bool = True) -> str:
    return _run(["git", *args], root, check=check).stdout.strip()


def _json(argv: list[str], root: Path) -> object:
    return json.loads(_run(argv, root).stdout)


def _segments(path: Path) -> list[Path]:
    pattern = re.compile(re.escape(path.name) + r"\.([1-9][0-9]*)\.gz\Z")
    numbered = []
    for entry in path.parent.glob(f"{path.name}.*.gz"):
        if match := pattern.fullmatch(entry.name):
            numbered.append((int(match[1]), entry))
    return [entry for _, entry in sorted(numbered, reverse=True)]


def read_journal(path: Path) -> list[dict]:
    """Read rotated segments oldest-first, then the live journal, without Factory imports."""
    rows: list[dict] = []
    for segment in _segments(path):
        with gzip.open(segment, "rt", encoding="utf-8") as handle:
            rows.extend(_rows(handle))
    try:
        with path.open(encoding="utf-8") as handle:
            rows.extend(_rows(handle))
    except FileNotFoundError:
        pass
    return rows


def _rows(lines) -> list[dict]:
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _repo(root: Path) -> str:
    raw = _worktree_config(root)
    slug = raw.get("repo", {}).get("slug") if isinstance(raw.get("repo"), dict) else None
    if isinstance(slug, str) and slug:
        return slug
    url = _git(root, "remote", "get-url", "origin")
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?\Z", url)
    if not match:
        raise ValueError("missing repository slug")
    return match.group(1)


def _worktree_config(root: Path) -> dict:
    try:
        value = tomllib.loads((root / ".factory.toml").read_text())
    except FileNotFoundError:
        return {}
    return value if isinstance(value, dict) else {}


def _version() -> str:
    text = Path(__file__).with_name("__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _installed_commit() -> str | None:
    host = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "factory" / "config.toml"
    try:
        raw = tomllib.loads(host.read_text())
        commit = raw.get("defaults", {}).get("engine", {}).get("sha")
        if isinstance(commit, str) and _SHA.fullmatch(commit):
            return commit
    except (FileNotFoundError, tomllib.TOMLDecodeError, AttributeError):
        pass
    try:
        direct = json.loads(importlib.metadata.distribution("factory").read_text("direct_url.json") or "{}")
    except (importlib.metadata.PackageNotFoundError, json.JSONDecodeError):
        return None
    name = str(direct.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
    return name if _SHA.fullmatch(name) else None


def _source(actual: dict, recorded: object, required: set[str]) -> bool:
    return (
        isinstance(recorded, dict)
        and required <= recorded.keys()
        and all(actual.get(key) == value for key, value in recorded.items())
    )


def _failed(premise: str, kind: str, detail: str) -> Verdict:
    return Verdict(f"failed:{premise}", f"{kind}: {detail}")


def _matching(rows: list[dict], event: str, ticket: int) -> list[tuple[int, dict]]:
    return [(index, row) for index, row in enumerate(rows)
            if row.get("event") == event and row.get("ticket") == ticket]


def _gh_pr(root: Path, repo: str, pr: int) -> dict:
    value = _json(["gh", "api", f"repos/{repo}/pulls/{pr}"], root)
    if not isinstance(value, dict):
        raise TypeError("PR response is not an object")
    return value


def _gh_checks(root: Path, repo: str, head: str) -> list[dict]:
    value = _json(["gh", "api", f"repos/{repo}/commits/{head}/check-runs?per_page=100"], root)
    source = value.get("check_runs") if isinstance(value, dict) else None
    if not isinstance(source, list):
        raise TypeError("check-runs response is missing check_runs")
    checks = []
    for row in source:
        if not isinstance(row, dict):
            continue
        checks.append({"id": row.get("id"), "name": row.get("name"), "conclusion": row.get("conclusion")})
        if row.get("status") != "completed":
            checks[-1]["conclusion"] = None
    return sorted(checks, key=lambda row: (str(row["name"]), str(row["id"])))


def _policy(root: Path, base: str, repo: str) -> dict:
    shown = _run(["git", "show", f"{base}:.factory.toml"], root, check=False)
    config_bytes = shown.stdout.encode() if shown.returncode == 0 else b""
    try:
        raw = tomllib.loads(config_bytes.decode()) if config_bytes else {}
    except tomllib.TOMLDecodeError:
        raw = {}
    manager_review = "escalated"
    host = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "factory" / "config.toml"
    try:
        host_raw = tomllib.loads(host.read_text())
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        host_raw = {}
    host_repos = host_raw.get("repo", {})
    layers = (
        host_raw.get("defaults", {}),
        host_repos.get(repo, {}) if isinstance(host_repos, dict) else {},
        raw,
    )
    for layer in layers:
        manager = layer.get("manager", {}) if isinstance(layer, dict) else {}
        if isinstance(manager, dict) and manager.get("review") in ("escalated", "all"):
            manager_review = manager["review"]
    review = raw.get("review", {}) if isinstance(raw.get("review", {}), dict) else {}
    review_path = review.get("policy", "REVIEW.md")
    policy_file = _run(["git", "show", f"{base}:{review_path}"], root, check=False)
    return {
        "base_sha": base,
        "config_sha256": sha256(config_bytes),
        "manager_review": manager_review,
        "review_policy": {
            "path": review_path,
            "sha256": sha256(policy_file.stdout.encode()) if policy_file.returncode == 0 else None,
        },
        "dispatcher_version": _version(),
        "installed_commit": _installed_commit(),
    }


def human_decision_required(_certificate: dict) -> bool:
    """Rule hook for future risk tiers; today's policy requires none."""
    return False


def verify_document(
    certificate: dict,
    rows: list[dict],
    root: Path,
    repo: str,
    *,
    pre_merge: bool = False,
    human_rule: Callable[[dict], bool] = human_decision_required,
) -> Verdict:
    """Re-derive each premise from journal, Git, and read-only GitHub data."""
    if certificate.get("schema_version") != 1 or certificate.get("repository") != repo:
        return _failed("binding", "forged", "certificate identity does not match repository")
    ticket, pr, head = certificate.get("ticket"), certificate.get("pr"), certificate.get("head")
    premises = certificate.get("premises")
    if type(ticket) is not int or type(pr) is not int or not isinstance(head, str) or not isinstance(premises, dict):
        return _failed("binding", "missing", "certificate identity or premises")

    try:
        remote = _gh_pr(root, repo, pr)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return _failed("binding", "missing", "PR evidence")
    binding = premises.get("binding")
    merged = next((row for row in reversed(rows)
                   if row.get("event") == "merged" and row.get("ticket") == ticket
                   and row.get("pr") == pr and row.get("head") == head), None)
    method = binding.get("merge_method") if isinstance(binding, dict) else None
    merge_sha = certificate.get("merge_sha")
    if not pre_merge:
        if merged is None:
            return _failed("binding", "missing", "merged journal row")
        method = merged.get("method")
        if merged.get("merge_sha") != merge_sha:
            return _failed("binding", "stale", "merge SHA differs from journal")
    expected_binding = {
        "ticket": ticket,
        "pr": pr,
        "head_ref": remote.get("head", {}).get("ref"),
        "base_ref": remote.get("base", {}).get("ref"),
        "base_sha": binding.get("base_sha") if isinstance(binding, dict) else None,
        "merge_method": method,
    }
    if binding != expected_binding or expected_binding["head_ref"] != f"agent/{ticket}":
        return _failed("binding", "forged", "ticket, PR, branch, base, or merge method differs")

    head_premise = premises.get("head")
    remote_head = remote.get("head", {}).get("sha")
    if head_premise != {"approved": head, "pr": head, "matched": head}:
        return _failed("head", "forged", "certificate heads do not agree")
    if remote_head != head:
        return _failed("head", "stale", "PR head differs")

    gate_premise = premises.get("gate")
    gate_candidates = [item for item in _matching(rows, "attempt", ticket) + _matching(rows, "refreshed", ticket)
                       if item[1].get("head") == head or item[1].get("gate_head") == head]
    if not gate_candidates:
        return _failed("gate", "missing", "gate row at head")
    gate_index, gate = max(gate_candidates, key=lambda item: item[0])
    if gate.get("gate") != "PASS" or gate.get("actual_head") != head:
        return _failed("gate", "forged", "latest gate did not pass at actual head")
    if not isinstance(gate_premise, dict) or not _source(
        gate, gate_premise.get("row"), {"at", "event", "ticket", "gate", "head", "actual_head"},
    ):
        return _failed("gate", "forged", "gate source row differs")
    gate_decision = next((row for row in rows
                          if row.get("event") == "decision" and row.get("stage") == "gate"
                          and row.get("ticket") == ticket
                          and row.get("decision_id") == gate_premise.get("decision_id")), None)
    if gate_decision is None:
        return _failed("gate", "missing", "gate decision")
    digest = gate_premise.get("report_sha256")
    if gate_decision.get("report_sha256") != digest:
        return _failed("gate", "forged", "gate report digest differs from decision")
    report = gate_premise.get("report")
    report_path = root / report if isinstance(report, str) else None
    if report_path is None or not report_path.is_file():
        return _failed("gate", "missing", "retained gate report")
    if sha256(report_path.read_bytes()) != digest:
        return _failed("gate", "forged", "retained gate report digest differs")

    review_premise = premises.get("review")
    review_candidates = [item for item in _matching(rows, "review", ticket) if item[1].get("head") == head]
    if not review_candidates:
        return _failed("review", "missing", "review row at head")
    review_index, review = max(review_candidates, key=lambda item: item[0])
    if review.get("accepted") is not True or review.get("verdict") != "APPROVE" or review.get("actual_head") != head:
        return _failed("review", "stale", "review was not accepted at actual head")
    if not isinstance(review_premise, dict) or not _source(
        review, review_premise.get("row"),
        {"at", "event", "ticket", "verdict", "accepted", "head", "actual_head"},
    ):
        return _failed("review", "forged", "review source row differs")
    review_decision = next((row for row in rows
                            if row.get("event") == "decision" and row.get("stage") == "review"
                            and row.get("ticket") == ticket
                            and row.get("decision_id") == review_premise.get("decision_id")), None)
    if review_decision is None:
        return _failed("review", "missing", "review decision")
    if (review_decision.get("comment_url") != review_premise.get("comment_url")
            or review_decision.get("head") != head):
        return _failed("review", "forged", "review decision binding differs")

    approval_premise = premises.get("approval")
    approval_candidates = _matching(rows, "approved", ticket)
    approval_match = [(index, row) for index, row in approval_candidates if row.get("pr") == pr]
    if not approval_match:
        return _failed("approval", "missing", "approval row")
    approval_index, approval = approval_match[-1]
    if any(approval.get(key) != head for key in ("head", "gate_head", "review_head")):
        return _failed("approval", "stale", "approved, gate, and review heads differ")
    if approval_index <= max(gate_index, review_index):
        return _failed("approval", "forged", "approval precedes gate or review")
    if not isinstance(approval_premise, dict) or not _source(
        approval, approval_premise.get("row"),
        {"at", "event", "ticket", "pr", "head", "gate_head", "review_head", "source", "actor"},
    ):
        return _failed("approval", "forged", "approval source row differs")

    manager_premise = premises.get("manager")
    if not isinstance(manager_premise, dict):
        return _failed("manager", "missing", "manager policy")
    if manager_premise.get("policy") == "all":
        matches = [(index, row) for index, row in _matching(rows, "manage", ticket)
                   if row.get("pr") == pr and row.get("head") == head and row.get("decision") == "APPROVE"]
        if not matches:
            return _failed("manager", "missing", "manager APPROVE row")
        _, manager = matches[-1]
        if not _source(manager, manager_premise.get("row"), {"at", "event", "ticket", "pr", "head", "decision"}):
            return _failed("manager", "forged", "manager source row differs")
    elif manager_premise != {"policy": manager_premise.get("policy"), "row": None}:
        return _failed("manager", "forged", "unexpected manager row")

    ci_premise = premises.get("ci")
    try:
        checks = _gh_checks(root, repo, head)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return _failed("ci", "missing", "check-run evidence")
    if not checks or not any(row["conclusion"] in _PASS_CONCLUSIONS for row in checks):
        return _failed("ci", "missing", "no passing check run")
    if any(row["conclusion"] not in _PASS_CONCLUSIONS for row in checks):
        return _failed("ci", "forged", "failing or pending check run")
    if not isinstance(ci_premise, dict) or ci_premise.get("head") != head \
            or sorted(ci_premise.get("checks", []), key=lambda row: (str(row.get("name")), str(row.get("id")))) != checks:
        return _failed("ci", "forged", "recorded check runs differ")

    freshness = premises.get("freshness")
    if not isinstance(freshness, dict) or freshness.get("behind_by") != 0 or freshness.get("method") != method:
        return _failed("freshness", "forged", "recorded freshness differs")
    if pre_merge:
        try:
            compared = _json(["gh", "api", f"repos/{repo}/compare/{binding['base_sha']}...{head}"], root)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError):
            return _failed("freshness", "missing", "compare evidence")
        if not isinstance(compared, dict) or compared.get("behind_by") != 0:
            return _failed("freshness", "stale", "candidate is behind base")
    elif not isinstance(merge_sha, str):
        return _failed("freshness", "missing", "merge SHA")
    elif method == "squash":
        if _git(root, "rev-parse", f"{merge_sha}^{{tree}}", check=False) != _git(root, "rev-parse", f"{head}^{{tree}}", check=False):
            return _failed("freshness", "forged", "squash tree differs from head tree")
    elif method == "merge":
        parents = _git(root, "show", "-s", "--format=%P", merge_sha, check=False).split()
        if len(parents) < 2 or parents[1] != head:
            return _failed("freshness", "forged", "merge second parent differs from head")
    else:
        return _failed("freshness", "forged", "unknown merge method")

    eval_premise = premises.get("eval")
    if eval_premise is not None and (
        not isinstance(eval_premise, dict) or not any(
            _source(row, eval_premise.get("row"), {"at", "event", "candidate", "status", "verdict"})
            and row.get("event") == "eval-summary" and row.get("candidate") == head
            and row.get("status") == "completed" and row.get("verdict") == "keep"
            for row in rows
        )
    ):
        return _failed("eval", "missing", "eval keep claim")

    human = premises.get("human_decision")
    if human_rule(certificate) and human is None:
        return _failed("human_decision", "missing", "required human decision")
    if human is not None and not isinstance(human, dict):
        return _failed("human_decision", "forged", "human decision is not an object")

    if premises.get("risk_tier") is not None and not isinstance(premises.get("risk_tier"), str):
        return _failed("risk_tier", "forged", "risk tier is not text or null")

    policy = premises.get("policy")
    if not isinstance(binding, dict) or policy != _policy(root, binding.get("base_sha", ""), repo):
        return _failed("policy", "forged", "policy digest or named values differ")
    if manager_premise.get("policy") != policy.get("manager_review"):
        return _failed("manager", "forged", "manager premise differs from policy")
    return Verdict("certified")


def verify_file(
    path: Path,
    root: Path,
    repo: str,
    rows: list[dict],
    *,
    pre_merge: bool = False,
    human_rule: Callable[[dict], bool] = human_decision_required,
) -> Verdict:
    try:
        data = path.read_bytes()
        certificate = json.loads(data)
    except (OSError, json.JSONDecodeError):
        return _failed("binding", "missing", "certificate file")
    if not isinstance(certificate, dict) or data != canonical(certificate):
        return _failed("binding", "forged", "certificate is not canonical JSON")
    if not pre_merge:
        merged = next((row for row in reversed(rows)
                       if row.get("event") == "merged"
                       and row.get("ticket") == certificate.get("ticket")
                       and row.get("head") == certificate.get("head")), None)
        if merged is None:
            return _failed("binding", "missing", "merged journal row")
        if merged.get("certificate_sha256") != sha256(data):
            return _failed("binding", "forged", "certificate digest differs from merged row")
    return verify_document(certificate, rows, root, repo, pre_merge=pre_merge, human_rule=human_rule)


def _since(value: str) -> str:
    if match := re.fullmatch(r"([1-9][0-9]*)d", value):
        return (datetime.now(UTC) - timedelta(days=int(match[1]))).isoformat()
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected Nd or ISO timestamp") from exc
    return value


def audit(root: Path, repo: str, rows: list[dict], main: str, since: str) -> list[dict]:
    output = _git(root, "log", "--first-parent", "--format=%H%x00%cI", f"--since={since}", main)
    commits = [line.split("\0", 1) for line in output.splitlines() if "\0" in line]
    certified_rows = [row for row in rows if row.get("event") == "merged" and row.get("certificate_sha256")]
    cutoff = min((str(row.get("at") or "") for row in certified_rows), default=None)
    results = []
    for commit, committed_at in commits:
        merged = next((row for row in reversed(certified_rows) if row.get("merge_sha") == commit), None)
        if merged is None:
            status = "legacy" if cutoff is None or committed_at < cutoff else "uncertified"
            results.append({"commit": commit, "status": status})
            continue
        relative = merged.get("certificate")
        if not isinstance(relative, str):
            relative = f".factory/certificates/{merged.get('ticket')}-{merged.get('head')}.json"
        verdict = verify_file(root / relative, root, repo, rows)
        results.append({"commit": commit, **verdict.value()})
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="factory verify-merge", description=__doc__)
    parser.add_argument("--since", default="7d", type=_since)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--pre-merge", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel"))
    repo = _repo(root)
    rows = read_journal(root / ".factory" / "events.jsonl")
    if args.certificate:
        verdict = verify_file(args.certificate, root, repo, rows, pre_merge=args.pre_merge)
        value: object = verdict.value()
        code = 0 if verdict.status == "certified" else 1
    else:
        raw = _worktree_config(root)
        main_ref = raw.get("repo", {}).get("main", "main") if isinstance(raw.get("repo", {}), dict) else "main"
        value = audit(root, repo, rows, main_ref, args.since)
        code = 1 if any(row["status"].startswith("failed:") for row in value) else 0
    if args.json:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    elif isinstance(value, list):
        for row in value:
            print(f"{row['commit']} {row['status']}" + (f" {row['reason']}" if row.get("reason") else ""))
    else:
        print(value["status"] + (f" {value['reason']}" if value.get("reason") else ""))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
