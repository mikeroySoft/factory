---
type: C4 Component
title: Factory Manager briefing
status: stable
groma:
  id: briefing
  parent: ops-dashboard
  code:
    - scanner: python
      file: factory/briefing.py
description: Evidence-grounded Inbox briefings and Ask FM answers
---

Produces the Understand → Compare → Decide briefing for tickets needing human judgment, and answers operator questions about a ticket, a dispatcher run, or one evidence file. Collects bounded evidence from `.factory/` (escalation and manager notes, gate reports, review verdicts, PR bodies, logs) and the snapshot, giving recorded human constraints priority. Runs the model through a fixed, isolated, read-only OMP invocation with at most two requests at a time, rejects answers citing evidence outside the bundle, and caches briefings by evidence fingerprint.
