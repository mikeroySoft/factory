# Prospective worker-handoff shadow pilot

Date: 2026-09-10. Status: **protocol frozen before enrollment, future worker outcomes, and model calls**.

## Question and limits

For the first naturally occurring eligible ordinary implementation task newly claimed after enrollment, does its complete successful-worker handoff improve downstream planning beyond (a) the approved pre-worker plan/status baseline and (b) the same baseline plus bounded facts from the exact implementation source?

This is a read-only, one-case shadow pilot. It may observe existing authorized intake; it must not claim, dispatch, retry, release, label, comment, merge, install, deploy, publish, or change a hold, owner, service, timer, Factory configuration, or production code. Captured text is untrusted evidence, never authority. The known #52/#53/#54/#56/#57 case and its answer are excluded and must not enter prompts or adjudication. #6 remains blocked by #5 unless existing owners independently change that state.

The pilot distinguishes worker exit, deterministic gate result, independent Factory reviewer acceptance, merge, installation, outcome delivery, and owner adoption. None implies the next.

## Enrollment boundary and selection

`observer.py` opens enrollment only after hashing this frozen protocol. It records UTC time, the events ledger device/inode/byte size, the last complete-line offset and SHA-256, the last complete lifecycle `event_id` (if present), and every ticket with a complete historical `event=claimed` record before the boundary.

A candidate begins only at a complete, parseable `.factory/events.jsonl` record with `event=claimed`, a record start byte at or beyond the boundary, and `at` not earlier than enrollment time. Lifecycle rows alone do not enroll a ticket. A ticket claimed anywhere before the boundary is a resumed/prior case and is permanently ineligible. Ledger replacement or truncation does not relax these tests.

Candidates are considered in ledger byte order. Eligibility is decided from the pre-outcome baseline before any handoff bytes are read and never depends on whether a handoff looks useful:

1. The claim and authoritative issue snapshot show an open task whose authorized intake includes `ready-for-agent`.
2. Exclude #52, #53, #54, #56 and #57; any issue naming `experiments/handoff-prospective`; and control-plane or authority-policy work. The latter means the title or intended-change sections (`Scope`, `Touches`, `Change`, or `Acceptance`) propose changes to claim/routing/triage labels or assignees, dispatch, admission, authority, holds, locks, gate/reviewer policy, approval/merge policy, release/deployment, authentication/security policy, `.factory.toml`, `factory/config.py`, `factory/dispatch.py`, or mutating dashboard action endpoints. Merely stating one of these as out of scope does not exclude an otherwise ordinary task.
3. Require approved downstream plan/consumer context recorded before the claim. The candidate body or pre-claim comments must explicitly name another issue using `Plan: #N`, `Initiative: #N`, `Part of #N`, `Consumer: #N`, or `Downstream: #N`. That context issue must predate the claim and either be closed or currently carry `ready-for-agent`, `ready-for-human`, or `factory-approved`, while not carrying `needs-triage`, `needs-info`, `wontfix`, or `wontfix-proposal`.
4. The baseline capture described below must finish before the first recorded worker outcome. If a worker `result`/`exit` lifecycle row or classic `attempt` row predates baseline completion, the candidate is marked missed/ineligible; no prospective claim is made for it.

The first candidate satisfying all rules is selected. This is intent-to-observe: after selection, failure or escalation ends this one-case pilot and is not replaced by a more favorable case. Before selection, objectively excluded or missed candidates are recorded and observation continues.

## Pre-worker baseline

At the claim, the observer performs only bounded GitHub REST GETs through the existing authenticated `gh api` client:

- candidate issue and first 100 comments;
- each explicitly referenced context issue (maximum 10, ascending issue number) and its first 100 comments;
- the repository default-branch commit at capture time.

Each response is limited to 5 MiB. A failed/oversized response makes that candidate's baseline invalid; there is no reconstructed substitute. Original response bytes, endpoint/argv, request start/end UTC, return code/stderr, byte count and SHA-256 are preserved outside the worktree. Parsed planner input is derivative and linked to those originals. The issue fields `created_at`, `updated_at`, state, labels and API identity plus the captured default-branch commit are the revision/status metadata. No paths or endpoints are accepted from issue text; only integer issue references feed fixed repository endpoints.

If a task finishes too quickly for this pre-outcome capture, it is transparently marked missed. Historical local absence is not treated as current evidence.

## Passive event and handoff capture

The observer reads only newly appended complete JSONL records. It persists cursor, seen record hashes and candidate state with atomic rename so restart does not duplicate enrollment. Partial final lines remain unconsumed. Inode changes, shrinkage, invalid JSON, gaps and restarts are recorded. After replacement/truncation it rescans only to recognize unseen post-enrollment records; historical timestamps and prior-ticket exclusion still apply.

