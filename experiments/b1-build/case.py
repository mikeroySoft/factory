#!/usr/bin/env python3
"""B1 case (#98), run inside the sandbox: probe the boundary, render, score.

python3 -I -B /src/experiments/b1-build/case.py   (reads /in, writes /out)

1. safety: every required denial is probed from inside; a probe that succeeds is a breach.
2. validity: every frozen input is re-hashed against the host manifest.
3. product: render the compact briefing from the frozen baseline representation and
   score baseline and candidate against the oracle that was frozen before rendering.
Stdlib only; never opens a path named inside the inputs; no network use beyond the probes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
from pathlib import Path

IN, OUT = Path("/in"), Path("/out")
ENV_ALLOWED = {"PATH", "HOME", "LANG", "PWD"}
TICKET = re.compile(
    r"^- #(\d+) (OPEN|CLOSED) tier=(NOW|NEXT|THEN|LATER|none)"
    r"(?: (held|not-held) open_blockers=(none|unknown|#\d+(?:,#\d+)*))? \[(S\d+)\]")
REVISION = re.compile(r"^revision ([0-9a-f]{64}) observed \S+ \[(S\d+)\]$")


# -- facts: (kind, ticket, value); values are str, bool or a sorted tuple of ints --

def as_facts(rows) -> set:
    return {(kind, number, tuple(value) if isinstance(value, list) else value) for kind, number, value in rows}


def compare(facts: set, oracle: set) -> dict:
    """Omitted: an oracle fact not stated. Unsupported: a stated fact the oracle contradicts or lacks."""
    rows = lambda found: sorted(([k, n, list(v) if isinstance(v, tuple) else v] for k, n, v in found), key=str)
    return {"oracle": len(oracle), "stated": len(facts), "retained": len(facts & oracle),
            "omitted": rows(oracle - facts), "unsupported": rows(facts - oracle)}


def plan_of(baseline: dict, initiative: int) -> dict | None:
    plans = ((baseline.get("investigation") or {}).get("plans")) or []
    return next((p for p in plans if isinstance(p, dict) and p.get("number") == initiative), None)


def baseline_facts(baseline: dict, initiative: int) -> set:
    """Facts the existing representation states, read structurally."""
    plan = plan_of(baseline, initiative)
    if plan is None:
        return set()
    facts = set()
    sha = (plan.get("revision") or {}).get("sha256")
    if isinstance(sha, str):
        facts.add(("revision", initiative, sha))
    tiers = ((plan.get("next") or {}).get("priorities")) or {}
    for child in plan.get("children") or []:
        number, state = child.get("number"), child.get("state")
        facts.add(("state", number, state))
        if str(number) in tiers:
            facts.add(("tier", number, tiers[str(number)]))
        if state == "OPEN":
            facts.add(("held", number, "factory-held" in (child.get("labels") or [])))
            rows = child.get("blockers") or []
            if not any("unavailable" in row for row in rows):
                facts.add(("open_blockers", number, tuple(sorted(row["number"] for row in rows))))
    return facts


def render(baseline: dict, initiative: int) -> str:
    """Compact source-linked briefing: scored lines (revision, linked tickets) carry a resolvable citation."""
    plan = plan_of(baseline, initiative)
    if plan is None:
        return f"# Initiative #{initiative}: unavailable in the frozen representation\n"
    revision = plan.get("revision") or {}
    coverage = baseline.get("coverage") or {}
    tiers = ((plan.get("next") or {}).get("priorities")) or {}
    action = ((plan.get("next") or {}).get("action")) or {}
    target = f"#{action['ticket']}" if action.get("ticket") else action.get("experiment") or "none"
    lines = [
        f"# Initiative #{initiative}: {plan.get('title')}",
        f"revision {revision.get('sha256')} observed {revision.get('observed_at')} [{plan.get('source')}]",
        f"status {plan.get('status')}; owner {plan.get('owner')}; issue {plan.get('state')}; "
        f"coverage {coverage.get('status')}; errors {len(baseline.get('errors') or [])} [{plan.get('source')}]",
        f"next {action.get('kind') or 'none'} {target} [{','.join(action.get('sources') or [])}]",
        "## linked tickets",
    ]
    for child in sorted(plan.get("children") or [], key=lambda c: c.get("number") or 0):
        number, state = child.get("number"), child.get("state")
        parts = [f"- #{number}", str(state), f"tier={tiers.get(str(number), 'none')}"]
        if state == "OPEN":
            rows = child.get("blockers") or []
            blockers = ("unknown" if any("unavailable" in row for row in rows) else
                        ",".join(f"#{row['number']}" for row in sorted(rows, key=lambda r: r["number"])) or "none")
            parts += ["held" if "factory-held" in (child.get("labels") or []) else "not-held",
                      f"open_blockers={blockers}"]
        parts.append(f"[{child.get('source')}]")
        lines.append(" ".join(parts) + f" {child.get('title')}")
    return "\n".join(lines) + "\n"


def candidate_facts(text: str, sources: dict, initiative: int) -> tuple[set, list]:
    """Facts the briefing states, parsed back from text; plus citations that do not resolve."""
    facts, unresolved = set(), []
    for line in text.splitlines():
        if match := REVISION.match(line):
            facts.add(("revision", initiative, match[1]))
            if f"#{initiative}" not in sources.get(match[2], ""):
                unresolved.append(line[:120])
        elif match := TICKET.match(line):
            number, state, tier, held, blockers, cite = match.groups()
            number = int(number)
            facts.add(("state", number, state))
            if tier != "none":
                facts.add(("tier", number, tier))
            if held:
                facts.add(("held", number, held == "held"))
            if blockers and blockers != "unknown":
                facts.add(("open_blockers", number,
                            () if blockers == "none" else tuple(sorted(int(b[1:]) for b in blockers.split(",")))))
            if not re.search(rf"#{number}\b", sources.get(cite, "")):
                unresolved.append(line[:120])
    return facts, unresolved


# -- safety probes: each must be denied from inside the sandbox --

def _attempt(fn) -> tuple[bool, str]:
    """(denied, observation)."""
    try:
        fn()
    except OSError as exc:
        return True, f"{type(exc).__name__}:{exc.errno}"
    return False, "succeeded"


def _connect(host: str, port: int):
    def go():
        with socket.create_connection((host, port), timeout=2):
            pass
    return go


def _write(path: str):
    def go():
        with open(path, "x"):
            pass
    return go


def _exists(path: str):
    def go():
        os.lstat(path)
    return go


def probes(host_paths: dict) -> list[dict]:
    checks = [
        ("external network (github.com address)", _connect("140.82.112.3", 443)),
        ("loopback service: local model port", _connect("127.0.0.1", 11435)),
        ("loopback service: factory dashboard port", _connect("127.0.0.1", 8769)),
        ("name resolution config", _exists("/etc/resolv.conf")),
        ("host sockets and runtime dir", _exists("/run")),
        ("write to /src", _write("/src/.b1-write-probe")),
        ("write to /in", _write("/in/.b1-write-probe")),
        ("new user namespace", lambda: os.unshare(os.CLONE_NEWUSER)),
    ]
    checks += [(f"host path: {label}", _exists(path)) for label, path in sorted(host_paths.items())]
    rows = []
    for name, fn in checks:
        denied, observed = _attempt(fn)
        rows.append({"probe": name, "denied": denied, "observed": observed})
    leaked = sorted(set(os.environ) - ENV_ALLOWED)
    rows.append({"probe": "environment limited to PATH/HOME/LANG/PWD", "denied": not leaked,
                 "observed": "clean" if not leaked else f"extra variables: {len(leaked)}"})
    home = os.listdir(os.environ.get("HOME", "/home/sandbox"))
    rows.append({"probe": "private empty home", "denied": not home, "observed": f"{len(home)} entries"})
    pid1 = Path("/proc/1/cmdline").read_bytes().split(b"\0", 1)[0]
    rows.append({"probe": "private PID namespace", "denied": pid1.endswith(b"bwrap"),
                 "observed": pid1.decode(errors="replace")[-40:]})
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = json.loads((IN / "manifest.json").read_bytes())
    initiative = manifest["initiative"]
    validity = {name: sha256(IN / name) == digest for name, digest in sorted(manifest["inputs"].items())}
    safety = probes(json.loads((IN / "host-paths.json").read_bytes()))
    result = {"safety": {"ok": all(row["denied"] for row in safety), "probes": safety},
              "validity": {"ok": all(validity.values()), "inputs_match_manifest": validity}, "product": None}
    if result["validity"]["ok"]:
        raw = (IN / "baseline.json").read_bytes()
        baseline = json.loads(raw)
        oracle = as_facts(json.loads((IN / "oracle.json").read_bytes())["facts"])
        text = render(baseline, initiative)
        (OUT / "candidate.md").write_text(text)
        sources = {row["id"]: str(row.get("label") or "") for row in baseline.get("sources") or []
                   if isinstance(row, dict) and isinstance(row.get("id"), str)}
        stated, unresolved = candidate_facts(text, sources, initiative)
        cand = compare(stated, oracle)
        base = compare(baseline_facts(baseline, initiative), oracle)
        result["product"] = {
            "baseline": {**base, "bytes": len(raw)},
            "candidate": {**cand, "bytes": len(text.encode()), "unresolved_citations": unresolved},
            "criterion_met": not cand["omitted"] and not cand["unsupported"] and not unresolved,
        }
    (OUT / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
