---
type: C4 System
title: GitHub
status: stable
groma:
  id: github
  technology: gh CLI, REST and GraphQL APIs
---

The source of truth for ticket state. Issues and their labels (`needs-triage`, `ready-for-agent`, `ready-for-human`, `factory-approved`, …) move tickets through the pipeline; `agent/<n>` branches and pull requests carry the work; CI check results gate the merge stage. The factory reaches it only through the authenticated `gh` CLI, so its permissions are those of the operator.
