---
type: C4 System
title: factory
status: stable
groma:
  id: mikeroysoft-factory
  technology: Python 3.11+
description: Autonomous triage, implementation, review, and merge of GitHub tickets
---

One Python package (`factory`, installed as the `factory` command) that runs as two processes on the operator machine: a timer-driven command-line pipeline and a long-running local ops dashboard.

Work enters as GitHub issues. The pipeline moves each ticket through triage, claim, worker attempts, gate, review, PR, and the serialized merge stage, recording every transition in `.factory/events.jsonl`. The dashboard reads the same GitHub and `.factory/` state to show progress and lets the operator answer escalations. Coding, reviewing, triage, and briefing models are external CLIs or endpoints chosen in `.factory.toml`, layered over a host config in `~/.config/factory/config.toml`.
