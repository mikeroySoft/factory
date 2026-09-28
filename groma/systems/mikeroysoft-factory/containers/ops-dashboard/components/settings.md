---
type: C4 Component
title: Settings
status: stable
groma:
  id: settings
  parent: ops-dashboard
  code:
    - scanner: python
      file: factory/settings.py
description: Shows effective settings and saves safe edits to `.factory.toml`
---

Backs the dashboard Settings view. The snapshot reports effective work limits and the Factory Manager model with the layer each value comes from, plus a read-only summary of worker, reviewer, and triage configuration with arguments and credentials omitted. Saves validate ranges, reject stale edits by revision hash, and rewrite only the changed keys with TOMLKit so comments and unrelated settings survive. It never commits or pushes.
