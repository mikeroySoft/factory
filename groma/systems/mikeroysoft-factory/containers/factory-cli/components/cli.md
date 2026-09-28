---
type: C4 Component
title: Command entry
status: stable
groma:
  id: cli
  parent: factory-cli
  code:
    - scanner: python
      file: factory/cli.py
    - scanner: python
      file: factory/__main__.py
    - scanner: python
      file: factory/__init__.py
description: Routes `factory <command>` to the module that implements it
---

Entry point for both the `factory` console script and `python -m factory`. Maps each subcommand to a module function (onboarding, triage, dispatch, gate, stats, learn, dashboard, codebase), imports it lazily, and passes the remaining arguments through. Also owns the package version reported by `--version`.
