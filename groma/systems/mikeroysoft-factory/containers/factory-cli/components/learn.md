---
type: C4 Component
title: Lessons
status: stable
groma:
  id: learn
  parent: factory-cli
  code:
    - scanner: python
      file: factory/learn.py
  group: Outcome analysis
description: Distils recent ticket outcomes into `.factory-lessons.md`
---

Closes the improvement loop. Collects evidence for the last finished tickets from `events.jsonl`: gate results, review verdicts, escalation reasons, the latest reviewer findings, and tails of failing attempt logs. Asks the local model for at most ten repository-specific lessons, writes `.factory-lessons.md` for the operator to review and commit, and records a `learn` event. Every later worker prompt carries the file.
