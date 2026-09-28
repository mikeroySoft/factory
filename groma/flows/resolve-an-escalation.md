---
type: Groma Flow
title: Resolve an escalation
groma:
  id: resolve-an-escalation
---

The operator decides a case the pipeline handed back. The dashboard assembles evidence, the Factory Manager briefs from that evidence only, and the confirmed decision is audited locally before and after it changes GitHub.

## Steps

| From | To | Action |
| --- | --- | --- |
| [Operator](../actors/operator.md) | [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | Open a `ready-for-human` case in the Inbox |
| [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | [GitHub](../externals/github.md) | Load issues, timelines, PRs, and CI in one GraphQL snapshot |
| [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | [Factory Manager briefing](../systems/mikeroysoft-factory/containers/ops-dashboard/components/briefing.md) | Request the Understand → Compare → Decide briefing |
| [Factory Manager briefing](../systems/mikeroysoft-factory/containers/ops-dashboard/components/briefing.md) | [OMP](../externals/omp.md) | Brief from the bounded, cited evidence bundle with no tools |
| [Operator](../actors/operator.md) | [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | Confirm a decision with rationale after the mutation preview |
| [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | Record the `human-decision` intent in `events.jsonl`, failing closed if unwritable |
| [Dashboard server](../systems/mikeroysoft-factory/containers/ops-dashboard/components/dashboard-server.md) | [GitHub](../externals/github.md) | Apply labels, assignment, and the rationale comment |
