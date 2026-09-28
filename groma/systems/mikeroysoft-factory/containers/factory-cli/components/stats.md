---
type: C4 Component
title: Ticket stats
status: stable
groma:
  id: stats
  parent: factory-cli
  code:
    - scanner: python
      file: factory/stats.py
  group: Outcome analysis
description: Prints per-ticket attempts, review rounds, and time to merge
---

Read-only metrics from GitHub: collects factory PRs on `agent/<n>` branches and their issues, then reports attempts, review rounds, state, and hours from issue creation to merge as a table or `--json`.
