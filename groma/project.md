---
type: Groma Project
title: factory
groma:
  profile: architecture
description: Label-driven autonomous ticket pipeline for GitHub repositories
---

factory turns labelled GitHub issues into merged pull requests without a human in the loop for routine work. A local model triages new issues; a stateless dispatcher claims `ready-for-agent` tickets, runs a coding agent in a git worktree, checks the result with a deterministic gate, has a second model review the diff, opens the PR, and lands it once gate, reviewer, CI, and freshness against `main` agree. Anything it cannot resolve goes back to a human with `ready-for-human` and the evidence attached.

It runs on the operator machine under systemd. State is GitHub (labels, comments, PRs) plus the gitignored `.factory/` directory (event log, worker logs, locks, worktrees, codebase cache). Research harnesses in `experiments/` and UI prototypes in `console/` are excluded from this map.
