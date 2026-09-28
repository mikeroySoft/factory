---
type: C4 System
title: Reviewer CLI
status: stable
groma:
  id: reviewer-cli
  technology: omp -p, codex exec
---

A second model that reviews each ticket diff. Configured in `[review].command`; receives the diff scope, the issue, and the gate report as a prompt and answers on stdout with `path:line` findings ending in `VERDICT: APPROVE` or `VERDICT: REVISE`.
