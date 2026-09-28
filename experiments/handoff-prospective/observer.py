#!/usr/bin/env python3
"""Passive prospective handoff observer; no production mutations or model calls."""
from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import time
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROTOCOL = HERE / "PROTOCOL.md"
ADDENDUM = HERE / "PROTOCOL-NAMED-PAIR.md"
EVENTS_REL = Path(".factory/events.jsonl")
REPO = "mikeroySoft/factory"
PRODUCER = 6
CONSUMER = 7
MAX_RESPONSE = 5 * 1024 * 1024
MAX_SOURCE = 20 * 1024 * 1024
Fetcher = Callable[[str, Path, str], tuple[bytes | None, dict]]


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return calendar.timegm(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\x00" not in value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def write_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as out:
            out.write(data)
        path.chmod(0o444)
    except FileExistsError:
        if path.read_bytes() != data:
            raise RuntimeError(f"immutable evidence collision: {path}")




def labels(issue: dict) -> set[str]:
    return {item.get("name", "") for item in issue.get("labels", []) if isinstance(item, dict)}


def worker_outcome(row: dict) -> bool:
    return row.get("event") == "attempt" or (
        row.get("event") == "lifecycle"
        and row.get("stage") == "worker"
        and row.get("kind") in {"result", "exit"}
    )


class Observer:
    def __init__(
        self,
        root: Path = ROOT,
        live: Path | None = None,
        protocol: Path = PROTOCOL,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.root = root.resolve()
        self.live = (live or (HERE / "live")).resolve()
        self.protocols = (
            (protocol.resolve(), ADDENDUM.resolve())
            if protocol.resolve() == PROTOCOL.resolve()
            else (protocol.resolve(),)
        )
        self.events = self.root / EVENTS_REL
        self.fetcher = fetcher or self._gh_get
        self.state_path = self.live / "state.json"
        self.status_path = self.live / "status.json"
        self.state: dict = {}

    def _protocol_manifest(self) -> tuple[list[dict], str]:
        records = []
        bound = bytearray()
        for path in self.protocols:
            data = path.read_bytes()
            records.append({"path": str(path), "bytes": len(data), "sha256": sha(data)})
            bound.extend(path.name.encode() + b"\0" + data)
        return records, sha(bytes(bound))

    def _status(self, phase: str, **extra: object) -> None:
        atomic_json(
            self.status_path,
            {"phase": phase, "observed_at": utc(), "model_calls": 0, **extra},
        )

    def _record(self, kind: str, **fields: object) -> None:
        row = {"at": utc(), "kind": kind, **fields}
        number = self.state.get("observation_count", 0) + 1
        self.state["observation_count"] = number
        write_once(
            self.live / "observations" / f"{number:06d}-{kind}.json",
            (json.dumps(row, sort_keys=True) + "\n").encode(),
        )

    def enroll(self) -> None:
        protocol_records, protocol_set_sha = self._protocol_manifest()
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            expected = self.state["enrollment"]["protocol_set_sha256"]
            if protocol_set_sha != expected:
                raise RuntimeError("frozen protocol set changed after enrollment")
            self._freeze_pair()
            self._status(self.state.get("phase", "READY_WAITING"), resumed=True)
            print(f"READY resumed offset={self.state['offset']}", flush=True)
            return
        baseline = self._freeze_pair()
        with self.events.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            boundary_size = stat.st_size
            data = stream.read(boundary_size)
        complete_end = data.rfind(b"\n") + 1
        prior_claims: set[int] = set()
        last_event_id = None
        last_line = b""
        for raw in data[:complete_end].splitlines(keepends=True):
            last_line = raw
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            # Nested gate tests emit lifecycle rows too; only claims establish ticket admission.
            if row.get("event") == "claimed" and isinstance(row.get("ticket"), int):
                prior_claims.add(row["ticket"])
            if row.get("event_id"):
                last_event_id = row["event_id"]
        if {PRODUCER, CONSUMER} & prior_claims:
            raise RuntimeError("named pair already started; fresh enrollment forbidden")
        opened = utc()
        enrollment = {
            "opened_at": opened,
            "protocols": protocol_records,
            "protocol_set_sha256": protocol_set_sha,
            "events_path": str(self.events),
            "events_device": stat.st_dev,
            "events_inode": stat.st_ino,
            "boundary_size": boundary_size,
            "last_complete_offset": complete_end,
            "last_complete_line_sha256": sha(last_line) if last_line else None,
            "last_complete_event_id": last_event_id,
            "preboundary_partial_bytes": boundary_size - complete_end,
            "preboundary_partial_sha256": sha(data[complete_end:]) if complete_end < boundary_size else None,
            "historically_claimed_tickets": sorted(prior_claims),
            "producer": PRODUCER,
            "consumer": CONSUMER,
            "baseline_sha256": sha((self.live / "baseline/summary.json").read_bytes()),
            "baseline_completed_at": baseline["captured_completed_at"],
        }
        write_once(
            self.live / "enrollment.json",
            (json.dumps(enrollment, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.state = {
            "version": 1,
            "phase": "READY_WAITING",
            "enrollment": enrollment,
            "ledger_device": stat.st_dev,
            "ledger_inode": stat.st_ino,
            "offset": boundary_size,
            "pending_b64": "",
            "pending_start": boundary_size,
            "discard_pending_line": boundary_size != complete_end,
            "future_event_ids": [],
            "future_line_hashes": [],
            "observation_count": 0,
            "candidates": [],
            "selected": None,
        }
        atomic_json(self.state_path, self.state)
        self._status("READY_WAITING", enrollment=opened, boundary_size=boundary_size)
        print(
            f"READY enrollment={opened} boundary={boundary_size} last_event_id={last_event_id}",
            flush=True,
        )

    def _gh_get(self, endpoint: str, destination: Path, label: str) -> tuple[bytes | None, dict]:
        argv = [
            "gh", "api", "--method", "GET", "-H", "Accept: application/vnd.github+json", endpoint
        ]
        started = utc()
        before = time.monotonic()
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=45, cwd=self.root)
            body, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            body = exc.stdout or b""
            stderr = exc.stderr or b""
            code = None
        ended = utc()
        meta = {
            "label": label,
            "endpoint": endpoint,
            "argv": argv,
            "started_at": started,
            "ended_at": ended,
            "seconds": round(time.monotonic() - before, 3),
            "returncode": code,
            "bytes": len(body),
            "sha256": sha(body),
            "stderr_bytes": len(stderr),
            "stderr_sha256": sha(stderr),
            "complete": code == 0 and len(body) <= MAX_RESPONSE,
            "limit_bytes": MAX_RESPONSE,
        }
        raw_dir = destination / "raw"
        write_once(raw_dir / f"{label}.response", body)
        write_once(raw_dir / f"{label}.stderr", stderr)
        write_once(
            raw_dir / f"{label}.meta.json",
            (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode(),
        )
        return (body if meta["complete"] else None), meta

    def _fetch_json(self, endpoint: str, destination: Path, label: str) -> tuple[object | None, dict]:
        body, meta = self.fetcher(endpoint, destination, label)
        if body is None:
            return None, meta
        try:
            return json.loads(body), meta
        except (ValueError, UnicodeDecodeError):
            return None, {**meta, "complete": False, "parse_error": "invalid JSON"}

    def _candidate_dir(self, ticket: int, claim_sha: str) -> Path:
        return self.live / "candidates" / f"{ticket}-{claim_sha[:12]}"

    def _save_event(self, row: dict, raw: bytes, line_hash: str) -> None:
        ticket = row.get("ticket")
        selected = self.state.get("selected")
        interested = row.get("event") == "claimed" or (selected and ticket == selected["ticket"])
        if not interested:
            return
        directory = (
            Path(selected["directory"]) if selected and ticket == selected["ticket"]
            else self._candidate_dir(ticket if isinstance(ticket, int) else 0, line_hash)
        )
        event_id = str(row.get("event_id") or line_hash[:16])
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", event_id)
        write_once(directory / "events" / f"{safe_id}.jsonl", raw)

    def _freeze_pair(self) -> dict:
        destination = self.live / "baseline"
        manifest_path = destination / "summary.json"
        if manifest_path.exists():
            baseline = json.loads(manifest_path.read_text())
            if self.state:
                if sha(manifest_path.read_bytes()) != self.state["enrollment"]["baseline_sha256"]:
                    raise RuntimeError("frozen baseline manifest changed")
            for name, digest in baseline["files"].items():
                if sha((destination / name).read_bytes()) != digest:
                    raise RuntimeError(f"frozen baseline changed: {name}")
            return baseline
        issues = {}
        for ticket in (5, PRODUCER, CONSUMER):
            issue, _ = self._fetch_json(
                f"repos/{REPO}/issues/{ticket}", destination, f"issue-{ticket}"
            )
            comments, _ = self._fetch_json(
                f"repos/{REPO}/issues/{ticket}/comments?per_page=100&page=1",
                destination, f"comments-{ticket}",
            )
            if not isinstance(issue, dict) or not isinstance(comments, list) or len(comments) >= 100:
                raise RuntimeError(f"incomplete baseline for #{ticket}")
            issues[str(ticket)] = issue
            if ticket in (PRODUCER, CONSUMER):
                if issue.get("state") != "open" or "ready-for-agent" not in labels(issue):
                    raise RuntimeError(f"#{ticket} is not open authorized intake")
                pulls, _ = self._fetch_json(
                    f"repos/{REPO}/pulls?state=all&head=mikeroySoft:agent/{ticket}&per_page=1",
                    destination, f"prior-pulls-{ticket}",
                )
                if pulls != []:
                    raise RuntimeError(f"#{ticket} has prior PR work or unavailable history")
        for child, parent in ((PRODUCER, 5), (CONSUMER, PRODUCER)):
            if not re.search(rf"(?im)^Blocked by:\s*#{parent}\s*$", issues[str(child)].get("body") or ""):
                raise RuntimeError(f"missing declared dependency #{child} -> #{parent}")
        main, _ = self._fetch_json(
            f"repos/{REPO}/commits/main", destination, "default-branch-commit"
        )
        if not isinstance(main, dict) or not re.fullmatch(r"[0-9a-f]{40}", main.get("sha", "")):
            raise RuntimeError("missing baseline main revision")
        baseline = {
            "producer": PRODUCER,
            "consumer": CONSUMER,
            "issues": issues,
            "captured_completed_at": utc(),
            "default_branch_sha": main["sha"],
            "files": {
                str(path.relative_to(destination)): sha(path.read_bytes())
                for path in sorted((destination / "raw").iterdir())
            },
        }
        write_once(manifest_path, (json.dumps(baseline, indent=2, sort_keys=True) + "\n").encode())
        return baseline

    def _capture_candidate(self, row: dict, raw: bytes, line_hash: str) -> None:
        ticket = row.get("ticket")
        if ticket != PRODUCER:
            return
        baseline = self._freeze_pair()
        destination = self._candidate_dir(ticket, line_hash)
        write_once(destination / "claim.jsonl", raw)
        reasons = []
        if (parse_time(row.get("at")) or 0) <= parse_time(baseline["captured_completed_at"]):
            reasons.append("claim_not_strictly_after_frozen_baseline")
        if "ready-for-agent" not in (row.get("labels") or []):
            reasons.append("claim_lacks_authorized_intake")
        for number in (PRODUCER, CONSUMER):
            current, _ = self._fetch_json(
                f"repos/{REPO}/issues/{number}", destination / "claim-status", f"issue-{number}"
            )
            frozen = baseline["issues"][str(number)]
            if not isinstance(current, dict) or any(
                current.get(key) != frozen.get(key) for key in ("id", "title", "body")
            ):
                reasons.append(f"frozen_contract_changed_{number}")
        main, _ = self._fetch_json(
            f"repos/{REPO}/commits/main", destination / "claim-status", "default-branch-commit"
        )
        if not isinstance(main, dict) or not re.fullmatch(r"[0-9a-f]{40}", main.get("sha", "")):
            reasons.append("missing_claim_main")
        summary = {
            "frozen_baseline": str(self.live / "baseline/summary.json"),
            "frozen_baseline_sha256": self.state["enrollment"]["baseline_sha256"],
            "captured_completed_at": baseline["captured_completed_at"],
            "default_branch_sha": main.get("sha") if isinstance(main, dict) else None,
        }
        write_once(destination / "baseline/summary.json", (json.dumps(summary, sort_keys=True) + "\n").encode())
        self._finish_candidate(destination, ticket, row.get("at"), not reasons, reasons, summary)
        if reasons:
            self.state["phase"] = "CAPTURE_FAILED"
            self._status("CAPTURE_FAILED", ticket=ticket, reasons=reasons)
            print(f"CAPTURE_FAILED ticket={ticket}", flush=True)
            return
        self.state["selected"] = {
            "ticket": ticket,
            "consumer": CONSUMER,
            "claim_at": row["at"],
            "claim_sha256": line_hash,
            "directory": str(destination),
            "baseline_completed_at": baseline["captured_completed_at"],
            "attempt": None,
            "last_handoff_sha256": None,
            "last_worker_handoff_sha256": None,
            "review_binding": None,
            "acceptance_processed": False,
            "manual_context_qualification": "pending",
        }
        self.state["phase"] = "OBSERVING_SELECTED"
        self._status("OBSERVING_SELECTED", ticket=ticket, consumer=CONSUMER, directory=str(destination))
        print(f"SELECTED ticket={ticket} consumer={CONSUMER} directory={destination}", flush=True)

    def _finish_candidate(
        self,
        destination: Path,
        ticket: int,
        claim_at: str | None,
        eligible: bool,
        reasons: list[str],
        baseline: dict | None = None,
    ) -> None:
        decision = {
            "ticket": ticket,
            "claim_at": claim_at,
            "decided_at": utc(),
            "capture_eligible": eligible,
            "selection": "user-nominated producer #6 and consumer #7",
            "manual_context_qualification_required_before_trials": eligible,
            "reasons": reasons,
            "selection_did_not_inspect_handoff": True,
            "baseline_summary_sha256": sha(json.dumps(baseline, sort_keys=True).encode()) if baseline else None,
        }
        write_once(
            destination / "eligibility.json",
            (json.dumps(decision, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.state["candidates"].append({**decision, "directory": str(destination)})
        if not eligible:
            self._record("candidate_excluded", ticket=ticket, reasons=reasons, directory=str(destination))
            print(f"EXCLUDED ticket={ticket} reasons={','.join(reasons)}", flush=True)

    def _git(self, wt: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        argv = ["git", *args]
        try:
            return subprocess.run(argv, cwd=wt, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(argv, 127, b"", str(exc).encode())

    def _head(self, wt: Path) -> str | None:
        proc = self._git(wt, "rev-parse", "HEAD")
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def _snapshot_handoff(self, corroborating: dict | None = None) -> dict | None:
        selected = self.state["selected"]
        ticket = selected["ticket"]
        wt = self.root / ".factory" / f"wt-{ticket}"
        directory = Path(selected["directory"]) / "handoff"
        event_id = corroborating.get("event_id") if corroborating else None
        event_hash = corroborating.get("_line_sha256") if corroborating else None
        if corroborating:
            observation_key = re.sub(r"[^A-Za-z0-9_.-]", "_", str(event_id or event_hash))
            observation_path = directory / "observations" / f"{observation_key}.json"
            if observation_path.exists():
                try:
                    meta = json.loads(observation_path.read_text())
                    digest = meta["sha256"]
                    object_path = directory / "objects" / f"{digest}.bin"
                    data = object_path.read_bytes()
                    if not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(data) != digest or len(data) != meta["bytes"]:
                        raise ValueError("saved object hash/size mismatch")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.state["phase"] = "CAPTURE_FAILED"
                    self._status("CAPTURE_FAILED", ticket=ticket, reason=f"saved handoff observation invalid: {exc}")
                    return None
                selected["last_handoff_sha256"] = digest
                selected["last_handoff_path"] = str(object_path)
                if meta.get("completeness") == "corroborated_after_worker_completion":
                    selected["last_worker_handoff_sha256"] = digest
                    selected["last_worker_handoff_path"] = str(object_path)
                return meta

        path = wt / ".factory" / f"handoff-{ticket}.md"
        try:
            if path.is_symlink() or not path.is_file():
                self._record("handoff_missing", ticket=ticket, path=str(path))
                return None
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
        except OSError as exc:
            self._record("handoff_disappeared", ticket=ticket, error=str(exc))
            return None
        stable = (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size == len(data)
            and before.st_mtime_ns == after.st_mtime_ns
        )
        if not stable:
            self._record("handoff_unstable", ticket=ticket, bytes=len(data))
            return None
        digest = sha(data)
        observed = utc()
        complete = bool(corroborating and worker_outcome(corroborating))
        object_path = directory / "objects" / f"{digest}.bin"
        write_once(object_path, data)
        observation_key = str(event_id or event_hash or f"poll-{digest}")
        observation_key = re.sub(r"[^A-Za-z0-9_.-]", "_", observation_key)
        meta = {
            "ticket": ticket,
            "attempt": selected.get("attempt"),
            "head": self._head(wt),
            "observed_at": observed,
            "bytes": len(data),
            "sha256": digest,
            "object_path": str(object_path),
            "file_device": before.st_dev,
            "file_inode": before.st_ino,
            "file_mtime_ns": before.st_mtime_ns,
            "completeness": "corroborated_after_worker_completion" if complete else "unproven",
            "corroborating_event_id": event_id,
            "corroborating_event_line_sha256": event_hash,
        }
        observation_path = directory / "observations" / f"{observation_key}.json"
        if corroborating or not observation_path.exists():
            write_once(
                observation_path,
                (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode(),
            )
        selected["last_handoff_sha256"] = digest
        selected["last_handoff_path"] = str(object_path)
        if complete:
            selected["last_worker_handoff_sha256"] = digest
            selected["last_worker_handoff_path"] = str(object_path)
        return meta

    def _freeze_source(self, acceptance_row: dict) -> tuple[dict | None, str | None]:
        selected = self.state["selected"]
        ticket = selected["ticket"]
        case = Path(selected["directory"])
        wt = self.root / ".factory" / f"wt-{ticket}"
        head = self._head(wt)
        try:
            baseline = json.loads((case / "baseline/summary.json").read_text())
            claim_main = baseline["default_branch_sha"]
        except (OSError, ValueError, KeyError, TypeError):
            return None, "missing claim-time default branch"
        if not head or not isinstance(claim_main, str):
            return None, "missing exact head or claim-time base"
        merge_base = self._git(wt, "merge-base", head, claim_main)
        if merge_base.returncode:
            return None, "claim-time default branch is not comparable with accepted head"
        base = merge_base.stdout.decode().strip()
        name_status = self._git(wt, "diff", "--name-status", "-z", "--find-renames", f"{base}..{head}")
        patch = self._git(wt, "diff", "--binary", "--full-index", f"{base}..{head}")
        if name_status.returncode or patch.returncode:
            return None, "git diff failed"
        parts = name_status.stdout.split(b"\0")
        paths: list[str] = []
        i = 0
        while i < len(parts) and parts[i]:
            status = parts[i].decode(errors="strict")
            count = 2 if status.startswith(("R", "C")) else 1
            for raw_path in parts[i + 1:i + 1 + count]:
                value = raw_path.decode(errors="strict")
                if not safe_path(value):
                    return None, f"unsafe changed path: {value!r}"
                paths.append(value)
            i += 1 + count
        total = len(name_status.stdout) + len(patch.stdout)
        blobs: list[dict] = []
        blob_bytes: list[tuple[str, bytes]] = []
        for path in sorted(set(paths)):
            proc = self._git(wt, "show", f"{head}:{path}")
            if proc.returncode:
                blobs.append({"path": path, "present_at_head": False})
                continue
            total += len(proc.stdout)
            if total > MAX_SOURCE:
                return None, f"exact source exceeds {MAX_SOURCE} byte cap"
            blob_name = f"{len(blob_bytes):04d}-{sha(path.encode())[:16]}.blob"
            blob_bytes.append((blob_name, proc.stdout))
            blobs.append({
                "path": path,
                "present_at_head": True,
                "file": blob_name,
                "bytes": len(proc.stdout),
                "sha256": sha(proc.stdout),
            })
        source = case / "source"
        write_once(source / "name-status.z", name_status.stdout)
        write_once(source / "exact.diff", patch.stdout)
        for name, data in blob_bytes:
            write_once(source / "blobs" / name, data)
        manifest = {
            "ticket": ticket,
            "captured_at": utc(),
            "claim_main": claim_main,
            "base": base,
            "head": head,
            "acceptance_event_id": acceptance_row.get("event_id"),
            "acceptance_event_line_sha256": acceptance_row.get("_line_sha256"),
            "name_status_sha256": sha(name_status.stdout),
            "diff_sha256": sha(patch.stdout),
            "total_captured_bytes": total,
            "cap_bytes": MAX_SOURCE,
            "blobs": blobs,
            "complete": True,
        }
        write_once(
            source / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        return manifest, None

    def _accept(self, row: dict) -> None:
        selected = self.state["selected"]
        if selected.get("acceptance_processed"):
            return
        selected["acceptance_processed"] = True
        ticket = selected["ticket"]
        case = Path(selected["directory"])
        wt = self.root / ".factory" / f"wt-{ticket}"
        current_head = self._head(wt)
        review = selected.get("review_binding")
        approval_head = row.get("head")
        approval_heads = {row.get("head"), row.get("gate_head"), row.get("review_head")}
        reasons = []
        if (
            not isinstance(review, dict)
            or review.get("head") != approval_head
            or review.get("dispatcher_run_id") != row.get("dispatcher_run_id")
        ):
            reasons.append("missing or mismatched bound independent review APPROVE")
        if (
            not isinstance(approval_head, str)
            or re.fullmatch(r"[0-9a-f]{40}", approval_head) is None
            or approval_heads != {approval_head}
            or current_head != approval_head
            or not isinstance(row.get("pr"), int)
        ):
            reasons.append("approved transition is not bound to current head, gate, review and PR")
        now_meta = self._snapshot_handoff()
        final_sha = now_meta.get("sha256") if now_meta else None
        if not final_sha or final_sha != selected.get("last_worker_handoff_sha256"):
            reasons.append("final handoff not corroborated unchanged after worker completion")
        source_manifest = None
        if not reasons:
            source_manifest, source_error = self._freeze_source(row)
            if source_error:
                reasons.append(source_error)
        pulls, pull_meta = self._fetch_json(
            f"repos/{REPO}/pulls?state=all&head=mikeroySoft:agent/{ticket}&per_page=5",
            case / "acceptance", "pull-metadata",
        )
        matching_pr = next(
            (
                pull for pull in pulls
                if isinstance(pull, dict)
                and pull.get("number") == row.get("pr")
                and isinstance(pull.get("head"), dict)
                and pull["head"].get("sha") == approval_head
            ),
            None,
        ) if isinstance(pulls, list) else None
        if matching_pr is None:
            reasons.append("captured PR identity/head does not match approved transition")
        acceptance = {
            "ticket": ticket,
            "observed_at": utc(),
            "accepted_for_capture": not reasons,
            "ready_for_trials": False,
            "context_qualification": "pending Main review of frozen baseline only",
            "independent_acceptance": "head-bound Factory reviewer APPROVE plus separate head-bound approved transition",
            "owner_acceptance": "pending",
            "merge": "not established by acceptance",
            "installation": "not established",
            "outcome_delivery": "not established",
            "approved_binding": {
                "pr": row.get("pr"),
                "head": approval_head,
                "dispatcher_run_id": row.get("dispatcher_run_id"),
                "review_event_line_sha256": review.get("line_sha256") if isinstance(review, dict) else None,
                "approval_event_line_sha256": row.get("_line_sha256"),
            },
            "reasons": reasons,
            "final_handoff_sha256": final_sha,
            "source_manifest_sha256": sha(json.dumps(source_manifest, sort_keys=True).encode()) if source_manifest else None,
            "pull_metadata": pull_meta,
        }
        write_once(
            case / "acceptance" / "decision.json",
            (json.dumps(acceptance, indent=2, sort_keys=True) + "\n").encode(),
        )
        self.state["phase"] = "READY_FOR_CONTEXT_REVIEW" if not reasons else "CAPTURE_FAILED"
        self._status(self.state["phase"], ticket=ticket, directory=str(case), reasons=reasons)
        print(f"{self.state['phase']} ticket={ticket}", flush=True)

    def _selected_event(self, row: dict) -> None:
        selected = self.state["selected"]
        if row.get("ticket") != selected["ticket"]:
            return
        event = row.get("event")
        if event == "attempt" and isinstance(row.get("attempt"), int):
            selected["attempt"] = row["attempt"]
        elif event == "lifecycle" and row.get("stage") == "worker" and isinstance(row.get("attempt"), int):
            selected["attempt"] = row["attempt"]
        if worker_outcome(row):
            selected["review_binding"] = None
            self._snapshot_handoff(row)
        if event == "review":
            head = row.get("head")
            if (
                row.get("verdict") == "APPROVE"
                and row.get("parsed") is True
                and row.get("accepted") is True
                and isinstance(head, str)
                and re.fullmatch(r"[0-9a-f]{40}", head)
                and row.get("actual_head") == head
            ):
                selected["review_binding"] = {
                    "head": head,
                    "dispatcher_run_id": row.get("dispatcher_run_id"),
                    "execution_id": row.get("execution_id"),
                    "line_sha256": row.get("_line_sha256"),
                }
            else:
                selected["review_binding"] = None
        elif event == "approved":
            self._accept(row)
        elif event == "escalate":
            case = Path(selected["directory"])
            outcome = {
                "ticket": selected["ticket"],
                "observed_at": utc(),
                "outcome": "escalated",
                "reason": row.get("reason"),
                "worker_exit_is_success": False,
                "gate_is_acceptance": False,
                "replacement_case_allowed": False,
            }
            write_once(case / "outcome.json", (json.dumps(outcome, indent=2, sort_keys=True) + "\n").encode())
            self.state["phase"] = "SELECTED_ESCALATED"
            self._status("SELECTED_ESCALATED", ticket=selected["ticket"], directory=str(case))
            print(f"SELECTED_ESCALATED ticket={selected['ticket']}", flush=True)

    def _process_line(self, raw: bytes, start: int) -> None:
        line_hash = sha(raw)
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            self._record("invalid_jsonl", start_offset=start, bytes=len(raw), sha256=line_hash, error=str(exc))
            return
        when = parse_time(row.get("at"))
        if when is None or when < parse_time(self.state["enrollment"]["opened_at"]):
            return
        if self.state["phase"] not in {"READY_WAITING", "OBSERVING_SELECTED"}:
            return
        if row.get("ticket") == CONSUMER and row.get("event") == "claimed":
            self.state["phase"] = "CAPTURE_FAILED"
            self._status("CAPTURE_FAILED", reasons=["consumer_started_before_capture_completed"])
            print("CAPTURE_FAILED consumer started before capture completed", flush=True)
            return
        if row.get("ticket") != PRODUCER:
            return
        selected = self.state.get("selected")
        if not selected and row.get("event") != "claimed":
            return
        event_id = row.get("event_id")
        if event_id and event_id in self.state["future_event_ids"]:
            return
        if not event_id and line_hash in self.state["future_line_hashes"]:
            return
        if event_id:
            self.state["future_event_ids"].append(event_id)
        else:
            self.state["future_line_hashes"].append(line_hash)
        row["_line_sha256"] = line_hash
        row["_start_offset"] = start
        self._save_event(row, raw, line_hash)
        if selected:
            self._selected_event(row)
        else:
            self._capture_candidate(row, raw, line_hash)

    def tick(self) -> None:
        try:
            stat = self.events.stat()
        except OSError as exc:
            self._record("ledger_missing", error=str(exc))
            return
        replaced = stat.st_dev != self.state["ledger_device"] or stat.st_ino != self.state["ledger_inode"]
        shrunk = stat.st_size < self.state["offset"]
        if replaced or shrunk:
            self._record(
                "ledger_reset",
                replaced=replaced,
                shrunk=shrunk,
                old_device=self.state["ledger_device"],
                old_inode=self.state["ledger_inode"],
                old_offset=self.state["offset"],
                new_device=stat.st_dev,
                new_inode=stat.st_ino,
                new_size=stat.st_size,
            )
            self.state["ledger_device"] = stat.st_dev
            self.state["ledger_inode"] = stat.st_ino
            self.state["offset"] = 0
            self.state["pending_b64"] = ""
            self.state["pending_start"] = 0
            self.state["discard_pending_line"] = False
        offset = self.state["offset"]
        with self.events.open("rb") as stream:
            stream.seek(offset)
            data = stream.read()
        if not data:
            if self.state.get("selected") and self.state["phase"] == "OBSERVING_SELECTED":
                self._snapshot_handoff()
            atomic_json(self.state_path, self.state)
            return
        pending = base64.b64decode(self.state.get("pending_b64") or "")
        start = self.state.get("pending_start", offset)
        combined = pending + data
        cursor = 0
        while True:
            end = combined.find(b"\n", cursor)
            if end < 0:
                break
            raw = combined[cursor:end + 1]
            record_start = start + cursor
            if self.state.get("discard_pending_line"):
                self._record("discarded_preboundary_partial_line", start_offset=record_start, bytes=len(raw), sha256=sha(raw))
                self.state["discard_pending_line"] = False
            else:
                self._process_line(raw, record_start)
            cursor = end + 1
        self.state["offset"] = offset + len(data)
        self.state["pending_b64"] = base64.b64encode(combined[cursor:]).decode()
        self.state["pending_start"] = start + cursor
        atomic_json(self.state_path, self.state)

    def run(self, interval: float) -> int:
        self.enroll()
        waiting = {"READY_WAITING", "OBSERVING_SELECTED", "READY_FOR_CONTEXT_REVIEW"}
        while self.state.get("phase") in waiting:
            self.tick()
            time.sleep(interval)
        atomic_json(self.state_path, self.state)
        return 0


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="handoff-observer-check-") as tmp:
        root = Path(tmp) / "repo"
        live = Path(tmp) / "live"
        (root / ".factory").mkdir(parents=True)
        protocol = Path(tmp) / "PROTOCOL.md"
        protocol.write_text("frozen synthetic protocol\n")
        events = root / EVENTS_REL
        events.write_text(json.dumps({"at": "2026-01-01T00:00:00Z", "event": "claimed", "ticket": 10, "labels": ["ready-for-agent"]}) + "\n")
        with events.open("a") as out:
            out.write(json.dumps({"at": "2026-01-01T00:00:00Z", "event": "lifecycle", "stage": "worker", "ticket": 7}) + "\n")

        wt = root / ".factory/wt-6"
        wt.mkdir()
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=wt, capture_output=True, text=True, check=True).stdout.strip()
        git("init", "--quiet")
        git("config", "user.name", "Observer Check")
        git("config", "user.email", "observer-check@example.invalid")
        (wt / "source.txt").write_text("base\n")
        git("add", "source.txt")
        git("commit", "--quiet", "-m", "base")
        main_sha = git("rev-parse", "HEAD")
        (wt / "source.txt").write_text("accepted implementation\n")
        git("commit", "--quiet", "-am", "implementation")
        accepted_head = git("rev-parse", "HEAD")

        fixture_issues = {
            number: {"id": number, "number": number, "title": "Edit factory/dispatch.py",
                     "body": f"Blocked by: #{number - 1}\n\nReview publishing and authority constraints.",
                     "state": "open", "labels": [{"name": "ready-for-agent"}]}
            for number in (5, 6, 7)
        }

        def fixture(endpoint: str, destination: Path, label: str) -> tuple[bytes | None, dict]:
            match = re.fullmatch(r"repos/mikeroySoft/factory/issues/(\d+)", endpoint)
            comments = re.fullmatch(r"repos/mikeroySoft/factory/issues/(\d+)/comments\?.*", endpoint)
            if match:
                data = json.dumps(fixture_issues[int(match.group(1))]).encode()
            elif comments:
                data = b"[]"
            elif endpoint == "repos/mikeroySoft/factory/commits/main":
                data = json.dumps({"sha": main_sha}).encode()
            elif label.startswith("prior-pulls-"):
                data = b"[]"
            elif endpoint.startswith("repos/mikeroySoft/factory/pulls?"):
                data = json.dumps([{"number": 123, "state": "open", "head": {"sha": accepted_head}}]).encode()
            else:
                raise AssertionError(endpoint)
            meta = {"label": label, "endpoint": endpoint, "argv": ["fixture", endpoint], "started_at": utc(), "ended_at": utc(), "returncode": 0, "bytes": len(data), "sha256": sha(data), "stderr_bytes": 0, "stderr_sha256": sha(b""), "complete": True, "limit_bytes": MAX_RESPONSE}
            raw = destination / "raw"
            write_once(raw / f"{label}.response", data)
            write_once(raw / f"{label}.stderr", b"")
            write_once(raw / f"{label}.meta.json", (json.dumps(meta, sort_keys=True) + "\n").encode())
            return data, meta

        observer = Observer(root=root, live=live, protocol=protocol, fetcher=fixture)
        observer.enroll()
        # Claims in the same timestamp second are conservatively rejected.
        time.sleep(1.05)
        opened = utc()
        rows = [
            {"at": opened, "event": "claimed", "ticket": 10, "labels": ["ready-for-agent"]},
            {"at": opened, "event": "claimed", "ticket": 52, "labels": ["ready-for-agent"]},
            {"at": opened, "event": "claimed", "ticket": 81, "labels": ["ready-for-agent"]},
            {"at": opened, "event": "claimed", "ticket": 82, "labels": ["ready-for-agent"]},
            {"at": opened, "event": "attempt", "ticket": 82, "attempt": 1, "worker_exit": 0, "gate": "PASS"},
        ]
        with events.open("a") as out:
            for row in rows:
                out.write(json.dumps(row) + "\n")
        observer.tick()
        assert observer.state["selected"] is None
        assert observer.state["candidates"] == []
        saved_state = json.loads(json.dumps(observer.state))
        for suffix, claim_time, change_contract in (
            ("late", observer.state["enrollment"]["baseline_completed_at"], False),
            ("drift", opened, True),
        ):
            if change_contract:
                fixture_issues[7]["body"] += "\nChanged downstream scope"
            claim = {"at": claim_time, "event": "claimed", "ticket": 6, "labels": ["ready-for-agent"]}
            observer._capture_candidate(claim, json.dumps(claim).encode(), suffix)
            assert observer.state["phase"] == "CAPTURE_FAILED"
            if change_contract:
                fixture_issues[7]["body"] = fixture_issues[7]["body"].removesuffix("\nChanged downstream scope")
            saved_state["observation_count"] = observer.state["observation_count"]
            observer.state = json.loads(json.dumps(saved_state))

        partial = json.dumps({"at": opened, "event": "claimed", "ticket": 6, "labels": ["ready-for-agent"]})
        with events.open("a") as out:
            out.write(partial)
        observer.tick()
        assert observer.state["selected"] is None
        with events.open("a") as out:
            out.write("\n")
        observer.tick()
        assert observer.state["selected"]["ticket"] == 6
        assert observer.state["selected"]["manual_context_qualification"] == "pending"

        handoff = wt / ".factory/handoff-6.md"
        handoff.parent.mkdir()
        handoff.write_bytes(b"full synthetic handoff\n")
        observer.tick()
        observer.tick()
        run_id = "synthetic-run"
        full_tick = [
            {"at": utc(), "event": "lifecycle", "kind": "result", "stage": "worker", "event_id": "worker-result", "ticket": 6, "attempt": 1, "dispatcher_run_id": run_id},
            {"at": utc(), "event": "lifecycle", "kind": "exit", "stage": "worker", "event_id": "worker-exit", "ticket": 6, "attempt": 1, "dispatcher_run_id": run_id},
            {"at": utc(), "event": "attempt", "ticket": 6, "attempt": 1, "worker_exit": 0, "gate": "PASS", "dispatcher_run_id": run_id},
            {"at": utc(), "event": "review", "ticket": 6, "verdict": "APPROVE", "parsed": True, "accepted": True, "head": accepted_head, "actual_head": accepted_head, "dispatcher_run_id": run_id, "execution_id": "review-execution"},
            {"at": utc(), "event": "approved", "ticket": 6, "pr": 123, "head": accepted_head, "gate_head": accepted_head, "review_head": accepted_head, "dispatcher_run_id": run_id},
        ]
        result_raw = (json.dumps(full_tick[0]) + "\n").encode()
        observer._process_line(
            result_raw,
            observer.state["offset"],
        )
        case = Path(observer.state["selected"]["directory"])
        first_result_meta = json.loads((case / "handoff/observations/worker-result.json").read_text())
        time.sleep(1.05)
        observer = Observer(root=root, live=live, protocol=protocol, fetcher=fixture)
        observer.enroll()
        with events.open("a") as out:
            for row in full_tick:
                out.write(json.dumps(row) + "\n")
        observer.tick()
        replayed_result_meta = json.loads((case / "handoff/observations/worker-result.json").read_text())
        assert replayed_result_meta == first_result_meta
        assert observer.state["selected"]["last_worker_handoff_sha256"] == first_result_meta["sha256"]
        case = Path(observer.state["selected"]["directory"])
        decision = json.loads((case / "acceptance/decision.json").read_text())
        source = json.loads((case / "source/manifest.json").read_text())
        assert observer.state["phase"] == "READY_FOR_CONTEXT_REVIEW"
        assert decision["accepted_for_capture"] is True and decision["ready_for_trials"] is False
        assert decision["approved_binding"]["head"] == accepted_head
        assert source["complete"] is True and source["head"] == accepted_head
        assert len(list((case / "handoff/objects").glob("*.bin"))) == 1
        assert len(list((case / "handoff/observations").glob("*.json"))) == 4
        handoff_objects = list((case / "handoff/objects").glob("*.bin"))
        assert handoff_objects[0].read_bytes() == b"full synthetic handoff\n"
        shutil_target = Path(tmp) / "removed-worktree"
        wt.rename(shutil_target)
        assert observer._head(wt) is None

        resumed = Observer(root=root, live=live, protocol=protocol, fetcher=fixture)
        resumed.enroll()
        assert resumed.state["phase"] == "READY_FOR_CONTEXT_REVIEW"
        print("PASS named pair: pre-start baseline; unrelated claims ignored; late claim and contract drift rejected; dispatcher subject accepted; exact-head capture; crash replay; partial lines; cleanup race; restart integrity")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return Observer().run(max(args.interval, 0.5))


if __name__ == "__main__":
    raise SystemExit(main())
