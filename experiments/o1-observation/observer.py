#!/usr/bin/env python3
"""O1 bounded observation producer (#97): read-only, stdlib-only, foreground.

start   freeze protocol + a digest-indexed snapshot of events.jsonl, then classify
status  fresh-process read of a run's status.json (plus live-writer check)
stop    cooperative stop request through the run's own artifact; idempotent
report  print report.json and re-verify every retained artifact digest

The source is opened O_RDONLY and only ever read by byte offset; nothing named
inside an event (paths, commands, logs) is opened. No subprocess, network or
model call is made. Outputs live in one exclusive run directory per start.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.json"
CHUNK = 1 << 20
TERMINAL = {"completed", "interrupted", "invalid", "inconclusive"}
IMMUTABLE = ("protocol.json", "snapshot.json", "evidence.jsonl", "report.json")


def main_root() -> Path:
    """Main checkout root, resolved without git: a worktree's `.git` file names the common dir."""
    root = HERE.parents[1]
    dotgit = root / ".git"
    if dotgit.is_file():
        gitdir = Path(dotgit.read_text().split("gitdir:", 1)[1].strip())
        if gitdir.parent.name == "worktrees":
            return gitdir.parents[2]
    return root


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_at(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else None


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- run-directory writes: create-once or atomic replace, never outside the run --

class Run:
    def __init__(self, path: Path, max_output: int, reserve: int):
        self.path, self.max_output, self.reserve = path, max_output, reserve
        self.sizes: dict[str, int] = {}
        self.status: dict = {}

    @property
    def used(self) -> int:
        return sum(self.sizes.values())

    def fits(self, extra: int) -> bool:
        return self.used + extra <= self.max_output - self.reserve

    def create(self, name: str, data: bytes) -> None:
        fd = os.open(self.path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        self.sizes[name] = len(data)

    def replace(self, name: str, data: bytes) -> None:
        tmp = self.path / f".{name}.{secrets.token_hex(6)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path / name)
        self.sizes[name] = len(data)

    def put_status(self, **fields) -> None:
        self.status.update(fields, updated_at=now())
        self.replace("status.json", dumps(self.status))


def dumps(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


class Stop(Exception):
    def __init__(self, state: str, reason: str):
        super().__init__(reason)
        self.state, self.reason = state, reason


def _check_limits(run: Run, started: float, max_seconds: float) -> None:
    if (run.path / "stop-request").exists():
        raise Stop("interrupted", "stop_requested")
    if time.monotonic() - started > max_seconds:
        raise Stop("interrupted", "time_limit")
    if run.used > run.max_output - run.reserve:
        raise Stop("interrupted", "output_limit")


def _open_source(source: Path) -> int:
    return os.open(source, os.O_RDONLY | os.O_NOFOLLOW)


# -- source freeze + parse --

def freeze(fd: int, max_input: int, check) -> dict:
    """Pick the covered byte range from the initial size and hash it in 1 MiB chunks."""
    size = os.fstat(fd).st_size
    start = max(0, size - max_input)
    end = size
    if start:  # suffix capture: drop the partial first line explicitly
        head = os.pread(fd, min(CHUNK, end - start), start - 1)
        nl = head.find(b"\n")
        cut = nl if nl >= 0 else len(head) - 1
        start = start + cut
    tail = os.pread(fd, min(CHUNK, end - start), max(start, end - CHUNK)) if end > start else b""
    partial_tail = 0
    if tail and not tail.endswith(b"\n"):
        nl = tail.rfind(b"\n")
        partial_tail = len(tail) - (nl + 1)
        end -= partial_tail
    whole, chunks, pos = hashlib.sha256(), [], start
    while pos < end:
        check()
        data = os.pread(fd, min(CHUNK, end - pos), pos)
        if not data:
            raise Stop("invalid", "source_shrank")
        whole.update(data)
        chunks.append({"offset": pos, "length": len(data), "sha256": sha256(data)})
        pos += len(data)
    return {
        "initial_size": size, "start": start, "end": end,
        "complete": start == 0 and partial_tail == 0,
        "discarded_prefix_bytes": start - max(0, size - max_input) if size > max_input else 0,
        "discarded_partial_tail_bytes": partial_tail,
        "sha256": whole.hexdigest(), "chunk_size": CHUNK, "chunks": chunks,
    }


def scan(fd: int, snap: dict, cutoff: dt.datetime, protocol: dict, check) -> dict:
    """Re-read exactly the frozen chunks (re-verifying digests) and keep only terminal/exit rows."""
    record_events = protocol["terminal"]["record_events"]
    stats = {"lines": 0, "malformed": 0, "malformed_offsets": [], "after_cutoff": 0, "untimed": 0}
    exits, signals, roots = [], [], {}
    carry, carry_at = b"", snap["start"]
    for chunk in snap["chunks"]:
        check()
        data = os.pread(fd, chunk["length"], chunk["offset"])
        if sha256(data) != chunk["sha256"]:
            raise Stop("invalid", "source_changed_after_freeze")
        buf = carry + data
        lines = buf.split(b"\n")
        carry = lines.pop()
        pos = carry_at
        for line in lines:
            _row(line, pos, stats, exits, signals, roots, record_events, protocol, cutoff)
            pos += len(line) + 1
        carry_at = pos
    if carry:
        _row(carry, carry_at, stats, exits, signals, roots, record_events, protocol, cutoff)
    return {"stats": stats, "exits": exits, "signals": signals, "roots": roots}


def _row(line, offset, stats, exits, signals, roots, record_events, protocol, cutoff):
    if not line.strip():
        return
    stats["lines"] += 1
    try:
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError
    except ValueError:
        stats["malformed"] += 1
        if len(stats["malformed_offsets"]) < 100:
            stats["malformed_offsets"].append(offset)
        return
    event = row.get("event")
    lifecycle = event == "lifecycle"
    if lifecycle and isinstance(row.get("execution_id"), str) and isinstance(row.get("root_execution_id"), str):
        roots[row["execution_id"]] = row["root_execution_id"]
    if not (event in record_events or (lifecycle and row.get("kind") == "exit")):
        return
    at = parse_at(row.get("at"))
    if at is None:
        stats["untimed"] += 1
        return
    if at > cutoff:
        stats["after_cutoff"] += 1
        return
    reason = row.get("reason")
    ref = {
        "offset": offset, "length": len(line) + 1, "line_sha256": sha256(line),
        "at": at.isoformat(), "event": event, "event_id": _s(row.get("event_id")),
        "execution_id": _s(row.get("execution_id")), "root_execution_id": _s(row.get("root_execution_id")),
        "stage": _s(row.get("stage")), "outcome": record_events.get(event) if not lifecycle else _s(row.get("outcome")),
        "ticket": row["ticket"] if type(row.get("ticket")) is int else None,
        "reason_code": code(reason, protocol), "reason_sha256": sha256(reason.encode()) if isinstance(reason, str) else None,
    }
    if lifecycle:
        exits.append(ref)
        if ref["outcome"] in protocol["terminal"]["lifecycle_outcomes"]:
            signals.append(ref)
    else:
        signals.append(ref)


def _s(value):
    return value if isinstance(value, str) else None


def code(reason, protocol):
    """Only exact structured codes survive; free text becomes None (and so `unknown`)."""
    cls = protocol["classification"]
    if not isinstance(reason, str):
        return None
    if reason in cls["reason_codes"]:
        return reason
    for prefix in cls["reason_prefixes"]:
        rest = reason[len(prefix):]
        if reason.startswith(prefix) and rest.lstrip("-").isdigit():
            return reason
    return None


def category(reason_code, protocol):
    cls = protocol["classification"]
    if reason_code in cls["reason_codes"]:
        return cls["reason_codes"][reason_code]
    for prefix, cat in cls["reason_prefixes"].items():
        if reason_code and reason_code.startswith(prefix):
            return cat
    return None


# -- cohort --

def cohort(parsed: dict, protocol: dict) -> dict:
    roots, ignore = parsed["roots"], set(protocol["classification"]["ignore_outcomes"])
    lineages: dict[tuple, list] = {}
    unknown_ticket = 0
    for sig in parsed["signals"]:
        if sig["ticket"] is None:
            unknown_ticket += 1
            continue
        root = sig["root_execution_id"] or roots.get(sig["execution_id"] or "")
        key = (sig["ticket"], root or f"ticket:{sig['ticket']}:{sig['execution_id'] or sig['offset']}")
        sig["lineage"], sig["lineage_source"] = key[1], "root_execution_id" if root else "ticket_fallback"
        lineages.setdefault(key, []).append(sig)
    by_root: dict[str, list] = {}
    for ex in parsed["exits"]:
        if ex["root_execution_id"] and ex["outcome"] not in ignore:
            by_root.setdefault(ex["root_execution_id"], []).append(ex)
    # One terminal per lineage: its last signal; nested stage exits in the lineage are not extra cases.
    # A lineage qualifies only with a dispatcher `record` row (merged/escalate); lifecycle-only terminal
    # outcomes without one are uncorroborated (e.g. fixture writes) and are counted, not sampled.
    terminals, uncorroborated = [], 0
    for (ticket, lineage), sigs in lineages.items():
        if all(s["event"] == "lifecycle" for s in sigs):
            uncorroborated += 1
            continue
        sigs.sort(key=lambda s: (s["at"], s["offset"]))
        last = sigs[-1]
        outcome = "merged" if any(s["outcome"] == "merged" for s in sigs) else "project_escalation"
        reason_codes = sorted({s["reason_code"] for s in sigs if s["reason_code"]})
        terminals.append({"ticket": ticket, "lineage": lineage, "lineage_source": last["lineage_source"],
                          "at": last["at"], "outcome": outcome, "signals": sigs,
                          "reason_key": (tuple(reason_codes), tuple(sorted({s["reason_sha256"] or "" for s in sigs})))})
    terminals.sort(key=lambda t: (t["ticket"], t["at"], t["lineage"]))
    # Retries: consecutive same-ticket terminals with identical outcome and reasons are one case.
    cases = []
    for t in terminals:
        prev = cases[-1] if cases else None
        if prev and prev["ticket"] == t["ticket"] and prev["outcome"] == t["outcome"] \
                and prev["reason_key"] == t["reason_key"]:
            prev["retries"].append(t)
            prev["at"] = t["at"]
            continue
        cases.append({**t, "retries": [t]})
    cases.sort(key=lambda c: (c["at"], c["ticket"], c["lineage"]))
    selected = cases[-protocol["cohort_size"]:]
    return {"cases": [render_case(c, by_root, protocol) for c in selected],
            "eligible_cases": len(cases), "terminal_lineages": len(terminals),
            "uncorroborated_lifecycle_lineages": uncorroborated, "signals_without_ticket": unknown_ticket}


def render_case(case, by_root, protocol):
    sigs = [s for t in case["retries"] for s in t["signals"]]
    lineage_ids = [t["lineage"] for t in case["retries"]]
    support = [e for lin in lineage_ids for e in by_root.get(lin, []) if e not in sigs]
    cats = {category(r["reason_code"], protocol) for r in sigs + support if r["reason_code"]} - {None}
    free_text = any(r["reason_sha256"] and not r["reason_code"] for r in sigs + support)
    if case["outcome"] == "merged":
        cat, basis = None, "merged: success, no intervention category"
    elif len(cats) == 1:
        cat, basis = cats.pop(), "single structured reason category in lineage"
    else:
        cat = protocol["classification"]["fallback"]
        basis = ("conflicting structured categories " + ",".join(sorted(cats))) if cats else \
            ("free-text reason only" if free_text else "no structured reason recorded")
    cite = lambda rows: [{k: r[k] for k in ("offset", "length", "line_sha256", "at", "event", "event_id",
                                           "execution_id", "stage", "outcome", "reason_code", "reason_sha256")}
                         for r in rows]
    return {
        "ticket": case["ticket"], "lineage": case["lineage"], "lineage_source": case["lineage_source"],
        "retry_lineages": lineage_ids, "retry_count": len(lineage_ids) - 1,
        "terminal_at": case["at"], "outcome": case["outcome"], "category": cat, "basis": basis,
        "terminal_signal_count": len(sigs), "supporting_exit_count": len(support),
        "terminal_signals": cite(sigs[:3] + sigs[-3:] if len(sigs) > 6 else sigs),
        "supporting_exits": cite(support[:6]),
    }


# -- commands --

def start(args) -> int:
    protocol_bytes = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_bytes)
    limits = protocol["limits"]
    max_seconds = _tighten(args.max_seconds, limits["max_seconds"])
    max_input = _tighten(args.max_input_bytes, limits["max_input_bytes"])
    max_output = _tighten(args.max_output_bytes, limits["max_output_bytes"])
    reserve = min(limits["output_reserve_bytes"], max_output // 2)
    root = main_root()
    source = Path(args.source) if args.source else root / protocol["source"]
    out = Path(args.output_dir) if args.output_dir else root / ".factory" / "experiments" / protocol["experiment"]
    out.mkdir(parents=True, exist_ok=True)
    guard = os.open(out, os.O_RDONLY)
    fcntl.flock(guard, fcntl.LOCK_EX)  # serialises iteration numbering only; not a scheduler
    try:
        prior = sorted(p.name for p in out.iterdir() if (p / "protocol.json").exists())
        iteration = len(prior) + 1
        if iteration > protocol["max_iterations"]:
            print(f"lineage {protocol['lineage']} already has {len(prior)} runs; cap is {protocol['max_iterations']}",
                  file=sys.stderr)
            return 2
        run_id = f"{protocol['experiment']}-i{iteration}-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        (out / run_id).mkdir()
        run = Run(out / run_id, max_output, reserve)
        run.create("protocol.json", protocol_bytes)
    finally:
        os.close(guard)
    lock = os.open(run.path / "writer.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    started, started_at = time.monotonic(), now()
    run.put_status(
        schema_version=1, experiment=protocol["experiment"], run_id=run_id, kind=protocol["kind"],
        lineage=protocol["lineage"], iteration=iteration, max_iterations=protocol["max_iterations"],
        prior_runs=prior, protocol_revision=protocol["protocol_revision"], protocol_sha256=sha256(protocol_bytes),
        cutoff=protocol["cutoff"], source={"path": str(source)}, state="waiting", reason=None,
        started_at=started_at, finished_at=None,
        limits={"max_seconds": max_seconds, "max_input_bytes": max_input, "max_output_bytes": max_output,
                "output_reserve_bytes": reserve, "model_calls": 0, "provider_calls": 0, "subprocess_workers": 0},
    )
    check = lambda: _check_limits(run, started, max_seconds)
    snap = parsed = result = None
    state, reason = "invalid", None
    fd = None
    try:
        check()
        try:
            fd = _open_source(source)
        except FileNotFoundError:
            raise Stop("invalid", "source_missing")
        except OSError as exc:
            raise Stop("invalid", f"source_unreadable:{exc.errno}")
        run.put_status(state="observing")
        snap = freeze(fd, max_input, check)
        run.create("snapshot.json", dumps({"source_path": str(source), **snap}))
        run.put_status(source={"path": str(source), "sha256": snap["sha256"], "initial_size": snap["initial_size"],
                               "covered": [snap["start"], snap["end"]], "complete": snap["complete"]},
                       frozen_at=now())
        parsed = scan(fd, snap, dt.datetime.fromisoformat(protocol["cutoff"]), protocol, check)
        result = cohort(parsed, protocol)
        retain_evidence(run, fd, result["cases"], check)
        n = len(result["cases"])
        state = "completed" if n == protocol["cohort_size"] else "inconclusive"
        reason = None if state == "completed" else ("no_qualifying_cases" if n == 0 else "fewer_cases_than_protocol")
    except Stop as stop:
        state, reason = stop.state, stop.reason
    finally:
        if fd is not None:
            os.close(fd)
    report = build_report(run, protocol, snap, parsed, result, state, reason, started)
    run.create("report.json", dumps(report))
    artifacts = {name: sha256((run.path / name).read_bytes()) for name in IMMUTABLE if (run.path / name).exists()}
    run.put_status(state=state, reason=reason, finished_at=now(), artifacts=artifacts,
                   consumption={"elapsed_seconds": round(time.monotonic() - started, 3),
                                "input_bytes": (snap["end"] - snap["start"]) if snap else 0,
                                "output_bytes": run.used})
    os.close(lock)
    print(json.dumps({"run": str(run.path), "state": state, "reason": reason}))
    return 0


def _tighten(value, ceiling):
    if value is None:
        return ceiling
    if value < 0 or value > ceiling:
        raise SystemExit(f"limit {value} outside 0..{ceiling} (protocol ceiling)")
    return value


def retain_evidence(run: Run, fd: int, cases: list, check) -> None:
    """Copy only cited lines (hash-verified) into evidence.jsonl; stop at the output budget."""
    seen, lines = set(), []
    for case in cases:
        for ref in case["terminal_signals"] + case["supporting_exits"]:
            if ref["offset"] in seen:
                continue
            seen.add(ref["offset"])
            check()
            raw = os.pread(fd, ref["length"], ref["offset"]).rstrip(b"\n")
            if sha256(raw) != ref["line_sha256"]:
                raise Stop("invalid", "source_changed_after_freeze")
            if not run.fits(sum(map(len, lines)) + len(raw) + 1):
                run.create("evidence.jsonl", b"".join(lines))
                raise Stop("interrupted", "output_limit")
            lines.append(raw + b"\n")
    run.create("evidence.jsonl", b"".join(lines))


def build_report(run, protocol, snap, parsed, result, state, reason, started) -> dict:
    status = run.status
    cases = result["cases"] if result else []
    counts: dict[str, int] = {}
    for c in cases:
        key = c["category"] or c["outcome"]
        counts[key] = counts.get(key, 0) + 1
    limitations = [
        "Classification uses only exact structured reason codes from protocol.json; free text stays unknown.",
        "Consecutive identical escalations of one ticket are grouped as retries of one case; cross-lineage identity is otherwise not reconstructed.",
        "Merge-pass and worker executions have different roots; a merged case is the merge lineage only.",
        "Lifecycle-only merged/project_escalation lineages without a dispatcher record row are excluded as uncorroborated (counted in observations).",
        "Historical correlation only: no causality and no population accuracy rate is claimed.",
        "Missing events are not evidence that no intervention happened.",
    ]
    if snap and not snap["complete"]:
        limitations.append("Source coverage is incomplete (bounded suffix and/or discarded partial boundary line).")
    if cases and len(cases) < protocol["cohort_size"]:
        limitations.append(f"Only {len(cases)} of {protocol['cohort_size']} cases qualified; cohort is limited.")
    if any(c["lineage_source"] == "ticket_fallback" for c in cases):
        limitations.append("Some cases lack execution identity and use ticket-level fallback grouping.")
    return {
        "schema_version": 1, "experiment": protocol["experiment"], "run_id": status["run_id"],
        "kind": protocol["kind"], "lineage": protocol["lineage"], "iteration": status["iteration"],
        "protocol_revision": protocol["protocol_revision"], "protocol_sha256": status["protocol_sha256"],
        "source": {
            "revision": "events.jsonl byte range", "sha256": snap and snap["sha256"],
            "initial_size": snap and snap["initial_size"], "covered": snap and [snap["start"], snap["end"]],
            "complete": bool(snap and snap["complete"]),
            "discarded_prefix_bytes": snap and snap["discarded_prefix_bytes"],
            "discarded_partial_tail_bytes": snap and snap["discarded_partial_tail_bytes"],
            "snapshot": "snapshot.json (chunk digests; source bytes are not copied)",
        },
        "baseline": protocol["baseline"], "candidate": protocol["candidate"],
        "state": state, "reason": reason, "cutoff": protocol["cutoff"],
        "timestamps": {"started_at": status["started_at"], "frozen_at": status.get("frozen_at"), "reported_at": now()},
        "limits": status["limits"],
        "consumption": {"elapsed_seconds": round(time.monotonic() - started, 3),
                        "input_bytes": (snap["end"] - snap["start"]) if snap else 0,
                        "output_bytes_before_report": run.used, "model_calls": 0, "provider_calls": 0,
                        "subprocess_workers": 0},
        "observations": {
            "ledger": parsed["stats"] if parsed else None,
            "eligible_cases": result and result["eligible_cases"],
            "terminal_lineages": result and result["terminal_lineages"],
            "signals_without_ticket": result and result["signals_without_ticket"],
            "uncorroborated_lifecycle_lineages": result and result["uncorroborated_lifecycle_lineages"],
            "category_counts": counts, "cases": cases,
        },
        "interpretation": None if state != "completed" else
            "Descriptive counts over the frozen cohort only; the managing agent decides disposition.",
        "limitations": limitations,
        "evidence": {"source_offsets": "cases[].terminal_signals/supporting_exits", "retained_lines": "evidence.jsonl",
                     "snapshot": "snapshot.json"},
        "proposed_amendment": None,
        "decision_owner": protocol["decision_owner"], "disposition": "pending",
        "note": "Completion is not hypothesis or production acceptance.",
    }


def _load_status(run_dir: Path) -> dict:
    return json.loads((run_dir / "status.json").read_bytes())


def _writer_active(run_dir: Path) -> bool:
    try:
        fd = os.open(run_dir / "writer.lock", os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def status(args) -> int:
    run_dir = Path(args.run)
    print(json.dumps({**_load_status(run_dir), "writer_active": _writer_active(run_dir)}, indent=2, sort_keys=True))
    return 0


def stop(args) -> int:
    run_dir = Path(args.run)
    current = _load_status(run_dir)
    if current["state"] in TERMINAL:
        print(json.dumps({"run": str(run_dir), "state": current["state"], "stop": "already_terminal"}))
        return 0
    try:
        os.close(os.open(run_dir / "stop-request", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444))
    except FileExistsError:
        pass
    if not _writer_active(run_dir):  # writer gone: record the interruption ourselves, keep all evidence
        run = Run(run_dir, 0, 0)
        run.status = current
        run.put_status(state="interrupted", reason="stopped_without_live_writer", finished_at=now())
    print(json.dumps({"run": str(run_dir), "state": _load_status(run_dir)["state"], "stop": "requested"}))
    return 0


def report(args) -> int:
    run_dir = Path(args.run)
    current = _load_status(run_dir)
    recorded = current.get("artifacts") or {}
    verified = {name: sha256((run_dir / name).read_bytes()) == digest for name, digest in recorded.items()}
    rep = json.loads((run_dir / "report.json").read_bytes()) if (run_dir / "report.json").exists() else None
    ok = bool(recorded) and all(verified.values())
    print(json.dumps({"state": current["state"], "verified": verified, "all_verified": ok, "report": rep},
                     indent=2, sort_keys=True))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--source", help="events.jsonl (default: main root .factory/events.jsonl)")
    s.add_argument("--output-dir", help="experiment dir holding runs (default: main root .factory/experiments/o1-observation)")
    s.add_argument("--max-seconds", type=float, help="tighten protocol limit")
    s.add_argument("--max-input-bytes", type=int, help="tighten protocol limit")
    s.add_argument("--max-output-bytes", type=int, help="tighten protocol limit")
    for name in ("status", "stop", "report"):
        sub.add_parser(name).add_argument("run", help="run directory")
    args = ap.parse_args(argv)
    return {"start": start, "status": status, "stop": stop, "report": report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
