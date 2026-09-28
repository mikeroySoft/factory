---
type: C4 System
title: Worker agent CLI
status: stable
groma:
  id: worker-agent-cli
  technology: omp, droid, codex exec, claude -p
---

The coding agent that implements a ticket. Configured per ticket label in `[workers]` as an argv with `{prompt}` and `{cwd}` placeholders (default `omp -p`). It runs inside the ticket worktree with a prompt file, commits its work, writes handoff notes, and finishes by running `factory gate` itself.