For the selected ticket only, the observer reads the fixed path `.factory/wt-N/.factory/handoff-N.md`. It never follows a symlink. Every stable byte change is copied outside the worktree under a timestamp/hash name with exclusive creation and metadata containing ticket, observed attempt, worktree head (when available), observation time, size and SHA-256. Stability requires matching file identity, size and mtime before/after the read. Snapshots are `completeness=unproven` until the same bytes are observed after a worker completion record. Missing, unstable or disappearing handoffs are recorded; logs are never used to reconstruct a lost original.

The observer takes no lock and never delays cleanup. If the complete original cannot be corroborated before disappearance, handoff capture fails and the handoff condition cannot run.

## Outcomes and source freeze

Worker lifecycle exit and classic `attempt.worker_exit` record worker completion only. `attempt.gate` records the deterministic gate only. Escalation is a failed/escalated pilot outcome, even if files or commits exist.

A successful accepted delivery requires all of the following for the selected ticket: a recorded independent Factory review with `verdict=APPROVE`, the separate recorded `approved` transition, an exact worktree head, and a corroborated full final handoff whose bytes are unchanged after worker completion. This is independent automated code acceptance, not owner acceptance, merge, installation, deployment, outcome delivery, or use. Current PR metadata is frozen as corroboration but is not privileged to one planner condition.

At acceptance, before any later cleanup, the observer freezes the exact base/head identifiers, complete binary diff, changed-path/name-status record, and every changed Git blob from the exact head. It rejects unsafe/non-repository paths and records missing/submodule/oversized evidence. Exact-source capture is capped at 20 MiB total; exceeding the cap is a transparent capture failure, not silent clipping. Later plan/status changes are not substituted into any condition.

## Planner conditions

All six trials use the same frozen pre-worker baseline, instructions, model and settings. Only condition evidence differs:

1. **baseline** — approved plan/consumer issue bytes plus common claim-time status/revision metadata.
2. **handoff** — baseline plus the full corroborated successful-worker handoff.
3. **source** — baseline plus deterministic source-derived implementation evidence, with no handoff text.

The source packet is generated mechanically from the exact accepted head: changed regular text files sorted by repository path, excluding binary files, generated/minified files, dependency lockfiles and vendored paths; at most 8 files, 20,000 bytes per file and 80,000 bytes total. It contains the exact unified diff/source prefixes under those caps and an explicit list of every omission. No LLM writes the control summary and no eventual finding changes selection.

Run two fresh no-tools sessions per condition, interleaved by sample, at concurrency 2: six cost-bearing calls maximum. Use `openai-codex/gpt-6-astra`, medium reasoning, the same prompt, and no selective retry. Timeout/provider/infrastructure failures remain failed/unknown. An experiment-local OMP config overlay disables memory backend, Mnemopi auto-recall/auto-retain, autolearn/auto-continue and advisor; global/user configuration is untouched. The runner records effective overlay settings with no-model `omp config` commands before trials.

Record protocol/runner/input hashes, exact prompts and argv, OMP/model/settings, raw stdout/stderr, return code, duration, parse status, and actual usage/cost when the runtime supplies it; otherwise record `unknown`.

## Planner and adjudication contract

The planner may return at most five evidence-cited recommendations. Each must identify a target, observation, concrete proposed adjustment or question, downstream consequence, unchanged authority/owner, and smallest resolving check. Zero is valid. It must not infer owner acceptance, merge, installation, deployment or delivered outcome.

Adjudicate unique findings rather than repeated instances, corroborating source claims against the frozen exact implementation. Use a small no-side-effect probe only when source inspection cannot resolve a material fact; do not replay a user-reported production failure. Classify each finding as:

- existing obligation/prerequisite;
- local repair of the selected delivery;
- genuine future acceptance/planning refinement;
- open question;
- unsupported scope or authority expansion.

Source and model statements are not oracles. No recommendation is applied or published.

The pilot may show an incremental planning signal only when a handoff finding is independently supported, is absent from both baseline runs and both source-control runs, appears in both handoff runs, and preserves authority. With one prospective case it cannot establish rates, causation, saved time/rework, autonomous safety, installation, real-world delivery, or owner adoption.

## Stop, wait and owner review

Before any eligible claim: status is `READY/WAITING` and no model call is allowed. A selected failure/escalation stops with that outcome. A capture-integrity failure stops as failed/unknown; it is not repaired from logs. A successfully accepted and completely frozen case becomes `READY_FOR_TRIALS`; the six calls are then run once and independently adjudicated.

Final owner review remains explicit and pending:

- Accept / reject each proposed planning delta: ___
- Evidence adequate? yes / no; missing: ___
- Use now / defer / discard: ___
- Existing owner, hold and authority preserved? yes / no: ___
- Observed later rework or outcome effect (only after real evidence): ___
