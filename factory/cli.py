"""`factory` command: one subcommand per stage, each a stateless pass."""

from __future__ import annotations

import sys
from importlib import import_module

COMMANDS = {
    "init": ("onboard", "init", "prepare this repository: .factory.toml, labels, issue template"),
    "doctor": ("onboard", "doctor", "check tools, auth, remotes, and the triage model"),
    "install": ("onboard", "install", "install the systemd user timer (and dashboard)"),
    "triage": ("triage", "main", "label needs-triage issues with the local model"),
    "dispatch": ("dispatch", "main", "one pass: sync, merge stage, claim and work tickets"),
    "gate": ("gate", "main", "run the deterministic quality gate in this worktree"),
    "stats": ("stats", "main", "ticket metrics from GitHub"),
    "learn": ("learn", "main", "distil recent ticket outcomes into .factory-lessons.md"),
    "dashboard": ("dashboard", "main", "serve the local ops dashboard"),
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
