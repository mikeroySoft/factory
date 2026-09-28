---
type: C4 Component
title: Dispatcher
status: stable
groma:
  id: dispatch
  parent: factory-cli
  code:
    - scanner: python
      file: factory/dispatch.py
  group: Ticket pipeline
description: Runs one stateless pass that moves tickets from claim to merge
---

The core of the pipeline. Under one landing lock it first syncs upstream `main` into a fork when configured, then runs the merge stage: at most one PR per pass is squash-merged, and only when the gate passed, the reviewer approved (`factory-approved`), GitHub checks are green, and the head already contains `main`; stale heads are rebased, re-gated, and force-pushed.

It then claims up to `max_active` tickets from the frontier. Per ticket it takes a `flock`, creates worktree `.factory/wt-<n>` on `agent/<n>`, runs the worker with a prompt built from the issue, lessons, and prior feedback, re-runs the gate as evidence of record, opens the PR, and asks the reviewer, bouncing `REVISE` findings back for up to `review_rounds`. Exhausted budgets, repeated gate failures, rebase conflicts, and red CI escalate the issue to `ready-for-human`. Every transition is appended to `.factory/events.jsonl`.
