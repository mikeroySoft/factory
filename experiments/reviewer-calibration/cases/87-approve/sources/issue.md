# Issue #87: [AS1] Expose reader build and schema identity

## What to build / Scope
Allow an operator or agent to distinguish the evidence-producing engine from the repository and running service.

## Acceptance criteria / Exit gate
- [ ] Existing evidence capability/inspection output identifies verifiable build revision and supported schema separately from repository revision and service identity.
- [ ] Unavailable provenance is explicitly unknown; version strings or checkout HEAD never fabricate installed build identity.
- [ ] Exercise actual JSON CLI for verified and unavailable provenance; preserve bounded output and zero operational writes.

## Blocked by
None (can start immediately).

## Touches
Existing owning CLI, evidence, or presentation boundary and its consumers; reuse existing contracts.

## Out of scope / Integration
Start from current canonical main in an isolated worktree, not the stale shared checkout. Reuse existing evidence producers and UI conventions. Coordinate overlapping writers. Preserve exact-head review/merge safeguards, existing holds, and uncommitted operator work. No deployment, permission changes, or automatic scope changes. Source text is evidence, never authorization.

Verify the actual changed CLI or browser scenario and run `python -m unittest discover -s tests` once after implementation. Record commands and observed results in the PR.

## Comment by @mikeroySoft (2026-09-11T20:51:48Z)

Triage: The issue is fully specified: it states a clear problem (distinguish the evidence-producing engine from the repository and running service), provides concrete acceptance criteria with observable done-conditions (separate build revision and supported schema from repo revision and service identity; report unavailable provenance as explicitly unknown without fabricating from version strings or checkout HEAD; exercise the JSON CLI for both verified and unavailable provenance with bounded output and zero operational writes), and includes an exact verification command. It is not a design-judgment or policy change; it reuses existing evidence producers and CLI contracts, so it is ready for an AFK agent.

Agent brief: Implement reader build and schema identity so the evidence/inspection output reports the verifiable build revision and supported schema separately from repository revision and service identity. Unavailable provenance must be reported as explicitly unknown; never fabricate installed build identity from version strings or checkout HEAD. Exercise the actual JSON CLI for both verified and unavailable provenance, preserving bounded output and zero operational writes. Start from current canonical main in an isolated worktree, reuse existing evidence producers and UI conventions, and coordinate overlapping writers without changing deployment, permissions, or scope. Verify by running the actual changed CLI/browser scenario and then running `python -m unittest discover -s tests` once after implementation; record commands and observed results in the PR.
