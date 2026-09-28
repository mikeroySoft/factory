---
type: C4 Component
title: Quality gate
status: stable
groma:
  id: gate
  parent: factory-cli
  code:
    - scanner: python
      file: factory/gate.py
  group: Ticket pipeline
description: Runs deterministic checks in a worktree and writes a report
---

Runs from a worktree root: conflict-marker detection, the configured `[[gate.check]]` commands in order, then a leak scan of added lines against a regex. Checks marked `exclusive` serialize on a host-wide `flock` (for example one GPU shared by many worktrees), and every check has a timeout so a wedged check fails instead of holding the lock. Writes a Markdown PASS/FAIL report with failure excerpts and exits nonzero on any failure. Workers run it themselves; the dispatcher re-runs it as a subprocess for the evidence of record.
