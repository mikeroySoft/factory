# Factory and District UX orchestration handoff

## Request and authorization

The user approved the grounded UX direction and wants a fresh agent to orchestrate its planning and execution. The immediate agreed next deliverable is a planning addendum and reconciled ticket set, preserving existing releases and deployment gates. Do not interpret approval of direction as permission to deploy, bypass triage, release every held issue, or approve visual/security checkpoints on the operator's behalf. If asked to proceed beyond planning, use the existing factory pipeline rather than a competing controller.

Exploration is explicitly wanted later, not a current priority. Preserve it as deferred scope, not a dependency or implementation task now.

This handoff request authorizes this document and a launch prompt only. No issues, code, services or releases were changed by this handoff.

## Read first; do not work from these snapshots alone

Repositories:
- `/home/mike/dev/mikeroysoft/factory`
- `/home/mike/dev/mikeroysoft/district`

Read relevant factory, district and ponytail skills; UI guidance for interface work. Context files supplied by the harness remain authoritative.

Read these current sources:
- District `OPERATIONS-CONSOLE-SPEC.md`
- District `OPERATIONS-CONSOLE-FACTORY-TICKETS.md`
- District `OPERATIONS-CONSOLE-DISTRICT-TICKETS.md`
- Factory `docs/manager-plan.md`
- Factory `factory/dashboard.py`, `factory/dashboard.html`, `factory/briefing.py`, `factory/dispatch.py`
- District `district/dashboard.py`, `district/health.py`, `district/status.py`

Approved immutable console baseline:
https://github.com/mikeroySoft/district/blob/164663b1a3e5e78b4fde3b127cf013d87b7e3c28/OPERATIONS-CONSOLE-SPEC.md

Refresh branch/worktree status, live issues and relevant PRs. Other agents and timers are working. Never overwrite uncommitted work or rewrite a claimed ticket under its worker.

## Grounded product decisions

- District owns machinery health, fleet runtime observation and host management.
- Factory owns issue/PR decisions and project feedback.
- Factory owns execution truth; District consumes public CLI JSON, not Python internals or parsed worker logs.
- District target is Overview / Flows / Brief, with stable equally weighted factory diagrams, concurrent executions, explicit freshness and known waits. Do not replace this with a severity-reordered table or another design programme.
- Normal code-check failure, review revision, project escalation, capacity/CI waits are not automatically machinery incidents.
- Preserve existing Factory Inbox and ticket drawer. No second inbox, notification centre, persistent agent-session authority or new desktop app.
- Current Inbox FM is a read-only, evidence-cited advisor. It is not the autonomous manager stage proposed in older plans.
- Keep deterministic gate, independent review, CI, current-main and human-veto requirements. No weaker merge readiness borrowed from another product.

## Existing features: do not reimplement

Factory already has:
- Inbox as default; human-required versus informational items.
- Situation, recommendation, uncertainty, earlier decisions and source citations.
- Explicit action effects, next owner, reversibility and exact-request confirmation.
- Durable started/result decision events and honest partial/unknown receipts.
- Ticket drawer: Timeline, Attempts, Gate, Review, Branch, Prompt, Issue; contextual Ask FM.
- Gate retries and own-reviewer bounces, retained worktrees and handoff reuse.

District still served Atlas during inspection; A/B/C prototype pages are illustrative, not telemetry. The approved specification already assigns its replacement and safety work.

## Observations to refresh

On 2026-09-05, live Factory Ops showed approved PRs #22–#24 with no CI checks, while the KPI said `human merge pending`. Dispatcher code refuses merge when no passing checks are reported. Treat this as an evidenced explanation gap, not authorization to bypass CI or assume its underlying cause.

At the last issue check:
- Factory #26 / F01 was CLOSED as completed. Verify merged acceptance and installed-engine compatibility separately.
- District #23 / D01 and #24 / D07 were OPEN, ready-for-agent.
- Factory #15 PR frontier was OPEN, ready-for-agent, blocked by #13. Its exit gate still named pytest; current repository gate used unittest discovery. Reconcile this before execution without modifying an active worker's contract.

Configured local viewing endpoints were Factory `http://127.0.0.1:8769/#ops` and District `http://127.0.0.1:8760/`. Viewing was exercised; no mutation/model requests were made. Opening an actionable Factory Inbox case can automatically generate a model briefing; use read-only Ops/source inspection when avoiding inference calls.

## Full planning work surface

Materialize each item below as its own todo before acting.

### Reconciliation

1. Refresh current implementation, installed versions, issues, PRs and active ownership.
2. Verify F01 merge/acceptance evidence and identify installed-runtime gap.
3. Preserve approved District scope, dependency graph and release gates.
4. Produce a small Factory UX/feedback addendum to existing planning material; reuse documents rather than create competing specifications.
5. Map every proposed outcome to existing issues; split only genuine uncovered outcomes.
6. Correct stale acceptance commands and source references where needed, without changing claimed work underneath workers.
7. Record exploration as wanted but deferred, with no intake label or blocking dependency.
8. Present exact proposed ticket changes/release state; publication and later releases must follow authorization and existing checkpoints.

### Workstream A: existing console programme, not duplicate tickets

