"""`factory` command: one subcommand per stage, each a stateless pass."""

from __future__ import annotations

import sys
from importlib import import_module

COMMANDS = {
    "init": ("onboard", "init", "prepare this repository: .factory.toml, labels, issue template"),
    "doctor": ("onboard", "doctor", "check tools, auth, remotes, and the triage model"),
    "install": ("onboard", "install", "install the systemd user timer (and dashboard)"),
    "triage": ("triage", "main", "label needs-triage issues with the local model"),
    "triage-probe": ("triage", "probe_main", "probe the triage endpoint and restart its configured service"),
    "dispatch": ("dispatch", "main", "one pass: sync, merge stage, claim and work tickets"),
    "manage": ("manage", "main", "recommend PR/issue viability, resolve untouched escalations, publish terminal human handoffs"),
    "gate": ("gate", "main", "run the deterministic quality gate in this worktree"),
    "verify-merge": ("certcheck", "main", "verify merge certificates or audit first-parent main"),
    "watch": ("watch", "main", "file new factory runtime failures (journal, failed units, runtime-json) as bug issues"),
    "stats": ("stats", "main", "ticket metrics from GitHub"),
    "explain": ("trace", "main", "explain one ticket's routing decisions from the local journal"),
    "learn": ("learn", "main", "distil recent ticket outcomes into .factory-lessons.md"),
    "eval": ("eval", "main", "replay the frozen worker suite with one profile"),
    "score": ("score", "main", "rubric-score finished tickets' worker attempts from their transcripts"),
    "release": ("release", "main", "measure the quality bar and cut an atomic patch release"),
    "dashboard": ("dashboard", "main", "serve the authenticated outcome-control workspace"),
    "chat": ("chat", "main", "open the read-only Factory Manager console (needs the installed Pi runtime)"),
    "evidence": ("evidence", "main", "read one bounded schema-1 project/CI evidence request"),
    "mcp": ("mcp", "main", "serve registered repositories' evidence reads to a stdio MCP client"),
    "plan": ("plan", "main", "read initiatives: `plan list`, `plan inspect N`, `plan baseline N`, `plan drift TICKET`, `plan route N --reason R` (JSON)"),
    "codebase": ("codebase", "main", "build commit-pinned codebase history for the dashboard"),
}


def usage() -> str:
    width = max(len(c) for c in COMMANDS)
    lines = ["usage: factory <command> [options]", "", "commands:"]
    lines += [f"  {name.ljust(width)}  {desc}" for name, (_, _, desc) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        from factory import __version__

        print(__version__)
        return 0
    entry = COMMANDS.get(argv[0])
    if not entry:
        print(f"factory: unknown command `{argv[0]}`\n\n{usage()}", file=sys.stderr)
        return 2
    module, func, _ = entry
    return import_module(f"factory.{module}").__dict__[func](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
