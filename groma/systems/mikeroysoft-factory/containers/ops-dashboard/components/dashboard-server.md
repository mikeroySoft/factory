---
type: C4 Component
title: Dashboard server
status: stable
groma:
  id: dashboard-server
  parent: ops-dashboard
  code:
    - scanner: python
      file: factory/dashboard.py
description: Serves the dashboard pages and its JSON APIs
---

Builds the dashboard snapshot from one GitHub GraphQL query (issues, timelines, PRs, CI), `.factory/` state, worktrees, per-ticket spend from `events.jsonl`, upstream drift, systemd timer state, and journal runs, cached briefly. Serves `/api/snapshot`, `/api/file`, `/api/codebase`, and `/api/settings`, plus the HTML, CSS, fonts, and vendored Motion assets.

`POST /api/act` applies operator decisions with the same `gh` calls the dispatcher uses (labels, comments, assignment, close), can re-run triage for one issue, and can clean up a stale worktree; it records a `human-decision` event before and after, failing closed if the audit trail is unwritable. Briefing and question requests go to Factory Manager briefing, settings saves to Settings. A background monitor refreshes codebase history every 30 seconds.
