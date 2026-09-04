---
name: Agent task
about: Fully specified work item for the agent factory
title: ''
labels: needs-triage
assignees: ''

---

<!--
Conventions:
- One observable change per ticket; split multi-outcome work.
- Declare dependencies in the body as "Blocked by: #N" — the dispatcher skips
  blocked tickets until the blocker closes.
- Add the `chore` label for mechanical work (renames, doc sync, version bumps);
  it routes to the chore worker.
-->

**Scope**
What changes, stated as the observable outcome.

**Touches**
Files/symbols expected to change (pointers, not a contract).

**Exit gate**
Acceptance criteria: the observable condition that means done, and the exact
command that verifies it.

**Out of scope**
What this ticket explicitly does not change.
