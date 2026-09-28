---
type: Groma Relationships
title: Architecture relationships
---

## Relationships

| Source | Target | Description | Technology |
| --- | --- | --- | --- |
| [Operator](actors/operator.md) | [factory/cli.py](../factory/cli.py) | Runs factory commands | shell |
| [Operator](actors/operator.md) | [factory/dashboard.py](../factory/dashboard.py) | Answers escalations | browser, HTTP |
| [Operator](actors/operator.md) | [GitHub](externals/github.md) | Files agent tasks | GitHub issues |
| [systemd user manager](externals/systemd-user-manager.md) | [factory/cli.py](../factory/cli.py) | Starts factory processes | systemd user units |
| [factory/cli.py](../factory/cli.py) | [factory/onboard.py](../factory/onboard.py) | Runs onboarding commands | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/triage.py](../factory/triage.py) | Runs triage pass | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/dispatch.py](../factory/dispatch.py) | Runs dispatch pass | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/gate.py](../factory/gate.py) | Runs quality gate | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/stats.py](../factory/stats.py) | Prints ticket stats | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/learn.py](../factory/learn.py) | Distils lessons | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/dashboard.py](../factory/dashboard.py) | Starts dashboard server | in-process call |
| [factory/cli.py](../factory/cli.py) | [factory/codebase.py](../factory/codebase.py) | Builds codebase history | in-process call |
| [factory/onboard.py](../factory/onboard.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/triage.py](../factory/triage.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/dispatch.py](../factory/dispatch.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/gate.py](../factory/gate.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/stats.py](../factory/stats.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/learn.py](../factory/learn.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/settings.py](../factory/settings.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/codebase.py](../factory/codebase.py) | [factory/config.py](../factory/config.py) | Loads configuration | in-process call |
| [factory/dispatch.py](../factory/dispatch.py) | [factory/gate.py](../factory/gate.py) | Re-runs gate as evidence | subprocess (python -m factory gate) |
| [factory/dispatch.py](../factory/dispatch.py) | [GitHub](externals/github.md) | Claims, opens, and merges PRs | gh CLI, git push |
| [factory/dispatch.py](../factory/dispatch.py) | [Worker agent CLI](externals/worker-agent-cli.md) | Runs worker attempts | subprocess in ticket worktree |
| [factory/dispatch.py](../factory/dispatch.py) | [Reviewer CLI](externals/reviewer-cli.md) | Requests diff review | subprocess, stdout verdict |
| [Worker agent CLI](externals/worker-agent-cli.md) | [factory/gate.py](../factory/gate.py) | Self-checks its work | factory gate CLI |
| [factory/triage.py](../factory/triage.py) | [GitHub](externals/github.md) | Labels issues | gh CLI |
| [factory/triage.py](../factory/triage.py) | [Local triage model](externals/local-triage-model.md) | Classifies issues | HTTP chat completions |
| [factory/learn.py](../factory/learn.py) | [factory/triage.py](../factory/triage.py) | Asks local model | in-process call |
| [factory/learn.py](../factory/learn.py) | [factory/dispatch.py](../factory/dispatch.py) | Reads ticket events | in-process, events.jsonl |
| [factory/stats.py](../factory/stats.py) | [GitHub](externals/github.md) | Reads ticket history | gh CLI |
| [factory/onboard.py](../factory/onboard.py) | [GitHub](externals/github.md) | Creates labels | gh CLI |
| [factory/onboard.py](../factory/onboard.py) | [systemd user manager](externals/systemd-user-manager.md) | Installs units | systemctl --user |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/dispatch.py](../factory/dispatch.py) | Reuses dispatcher state | in-process, events.jsonl and locks |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/triage.py](../factory/triage.py) | Re-triages one issue | subprocess (python -m factory triage) |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/briefing.py](../factory/briefing.py) | Requests briefings | in-process call |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/settings.py](../factory/settings.py) | Reads and saves settings | in-process call |
| [factory/dashboard.py](../factory/dashboard.py) | [factory/codebase.py](../factory/codebase.py) | Refreshes codebase history | in-process, background thread |
| [factory/dashboard.py](../factory/dashboard.py) | [GitHub](externals/github.md) | Reads and updates tickets | gh GraphQL and CLI |
| [factory/dashboard.py](../factory/dashboard.py) | [systemd user manager](externals/systemd-user-manager.md) | Reads timer and journal | systemctl, journalctl |
| [factory/briefing.py](../factory/briefing.py) | [OMP](externals/omp.md) | Asks Factory Manager | omp subprocess, no tools |
