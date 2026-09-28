---
type: Groma Flow
title: Learn from outcomes
groma:
  id: learn-from-outcomes
---

The operator turns recent ticket outcomes into standing guidance for workers. Lessons are only proposed; the operator reviews and commits `.factory-lessons.md`, and its effect shows in the dashboard first-gate pass and bounce rate.

## Steps

| From | To | Action |
| --- | --- | --- |
| [Operator](../actors/operator.md) | [Command entry](../systems/mikeroysoft-factory/containers/factory-cli/components/cli.md) | Run `factory learn --dry-run`, then `factory learn` |
| [Command entry](../systems/mikeroysoft-factory/containers/factory-cli/components/cli.md) | [Lessons](../systems/mikeroysoft-factory/containers/factory-cli/components/learn.md) | Run the learn command |
| [Lessons](../systems/mikeroysoft-factory/containers/factory-cli/components/learn.md) | [Dispatcher](../systems/mikeroysoft-factory/containers/factory-cli/components/dispatch.md) | Read gate results, verdicts, and escalations for the last finished tickets |
| [Lessons](../systems/mikeroysoft-factory/containers/factory-cli/components/learn.md) | [Triage](../systems/mikeroysoft-factory/containers/factory-cli/components/triage.md) | Send the evidence through the local-model client |
| [Triage](../systems/mikeroysoft-factory/containers/factory-cli/components/triage.md) | [Local triage model](../externals/local-triage-model.md) | Propose at most ten repository-specific lessons |
