---
type: C4 System
title: OMP
status: stable
groma:
  id: omp
  technology: omp CLI (no-tools, no-session)
---

The `omp` agent CLI, used by the dashboard as the Factory Manager model. The dashboard runs a bounded, read-only `omp -p --no-tools --no-session` process against server-collected evidence; `[manager].model` selects the model. The configured `manager.command` is never executed.
