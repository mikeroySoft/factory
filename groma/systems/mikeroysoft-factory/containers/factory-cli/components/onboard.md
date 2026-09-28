---
type: C4 Component
title: Onboarding
status: stable
groma:
  id: onboard
  parent: factory-cli
  code:
    - scanner: python
      file: factory/onboard.py
description: 'Prepares a repository and host: init, doctor, install'
---

`factory init` writes `.factory.toml`, the `.gitignore` lines, the Agent task issue template, and a placeholder CI workflow when the repository has none (the merge stage refuses PRs without a passing check), then creates the six factory labels on GitHub. `factory doctor` checks tools, `gh` auth and push access, labels, CI workflows, worker and reviewer executables, config drift, the triage endpoint, and the timer. `factory install` writes and enables the systemd user units: the oneshot triage + dispatch service with its timer, and optionally the dashboard service.