- F01 Factory #26: authoritative execution lifecycle.
- F02 Factory #27: waits/resource ownership, depends on F01.
- F03 Factory #28: bounded network-free runtime JSON, depends on F02.
- Explicit installed-engine verification before District collection work.
- D01 District #23: shared operational classification.
- D07 District #24: read-only LAN / trusted-local management policy.
- D02 District #25: bounded shared collector; requires D01, integrated D07 and verified installed F03.
- D03 District #26: scoped shell/navigation.
- D04 District #27: real graphical Overview.
- D05 District #28: concurrent Flows/reusable evidence.
- D06 District #29: deterministic Brief.
- D08 District #30: management workflows after D05/D07.
- D09 District #31: accepted cutover, only after required visual/LAN acceptance and pinned prototype archive.

### Workstream B: narrow Factory outcomes

B1. Explain PR readiness in existing board/Inbox. Missing/pending/failing CI, requested changes and missing approval are distinct; unknown branch freshness stays unknown. Never equate approval with eligibility. Also map queue/admission explanation gaps against F02 rather than invent another telemetry source.

B2. Expose later PR feedback as structured evidence. Identify source review/thread/check, relevant commit, disposition and owning issue. Simultaneous CI/review feedback must both survive. Ordinary PR comments and aggregate reviewDecision are not full inline-review evidence.

B3. Deliver feedback through bounded follow-up execution. Reuse/extend #15's PR-frontier path, dependent on its existing manager prerequisites. Include relevant feedback in the worker brief; repeated observations must not duplicate dispatch; failed/suppressed delivery must not become success. Preserve fresh gate/review and human veto. Do not inject unsolicited text into persistent terminals.

B4. Render authoritative lifecycle history in existing Timeline/Attempts. Show real executions, retries/review loops, earlier outcomes and explicit legacy/truncation gaps. Current artifact-derived timing must not be dressed up as precise telemetry. Re-ground after F01 because it may already migrate some consumers.

B5. Link human decision receipts to later claim/execution/outcome. Routing applied is not worker started. Preserve waiting/no-claim explanations and link by causal identity, not timestamp proximity. Depends on accepted lifecycle and decision identity contracts.

Serialize shared dashboard/dispatch edits. B2 evidence work and independent District work can proceed concurrently where ownership is disjoint; B3 consumes produced evidence/manager contracts; B4 consumes lifecycle; B5 consumes stable causal identities.

## AO opportunities and cautions

Reference: https://github.com/Untrivial-ai/agent-orchestrator

Read implementation, not only README:
- `backend/pkg/contract/kanban.go`: separate derived column from specific display status; current-head versus historical reviews; next-step ownership. Its ready lane semantics are NOT Factory merge eligibility.
- `backend/internal/lifecycle/reactions.go`: independent CI/review/conflict reactions, deduplication, delivery guards, self-resolving readiness notifications and completion distinction.
- `backend/internal/observe/scm/observer.go`: persisted observations and acknowledgement/retry behavior.
- `backend/internal/service/review/review.go`: worker/current-head review delivery.
- `backend/internal/service/session/delegation.go`: direct worker creation. ApprovalMode means agent permissions, not business-plan approval.

Use GitHub file_read for repository files. Borrow feedback provenance, current relevance and acknowledgements, not AO's desktop shell, SQLite/session authority, perpetual coordinator, terminal injection or broad ready-lane policy.

## Verification and release

Each runnable issue has Scope / Touches / Exit gate / Out of scope. Use normal needs-triage intake, never bypass directly to ready-for-agent without authorization. Same-repo blockers use real issue numbers. Cross-repo installation/release remains an explicit checkpoint, not a bare cross-repo number.

When implementing, orchestration owner verifies; subagents skip tests/build/lint/formatting. Run actual repository gates (not generic Bun commands): last observed Factory `python -m unittest discover -s tests`; District `uv run python -m unittest discover -s tests`. Re-read configuration before use.

End-to-end disposable scenario:
1. Worker finishes with no reported CI: exact blocker displayed.
2. CI fails and a reviewer requests changes: both sources visible.
3. Authorized bounded follow-up receives applicable feedback once.
4. Newer head retains old feedback provenance without calling it current-head evidence.
5. Gate/review succeed: actual merge eligibility explained.
6. Merge completes: history and decision receipt link to outcome.
7. District distinguishes project feedback from machinery failure throughout.
8. Reconnect/partial evidence never replays old transitions or fabricates health.

Additional existing gates: concurrent execution lanes; unknown resource holder; interruption reconciliation; source freshness; genuine non-loopback read-only verification; browser Back/scope; keyboard/narrow viewport/reduced motion; human visual acceptance. Keep the full approved spec acceptance list, not a sample of it.

No production schedule changes or unsafe failure experiments. No deployment from issue closure alone. No commits/pushes/security changes beyond explicit authorization or established workflow.

## Future exploration path

Desired later: explore outcome → compare approaches → approve concrete issue set → normal triage. Preserve durable decisions/constraints/proposed issues. Exploration must not implicitly dispatch. Revisit after operational and feedback loops are accepted; do not build it now.

## Orchestrator operating contract

Read and enumerate first. Dispatch substantial independent writing slices together, with explicit paths, ownership, inputs, outputs and non-goals. Do not delegate top-level interpretation. Never launch competing writers on shared files. Verify every claimed result; fix through scoped corrective work; advance without unnecessary phase-boundary handoffs. Stop only at requested scope completion or a concrete external authorization/checkpoint. Distinguish planned, implemented, verified, installed and operator-accepted states in the final report.
