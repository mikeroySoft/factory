---
type: Groma Flow
title: Automate a ticket
groma:
  id: automate-a-ticket
---

An issue filed by the operator becomes a merged pull request. Triage and dispatch run as one oneshot systemd pass every interval; a ticket normally spans several passes: one to triage, one to work, review, and open the PR, and a later one whose merge stage lands it. Any failure along the way escalates the issue to `ready-for-human` instead.

## Steps

| From | To | Action |
| --- | --- | --- |
| [Operator](../actors/operator.md) | [GitHub](../externals/github.md) | File an Agent task issue; it starts as `needs-triage` |
| [systemd user manager](../externals/systemd-user-manager.md) | [Command entry](../systems/mikeroysoft-factory/containers/factory-cli/components/cli.md) | Timer starts the oneshot pass: `factory triage`, then `factory dispatch` |
| [Command entry](../systems/mikeroysoft-factory/containers/factory-cli/components/cli.md) | [Triage](../systems/mikeroysoft-factory/containers/factory-cli/components/triage.md) | Run the triage pass |
| [Triage](../systems/mikeroysoft-factory/containers/factory-cli/components/triage.md) | [Local triage model](../externals/local-triage-model.md) | Decide a label for each `needs-triage` issue |
| [Triage](../systems/mikeroysoft-factory/containers/factory-cli/components/triage.md) | [GitHub](../externals/github.md) | Apply `ready-for-agent` with an agent brief |
| [Command entry](../systems/mikeroysoft-factory/containers/factory-cli/components/cli.md) | [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | Run the dispatch pass |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [GitHub](../externals/github.md) | Claim the ticket: re-read it, assign, take the ticket lock, create `agent/<n>` worktree |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [Worker agent CLI](../externals/worker-agent-cli.md) | Implement the ticket in `.factory/wt-<n>` within the attempt budget |
| [Worker agent CLI](../externals/worker-agent-cli.md) | [Quality gate](../systems/mikeroysoft-factory/containers/factory-cli/components/gate.md) | Run `factory gate` before finishing |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [Quality gate](../systems/mikeroysoft-factory/containers/factory-cli/components/gate.md) | Re-run the gate as the evidence of record |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [GitHub](../externals/github.md) | Push `agent/<n>` and open the PR with the gate report |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [Reviewer CLI](../externals/reviewer-cli.md) | Review the diff; `REVISE` findings go back to the worker up to `review_rounds` times |
| [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | [GitHub](../externals/github.md) | On `APPROVE` add `factory-approved`; a later merge stage squash-merges once CI is green and the head contains `main` |
