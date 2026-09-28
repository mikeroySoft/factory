---
type: C4 Component
title: Configuration
status: stable
groma:
  id: configuration
  parent: factory-cli
  code:
    - scanner: python
      file: factory/config.py
description: Loads layered host and repository settings into one Config
---

Finds the main checkout even from inside a worktree, then layers `.factory.toml` over the host file `~/.config/factory/config.toml` (host-owned tables: triage, workers, review, manager, install, plus dashboard port and gate lock). Produces the `Config` every other part uses: repo slug and upstream, worker argv per label, reviewer argv, gate checks, leak-scan pattern, triage endpoint, Factory Manager model, limits, and install defaults.

Also defines the fixed label conventions and small git helpers shared across the package. Reads the legacy `manager.command` only for its `--model` value and never executes it.
