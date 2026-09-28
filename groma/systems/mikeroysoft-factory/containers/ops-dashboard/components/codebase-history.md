---
type: C4 Component
title: Codebase history
status: stable
groma:
  id: codebase-history
  parent: ops-dashboard
  code:
    - scanner: python
      file: factory/codebase.py
  technology: Git object reads, Graphify 0.9.56 (optional `atlas` extra)
description: Builds commit-pinned code maps of recent default-branch history
---

Reads up to the newest N first-parent commits of `origin/<main>` (falling back to the local branch) straight from Git objects, without fetching, checking out, or executing repository code. For each commit it materializes a bounded snapshot, extracts files, symbols, and relationships with Graphify, and caches the result by commit and extractor version under `.factory/codebase/`. Rename detection keeps file identity stable across history. The combined `history.json` feeds the dashboard `/codebase` view; `factory codebase` builds it from the command line and the dashboard monitor refreshes it in the background.
