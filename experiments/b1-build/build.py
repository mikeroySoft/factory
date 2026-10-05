#!/usr/bin/env python3
"""B1 isolated build experiment producer (#98).

start   freeze inputs and the oracle on the host, run case.py in the sandbox, retain the run
status  fresh-process read of a run's status.json (plus live-writer check)
stop    cooperative stop request through the run's own artifact; idempotent
report  print report.json and re-verify every retained artifact digest

`start` reads GitHub (issue GETs and the existing `factory evidence` initiative read, run
from a disposable checkout of the committed source) and writes only its own run directory
under the main root's .factory/experiments/b1-build/ (or --output-dir). Everything that
renders or scores runs inside sandbox.py's boundary; if that boundary cannot be proven the
run is invalid and nothing executes on the host instead. Zero model or provider calls.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.json"
sys.path.insert(0, str(HERE))
import sandbox  # noqa: E402


def _load_o1():
    """Reuse O1's run-directory conventions (atomic writes, status/stop/report) rather than copy them."""
    spec = importlib.util.spec_from_file_location("o1_observer", HERE.parent / "o1-observation" / "observer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


o1 = _load_o1()
Run, dumps, now, sha256 = o1.Run, o1.dumps, o1.now, o1.sha256
HEADER = re.compile(r"^\*\*([^*\n]+)\*\*[ \t]*$", re.M)
TIER = re.compile(r"\s*(NOW|NEXT|THEN|LATER)\b")
ISSUE_FIELDS = "number,title,state,labels,body,updatedAt"
STDERR_KEEP = 65536


class Invalid(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# -- oracle: derived from raw issue text, independently of the representation under test --

def section(body: str, name: str) -> str:
    matches = list(HEADER.finditer(body or ""))
    for index, match in enumerate(matches):
        if match[1].strip() == name:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            return body[match.end():end]
    return ""


def declared_blockers(body: str) -> list[int]:
    return sorted({int(n) for line in re.findall(r"(?im)^blocked by:(.*)$", body or "")
                   for n in re.findall(r"#(\d+)", line)})


def links_of(initiative: dict) -> list[int]:
    return sorted({int(n) for n in re.findall(r"(?<![\w/#])#(\d{1,9})\b", section(initiative["body"], "Implementation links"))})


def derive_oracle(issues: dict, revision: str) -> list[list]:
    """Expected sequencing facts as [kind, ticket, value] rows."""
    initiative = issues["initiative"]
    facts = [["revision", initiative["number"], revision]]
    tiers: dict[int, str] = {}
    for line in section(initiative["body"], "Plan").splitlines():
        if match := TIER.match(line):
            for n in re.findall(r"#(\d+)", line):
                tiers.setdefault(int(n), match[1])
    for number in links_of(initiative):
        issue = issues["linked"][str(number)]
        state = issue["state"]
        facts.append(["state", number, state])
        if number in tiers:
            facts.append(["tier", number, tiers[number]])
        if state == "OPEN":
            labels = {label["name"] for label in issue.get("labels") or []}
            facts.append(["held", number, "factory-held" in labels])
            facts.append(["open_blockers", number,
                          [b for b in declared_blockers(issue["body"]) if issues["blockers"][str(b)]["state"] == "OPEN"]])
    return facts


# -- host-side freeze (network GETs only; no model or provider) --

def gh_issue(repository: str, number: int) -> dict:
    out = subprocess.run(["gh", "issue", "view", str(number), "--repo", repository, "--json", ISSUE_FIELDS],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise Invalid(f"github_read_failed:#{number}")
    return json.loads(out.stdout)


def freeze_issues(repository: str, initiative: int) -> dict:
    root = gh_issue(repository, initiative)
    linked = {str(n): gh_issue(repository, n) for n in links_of(root)}
    wanted = sorted({b for issue in linked.values() if issue["state"] == "OPEN" for b in declared_blockers(issue["body"])})
    blockers = {str(b): linked.get(str(b)) or gh_issue(repository, b) for b in wanted}
    return {"initiative": root, "linked": linked, "blockers": blockers, "observed_at": now()}


def checkout(commit: str, repo: Path, dest: Path) -> str:
    """Disposable source: `git archive` of the pinned commit, no .git, nothing shared with the repo."""
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return sha256(data)


def capture_baseline(src: Path, main_root: Path, repository: str, initiative: int) -> bytes:
    request = {"schema_version": 1, "repository": repository, "op": "investigate", "kind": "initiative",
               "number": initiative}
    out = subprocess.run([sys.executable, "-B", "-m", "factory.cli", "evidence", "--root", str(main_root)],
                         cwd=src, input=json.dumps(request).encode(), capture_output=True, timeout=300)
    try:
        parsed = json.loads(out.stdout)
    except ValueError:
        raise Invalid("baseline_unparseable") from None
    if not parsed.get("ok"):
        raise Invalid("baseline_unavailable")
    return out.stdout


def canonical_revision(src: Path, body: str, initiative: int) -> str:
    """Revision identity as the accepted binding contract (#57) defines it, from the pinned source."""
    code = ("import json,sys\nfrom factory import binding\nd=json.load(sys.stdin)\n"
            "print(binding._digest(binding._sections(d['body'], d['n'])))")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=src, capture_output=True, text=True, timeout=60,
                         input=json.dumps({"body": body, "n": initiative}))
    if out.returncode != 0 or not re.fullmatch(r"[0-9a-f]{64}", out.stdout.strip()):
        raise Invalid("revision_digest_failed")
    return out.stdout.strip()


def host_paths(main_root: Path) -> dict:
    home = Path.home()
    return {"repository root": str(main_root), "state ledger": str(main_root / ".factory" / "events.jsonl"),
            "operator home": str(home), "gh config": str(home / ".config" / "gh"),
            "omp agent config": str(home / ".omp"), "ssh keys": str(home / ".ssh")}


# -- run lifecycle --

def committed_source(repo: Path) -> str:
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "experiments", "factory"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit("start runs the committed source only; commit or stash experiments/ and factory/ first")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def new_run(out: Path, protocol: dict, protocol_bytes: bytes, limits: dict) -> tuple[Run, int, list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    guard = os.open(out, os.O_RDONLY)
    fcntl.flock(guard, fcntl.LOCK_EX)  # serialises iteration numbering only; not a scheduler
    try:
        prior = sorted(p.name for p in out.iterdir() if (p / "protocol.json").exists())
        iteration = len(prior) + 1
        if iteration > protocol["max_iterations"]:
            raise SystemExit(f"lineage {protocol['lineage']} already has {len(prior)} runs; cap is {protocol['max_iterations']}")
        run_id = f"{protocol['experiment']}-i{iteration}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        (out / run_id).mkdir()
        run = Run(out / run_id, limits["max_output_bytes"], limits["output_reserve_bytes"])
        run.create("protocol.json", protocol_bytes)
    finally:
        os.close(guard)
    return run, iteration, prior


def start(args) -> int:
    protocol_bytes = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_bytes)
    limits = dict(protocol["limits"])
    if args.max_seconds is not None:
        if not 0 < args.max_seconds <= limits["max_seconds"]:
            raise SystemExit(f"--max-seconds outside 1..{limits['max_seconds']} (protocol ceiling)")
        limits["max_seconds"] = args.max_seconds
    repo = HERE.parents[1]
    commit = committed_source(repo)
    main_root = o1.main_root()
    out = Path(args.output_dir) if args.output_dir else main_root / ".factory" / "experiments" / protocol["experiment"]
    started = time.monotonic()
    run, iteration, prior = new_run(out, protocol, protocol_bytes, limits)
    lock = os.open(run.path / "writer.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run.put_status(
        schema_version=1, experiment=protocol["experiment"], run_id=run.path.name, kind=protocol["kind"],
        lineage=protocol["lineage"], iteration=iteration, max_iterations=protocol["max_iterations"],
        prior_runs=prior, protocol_revision=protocol["protocol_revision"], protocol_sha256=sha256(protocol_bytes),
        cutoff=None, source={"commit": commit}, state="waiting", reason=None, started_at=now(), finished_at=None,
        limits=limits, out_of_lineage=args.output_dir is not None)
    scratch = Path(tempfile.mkdtemp(prefix="b1-", dir=os.environ.get("XDG_RUNTIME_DIR") or None))
    os.chmod(scratch, 0o700)
    state, reason, outcome, result, archive_sha = "invalid", None, None, None, None
    try:
        unavailable = sandbox.available()
        if unavailable:
            raise Invalid(f"isolation_unavailable: {unavailable}")
        src, inp, private = scratch / "src", scratch / "in", scratch / "out"
        for path in (src, inp, private):
            path.mkdir()
        archive_sha = checkout(commit, repo, src)
        run.put_status(state="observing")
        # Freeze: inputs and oracle exist before anything is rendered.
        issues = freeze_issues(protocol["repository"], protocol["initiative"])
        baseline = capture_baseline(src, main_root, protocol["repository"], protocol["initiative"])
        revision = canonical_revision(src, issues["initiative"]["body"], protocol["initiative"])
        frozen = {"issues.json": dumps(issues), "baseline.json": baseline,
                  "oracle.json": dumps({"facts": derive_oracle(issues, revision)})}
        manifest = {"initiative": protocol["initiative"], "inputs": {k: sha256(v) for k, v in frozen.items()},
                    "commit": commit, "archive_sha256": archive_sha}
        frozen["manifest.json"] = dumps(manifest)
        if not run.fits(sum(len(v) for v in frozen.values())):
            raise o1.Stop("interrupted", "output_limit")
        for name, data in frozen.items():
            (inp / name).write_bytes(data)
            run.create(name, data)
        # Concrete host paths for the probes stay in the sandbox input only; the record keeps labels.
        (inp / "host-paths.json").write_bytes(dumps(host_paths(main_root)))
        inputs_digest = sha256(frozen["manifest.json"])
        run.put_status(frozen_at=now(), source={
            "commit": commit, "archive_sha256": archive_sha, "path": f"github:{protocol['repository']}",
            "sha256": inputs_digest, "initial_size": sum(len(v) for v in frozen.values()),
            "covered": [0, sum(len(v) for v in frozen.values())], "complete": True})
        remaining = int(limits["max_seconds"] - (time.monotonic() - started))
        if remaining < 1:
            raise o1.Stop("interrupted", "time_limit")
        sandbox_started_at = now()
        outcome = sandbox.run([sandbox.PYTHON, "-I", "-B", "/src/experiments/b1-build/case.py"],
                              src=src, inp=inp, out=private, limits=limits, seconds=remaining,
                              evidence_dir=scratch, stop_file=run.path / "stop-request")
        outcome["started_at"] = sandbox_started_at
        retain_sandbox_evidence(run, scratch)
        state, reason, result = settle(run, outcome, private)
    except Invalid as exc:
        state, reason = "invalid", exc.reason
    except o1.Stop as stop:
        state, reason = stop.state, stop.reason
    except Exception as exc:  # host-side freeze/settle failure: retain an invalid run, never an unsettled one
        state, reason = "invalid", f"producer_error:{type(exc).__name__}"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    report = build_report(run, protocol, outcome, result, state, reason, started)
    run.create("report.json", dumps(report))
    artifacts = {path.name: sha256(path.read_bytes()) for path in sorted(run.path.iterdir())
                 if path.name not in ("status.json", "writer.lock", "stop-request") and not path.name.startswith(".")}
    run.put_status(state=state, reason=reason, finished_at=now(), artifacts=artifacts,
                   consumption={"elapsed_seconds": round(time.monotonic() - started, 3), "output_bytes": run.used,
                                "model_calls": 0, "provider_calls": 0})
    os.close(lock)
    print(json.dumps({"run": str(run.path), "state": state, "reason": reason}))
    return 0


def retain_sandbox_evidence(run: Run, scratch: Path) -> None:
    limits = scratch / "limits.json"
    if limits.exists():
        run.create("limits.json", limits.read_bytes())
    for name in ("sandbox.stdout", "sandbox.stderr"):
        with (scratch / name).open("rb") as handle:
            data = handle.read(STDERR_KEEP + 1)
        if data:
            run.create(name, data[:STDERR_KEEP])


def settle(run: Run, outcome: dict, private: Path) -> tuple[str, str | None, dict | None]:
    """Map the sandbox outcome and its outputs to a run state; copy only the expected outputs."""
    kind = outcome["outcome"]
    if kind in ("launch_failed", "limits_mismatch"):
        return "invalid", f"isolation_{kind}", None
    if kind in ("time_limit", "stopped", "killed"):
        return "interrupted", {"stopped": "stop_requested"}.get(kind, kind), None
    if outcome["returncode"] != 0:
        return "invalid", f"payload_exit:{outcome['returncode']}", None
    total = sum(p.lstat().st_size for p in private.rglob("*"))
    if not run.fits(total):
        return "interrupted", "output_limit", None
    expected = ("result.json", "candidate.md")
    for name in expected:
        path = private / name
        if path.is_file() and not path.is_symlink():
            run.create(name, path.read_bytes())
    try:
        result = json.loads((run.path / "result.json").read_bytes())
    except (OSError, ValueError):
        return "invalid", "result_missing_or_malformed", None
    if not all(isinstance(result, dict) and isinstance(result.get(key), dict) for key in ("safety", "validity")):
        return "invalid", "result_missing_or_malformed", None
    if result["safety"].get("ok") is not True:
        return "invalid", "isolation_breach", result
    if result["validity"].get("ok") is not True:
        return "invalid", "inputs_changed", result
    return "completed", None, result


def build_report(run, protocol, outcome, result, state, reason, started) -> dict:
    status = run.status
    product = (result or {}).get("product")
    applied = (outcome or {}).get("limits") or {}
    limitations = [
        "One initiative and one frozen observation; descriptive only, no population claim.",
        "Baseline and raw issues are sequential GitHub reads, not an atomic snapshot.",
        "The candidate renders from the frozen baseline, so it cannot state a fact the baseline lacks; "
        "it tests compaction, not better retrieval.",
        "Oracle facts cover revision, tier, state, held and open blockers only; other sequencing context "
        "(experiments, drift, owner attention) is not scored.",
        "Output volume inside the sandbox is bounded per file (RLIMIT_FSIZE) and by the memory cgroup "
        "(tmpfs pages), then checked against the output cap before retention.",
        "Loopback probes cover two known service ports; the private network namespace is the actual denial.",
        "The baseline is captured after this run's directory exists, so it may list this run as an "
        "unfinished experiment; that entry is not an oracle fact.",
    ]
    return {
        "schema_version": 1, "experiment": protocol["experiment"], "run_id": status["run_id"],
        "kind": protocol["kind"], "lineage": protocol["lineage"], "iteration": status["iteration"],
        "protocol_revision": protocol["protocol_revision"], "protocol_sha256": status["protocol_sha256"],
        "source": status.get("source"), "baseline": protocol["baseline"], "candidate": protocol["candidate"],
        "state": state, "reason": reason, "cutoff": None, "out_of_lineage": status.get("out_of_lineage"),
        "timestamps": {"started_at": status["started_at"], "frozen_at": status.get("frozen_at"),
                       "sandbox_started_at": (outcome or {}).get("started_at"), "reported_at": now()},
        "limits": status["limits"],
        "consumption": {"elapsed_seconds": round(time.monotonic() - started, 3),
                        "sandbox_seconds": (outcome or {}).get("elapsed_seconds"),
                        "output_bytes_before_report": run.used, "model_calls": 0, "provider_calls": 0},
        "observations": {
            "safety": {"limits_applied": applied.get("applied"), "limits_expected": applied.get("expected"),
                       "limits_ok": applied.get("ok"), "sandbox_outcome": (outcome or {}).get("outcome"),
                       "sandbox_returncode": (outcome or {}).get("returncode"),
                       **((result or {}).get("safety") or {})},
            "validity": (result or {}).get("validity"),
            "product": product,
        },
        "interpretation": None if state != "completed" or not product else (
            "Candidate stated every scored oracle fact (revision, tier, state, held, open blockers) on cited, "
            "resolvable lines and contradicted none; header and next-action lines are not scored."
            if product.get("criterion_met") is True else
            "Candidate did not meet the frozen criterion; see omitted/unsupported/unresolved rows."),
        "limitations": limitations,
        # Only artifacts this run actually retained: a missing link would read back as a damaged run.
        "evidence": {key: name for key, name in (
            ("inputs", "manifest.json"), ("oracle", "oracle.json"), ("baseline", "baseline.json"),
            ("issues", "issues.json"), ("candidate", "candidate.md"), ("sandbox_result", "result.json"),
            ("applied_limits", "limits.json")) if (run.path / name).is_file()},
        "proposed_amendment": None,
        "decision_owner": protocol["decision_owner"], "disposition": "pending",
        "note": "Completion is not hypothesis or production acceptance.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--output-dir", help="experiment dir holding runs (default: main root .factory/experiments/b1-build)")
    s.add_argument("--max-seconds", type=int, help="tighten the protocol elapsed limit")
    for name in ("status", "stop", "report"):
        sub.add_parser(name).add_argument("run", help="run directory")
    args = ap.parse_args(argv)
    return {"start": start, "status": o1.status, "stop": o1.stop, "report": o1.report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
