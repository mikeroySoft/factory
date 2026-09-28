---
type: C4 Container
title: Ops dashboard
status: stable
groma:
  id: ops-dashboard
  parent: mikeroysoft-factory
  technology: Python http.server, single-page HTML/JS
description: Local web UI for watching tickets and answering escalations
---

A long-running local HTTP server (default `127.0.0.1:8765`) started by `factory dashboard`, usually as its own systemd user service. It serves the single-page dashboard (Inbox, Ops board, Settings), the `/codebase` history view, and the `/atlas` architecture page, backed by JSON APIs.

It is launched through the same `factory` entry point and reuses the CLI configuration loader and the dispatcher state helpers in-process, so it sees exactly what the pipeline sees: GitHub via `gh`, `.factory/` state, worktrees, the systemd timer, and the journal. Its mutating APIs change GitHub labels and comments, clean up worktrees, and edit `.factory.toml`; binding to `0.0.0.0` exposes them to the network.
