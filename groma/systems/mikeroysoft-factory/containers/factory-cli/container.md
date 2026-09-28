---
type: C4 Container
title: Factory CLI
status: stable
groma:
  id: factory-cli
  parent: mikeroysoft-factory
  technology: Python 3.11, argparse, subprocess
description: 'The `factory` command: one stateless pass per subcommand'
---

The command-line application installed as `factory` (`python -m factory`). Each subcommand is a self-contained, re-runnable pass: the systemd timer runs `triage` then `dispatch`; worker agents and the dispatcher run `gate` inside ticket worktrees; the operator runs `init`, `doctor`, `install`, `stats`, and `learn` by hand. Every command reads `.factory.toml` from the main checkout, even inside a worktree.

It talks to GitHub through `gh`, to git for worktrees and merges, and to the configured worker, reviewer, and triage-model CLIs. Durable local state goes to `.factory/`: the `events.jsonl` audit trail, per-attempt logs, and per-ticket `flock` files that keep concurrent passes from claiming the same ticket.
