# Agent system contracts

Status: **proposed contract changes, not implemented behavior or permission**. Read [the system design](agent-system-design.md) for purpose and [the delivery plan](agent-system-plan.md) for ownership and gates. [Grounding](agent-system-grounding.md) distinguishes canonical source, local work and installed observations.

## 1. Extend the existing surface; do not create another controller

The current shared reader is `factory evidence --root <operator-selected-main-checkout>`, with schema-1 `capabilities`, `observe`, `inspect` and bounded `investigate` operations. `factory dashboard --runtime-json` is a separate network-free, non-persisting path. The full dashboard snapshot can persist reconciliation and probe a model endpoint; it is **not** a substitute for either read-only contract.

Keep those distinctions. Extend the evidence module to serve decision-ready projections. C3 owns the proposed guarded action interface; B5 owns causal receipts; #15 owns sustained Factory-PR feedback handling. The console and dashboard adapt these interfaces rather than interpreting GitHub semantics themselves.

| Agent need | Existing surface / proposed extension | Effect class |
| --- | --- | --- |
| Establish scope and support | `capabilities`; add verified reader/build identity and supported object/link kinds. | Local read; no provider probe or auth changes. |
| Find relevant work | `observe`; add explicit selection/coverage and compact blockers/change references where producers support them. | Bounded read. |
| Understand one object | `inspect`; add typed references, contract links, exact evidence applicability and available next inspections. | Bounded read. |
| Resolve a specific unknown | Existing `investigate` kinds; extend only for an accepted case such as retained execution history or exact code neighborhood. | Bounded read with declared cost/coverage. |
| Decide what could happen | C3 `prepare` semantics over its accepted action menu. | No operational mutation; may durably save an immutable local proposal, explicitly disclosed. |
| Carry out an authorized decision | C3 `apply` semantics; B5 causal receipt. | Guarded mutation through the existing owner. |
| Recover or continue | Inspect the recorded decision/execution; obtain current applicability and next permitted step. | Read; any reconciliation mutation stays with its separately authorized owner. |
| Inspect an earlier improvement | Proposed typed `inspect` target for a scoped lesson or plan amendment, using existing C5/notes or initiative/decision sources. Return its evidence, applicability and proposed/accepted/rejected/deferred/superseded disposition. | Bounded read; legacy notes without acceptance provenance remain unclassified guidance. |

`prepare` and `apply` here name the C3 interface semantics, not commands available in the installed CLI. Final transport spelling belongs in the same C3 contract and implementation, not a competing `agent` CLI. The Python CLI remains the default transport; no HTTP/MCP server is required for agents to use it.

### Compatibility

Schema-1 request validation is strict. Do not send new fields and hope an old reader ignores them. Advertise new request/semantic support explicitly, use a new negotiated version for changed meanings, and migrate dashboard, console and affected automation in one coordinated release. Keep schema-1 behavior unchanged during preparation; once the new contract is accepted and its consumers cut over, remove obsolete implementations rather than leaving permanent translation layers. Unsupported installed versions fail explicitly and keep read-only inspection where genuinely supported. Deployment acceptance is separate from source acceptance.

The schema-1 `capabilities` response is the designated discovery point: it may gain additive metadata advertising supported protocol versions and object kinds while existing keys keep their exact meanings and `actions:[]` remains unchanged. Consumers tolerate unknown response keys. New request fields/operations or changed meanings require an explicitly advertised negotiated version; probing an old reader with speculative fields is not negotiation.

## 2. Identity and relationships

Use native identities and existing lifecycle IDs. A reference is **provider/host + repository identity + object kind + native ID**, plus a revision when the question is revision-specific. Display numbers and paths are locators, not universally unique identities. Prefer existing provider identity strings; a representation must not silently round large IDs through JavaScript numbers.

| Object | Identity / applicability |
| --- | --- |
| Repository | Provider host and native repository ID when available; explicit slug retained. Root is operator-selected, not supplied by model evidence. Rename/transfer without a verifiable identity match needs re-selection. |
| Initiative / issue / PR | Kind and native ID plus repository identity; number retained for display. Ordinary issues have no invented initiative. |
| Work contract | Complete relevant canonical content/digest and recorded admission provenance; initiative-linked slices use accepted #57 binding. `updatedAt` alone is not a scope revision, and a digest alone is not approval. |
| Execution / transition | Existing `execution_id`, `parent_execution_id`, `root_execution_id`, `dispatcher_run_id`, `event_id`, per-execution sequence. Never synthesize a claim from a stage log. |
| Code / verification | Commit/blob plus path/symbol when appropriate; assessed head and gate/review execution identity. Branch name alone is insufficient. |
| Feedback | B2 native `evidence_id` and `source_revision`, with source head, observed head, relevance and disposition retained. |
| Decision / result | Existing decision identity extended through the owning action contract; exact request, authority record, effect identity and linked execution/result. |
| Retained artifact | Complete bytes digest plus producer execution, source/head, capture event and storage status. Same bytes observed twice may share content storage but not observation identity. |

Legacy work may lack a frozen admission contract. Expose `unbound`/unknown provenance and the current mutable issue description rather than retrospectively certifying it as an accepted revision. Read-only inspection remains useful; an action requiring stronger binding stays unavailable until its owning admission contract supplies it.

Use only relationships whose producers can support their meaning:

| Relationship | Meaning / owning evidence |
| --- | --- |
| `contributes_to` | Work links to an initiative under its accepted implementation-links contract; not proof of delivery. |
| `depends_on` | A declared work prerequisite, with source and the required completion predicate. Existing `Blocked by` semantics remain unchanged until their owner accepts a change. |
| `attempts` | An admitted execution attempts a particular work contract. |
| `produced` | An execution captured an artifact/result. Does not certify the artifact's claims. |
| `supports` / `contradicts` | Evidence relates to a named claim or acceptance criterion. Deterministic relation or attributed judgment must be identified. |
| `supersedes` | A later source/accepted decision replaces applicability of an earlier one, without erasing history. |
| `delivered_in` | Verified containment in a release/install/publication result, under that stage's own authority. |

A code `calls`/`imports` edge remains a structural edge from its extractor, not `depends_on` work scheduling. A model-proposed relationship is a hypothesis, not an accepted graph edge. Inverse links are derived views; no second canonical graph store is needed. Conflicting links are shown with sources rather than silently reconciled by the model.

## 3. Decision-ready inspection

The proposed selected-work view has these semantic fields. This is a projection of existing owners, **not a collection of new persisted record types**.

| Field | Required meaning |
| --- | --- |
| `identity` | Explicit target and reader identity, schema support, collection time and scope. |
| `purpose` | Recorded objective, exclusions and success-evidence pointers, with accepted versus mutable/unbound provenance; initiative/decision owner if present. |
| `basis` | Referenced source revisions, contract/head/policy versions and independent coverage. |
| `state` | Separate admission, execution, review/integration and delivery facts. Unknown values remain unknown. |
| `changes` | Relevant differences against a specifically named earlier basis; unavailable comparison is explicit. |
| `blockers` | Known unmet predicate, evidence, owner or unknown ownership, and wake condition. |
| `affordances` | Implemented next reads and supported action proposals; permission and current eligibility separately stated. |
| `resources` | Existing limits, consumed/remaining known budget, wait/resource evidence and measurement coverage. |
| `links` | Bounded routes to full contract, sources, executions, retained results and relevant dependents. |

**Facts required for action cannot be recovered by parsing a shortened prose summary.** Display text may be shortened, but the canonical contract and applicability facts must be complete or that action is unavailable.

### A compact illustrative rendering

This is an invented example, not an observation of a real issue or an existing wire response:

```text
example/widgets · issue 417 · PR 420
Purpose: preserve report data across cancellation. Contract: accepted revision R3.
Current: worker completed; review approved head H1; remote head now H2.
Change: H1 → H2. Prior approval is historical, not applicable to H2.
Blocked: fresh gate and review of H2 required. No merge readiness claimed.
Next reads: inspect H1..H2 diff; inspect H2 checks; inspect recorded refresh result.
Action: prepare existing refresh/reverification path if current policy permits.
Budget: remaining worker allowance unknown; inspect owning ticket record.
Evidence: exact-head approval record; remote PR observation; partial history notice.
```

The machine representation uses actual immutable IDs/SHAs, not the display aliases above. It does not let an `approved` label override the head mismatch.

### Reader identity matters

Expose separately: imported engine build/revision when verifiable; package version; evidence/runtime schema support; observed repository root and code revision; configured integration target; installed-service identity **only if actually observed**. Unknown service identity stays unknown. A source checkout, installed CLI and running service may be different programs. Discovering one cannot certify the others.

Capabilities describe actual behavior, including whether a read performs network access, its bounds, and unsupported operations. Do not leak full host configuration or secrets to make identity legible.

## 4. Evidence, freshness and change detection

Preserve each producer's original identity semantics:

- C1's outer `observation_id` is fresh per read.
- B2's `observation_id` is content-derived and stable across equivalent feedback observations.
- Lifecycle `event_id` identifies a transition; sequence orders one execution, not the whole fleet.

Do not unify these by field-name coincidence. An enclosing projection may identify its own read and comparison basis, while keeping producer name/schema/IDs intact. A comparison digest names the selected decision inputs, not “the state of the world.”

Each action defines its **relevant basis**: repository/target, complete admitted contract revision, PR head and target branch where relevant, ownership/hold/human-intervention evidence, applicable policy revision, verification and resource preconditions. Reading these sources is not an atomic distributed snapshot. Revalidate at application under the owning coordination mechanism and use provider-side preconditions wherever supported.

| Change | Consequence |
| --- | --- |
| PR head changes | Prior gate/review/approval stays historical; fresh applicability required. |
| Scope section changes outside a truncated display prefix | Complete canonical digest changes, or admission is refused as incomplete. Display digest cannot authorize. |
| Human takeover or relevant hold changes | Pending proposal is ineligible/stale; no automatic unassignment or hold removal. |
| Provider feedback body/resolution/check attempt changes | B2 source revision/identity changes under its existing rules; consumer evaluates that revision, not every poll. |
| Source refresh fails | Retain prior evidence as historical with original time; do not acknowledge disappearance or retry an effect. |
| Unrelated comment/presentation ordering changes | Do not invalidate unrelated cached code evidence or manufacture new feedback. Existing conservative takeover policy still applies where it treats that human activity as relevant. |
| Extractor/model/prompt changes | Cached derivations or evaluation claims tied to the old producer remain historical; no automatic promotion. |
| Journal rotates, truncates or has a gap | Comparison coverage is incomplete; old cursors cannot establish “nothing changed.” |

A complete source can support a negative fact within its declared scope. A capped list cannot prove absence outside its prefix. A digest over truncated bytes proves only those bytes. Hashes establish integrity/identity, not human acceptance.

### Bounded history without amnesia

The existing runtime tail is deliberately bounded. Add targeted history retrieval only if a decision cannot be answered within it. First prefer bounded lookup of a named execution/decision using retained source evidence. If scans become material, the journal owner may maintain a rebuildable index/checkpoint with source identity, committed offset and integrity checks. Readers do not create or repair it. Missing/corrupt/stale indexes fail to partial evidence or an explicitly bounded source scan; they never invent closure of an old execution.

An index is an optimization, not a mandatory new store. Complete historical context cannot be promised after source retention has expired.

## 5. Work boot and resume contract

Extend the existing claim-time brief and `build_prompt`; do not create a second prompt composer.

| Always present for an admitted worker | Load on demand |
| --- | --- |
| Explicit repository/worktree, admitted work identity, current head/base, supported tools and hard limits. | Source bodies, history, broader architecture, detailed logs. |
| Accepted objective, scope/exclusions, acceptance criteria and verification command; complete contract reference. | Neighboring code and accepted preceding implementation details. |
| Execution/attempt identity and exact reason for this invocation. | Relevant scoped lessons with applicability and evidence. |
| Current actionable feedback, relevant changes since prior attempt, unresolved uncertainty and stop condition. | Alternative approaches, rejected suggestions and their reasons when the same choice recurs. |
| Pointers to retained prior result and next result destination. | Domain/operational recipes selected for this task, not all global skills. |

The prompt distinguishes trusted standing instructions from quoted issue/comment/source evidence. If a required contract cannot be read completely, report the concrete missing prerequisite; do not execute a narrowed interpretation. Context availability cannot grant extra tools, credentials or authority.

A resume packet is **current contract + new facts + last recorded checkpoint**, not a transcript replay. It identifies what was actually attempted, which artifacts exist, what remains unverified, and the next discriminating check. Prior narration cannot prove current liveness, a clean worktree, or an external effect.

Packet size is a measurement target, not an excuse to omit mandatory facts. Proposed first trial ceiling: 24 KiB of additional brief/continuity text beyond the accepted task contract and standing instructions. Include byte counts and explicit omissions; measure actual model tokens where available. If mandatory data exceeds a bound, provide an exact retrievable contract and require its acquisition before action, or refuse admission. Do not duplicate the full contract across every layer.

Different roles receive different relevant evidence under the same model:

- **Worker:** executable slice, local code seam, acceptance and current required feedback.
- **Reviewer:** approved scope, exact candidate diff and independent validation; worker claims labeled as claims.
- **Manager/operator:** objective, blockers, alternatives, resource limits, decisions and current eligibility.
- **Outcome planner:** accepted plan plus successful discoveries and unresolved downstream assumptions; not every repair log.

These are context selections for existing roles, not new resident agents. Tool inspection remains available where permitted; a concise packet should reduce unnecessary exploration without suppressing necessary independent review.

## 6. Retained result and upward feedback

Capture a small manifest and the complete available result artifact **before the producing worktree is removed**. Reuse existing journal/artifact conventions and the current handoff pilot's integrity lessons. Capture is not a new verification authority.

Manifest semantics:

- Producer execution/root/attempt, repository/work contract, actual resulting head.
- Artifact content digest, byte length, capture time/event, storage/read status and completeness.
- Claimed changes and remaining uncertainty, explicitly attributed to the worker.
- Links to actual gate/review results for that head and the criteria they support.
- Any proposed downstream contract impact, linked to its target and supporting source.

Write complete bytes atomically and durably, then record their reference through the journal owner; verify the reference before cleanup. A crash between storage and reference may leave an unreferenced blob, not an invented successful capture. Replays reuse the recorded observation and bytes; two events with identical bytes remain two observations. Failed capture is explicit, with bounded retry under the retention owner; preserve available source until its accepted retention/cleanup policy decides otherwise. Retention failure does not turn a successful gate into a failed gate, and must not cause unlimited worktree growth.

Retention policy must name per-artifact and repository byte/time ceilings, permitted readers, redaction/disclosure policy, expiration and removal procedure before deployment. Required-but-oversized evidence is marked incomplete/unavailable; never labeled complete after clipping. The read-only protocol does not execute historical code or dereference arbitrary host paths.

### Proposed refinement, not automatic replanning

A downstream suggestion contains only: target contract/owner; evidenced observation; concrete proposed delta; consequence; smallest resolving check; disposition (`proposed`, `accepted`, `rejected`, `deferred`, or `superseded`) and its attributed decision when one exists. It does not need an essay or a new global planning object.

Store the authoritative accepted amendment with the existing plan/issue owner. Link to the captured evidence instead of copying the whole report. Admission consumes only the newly accepted revision through #57's contract. A hold stays held until its own authority releases it. A general lesson must first survive corroboration and the qualification process in the plan.

## 7. Guarded action and receipt contract

This section sharpens C3/B5, not the manager's autonomous permissions. Begin with the smallest already-approved action menu and preserve human-only actions as human-only. No label, comment, model field, transcript, or retained lesson can mint authorization.

### Prepare

Validate a closed operation and target; obtain relevant current evidence; enumerate exact external and local effects; identify eligibility, required authority, failure modes and reversibility. Every proposal reachable through `apply` must first be stored immutably and durably by the trusted owner. Return its ID/fingerprint, relevant basis, validity limit, costs/limits, and safe alternatives such as further inspection or waiting. An unstored preview is inspection-only and has no applicable proposal ID.

Human confirmation is bound by a trusted host control to the exact proposal, target, scope and values. The model may request preparation but has no callable “approve” operation. Autonomous actions instead cite an existing, bounded policy grant and its revision; this is a different authorization source, not fabricated human confirmation.

### Apply

1. Resolve the stored proposal and authenticated/trusted authorization outside the model-controlled payload.
2. Acquire the existing owner coordination and re-read all relevant preconditions. Refuse stale, ineligible, unsupported or unauthorized requests before effects.
3. Persist intent and consumed allowance before external effects. Failure to persist means no action.
4. Execute only the proposal's permitted steps through the owning executor. Track child/lifecycle identity where applicable; preserve operation-specific deadlines and output bounds rather than blindly swapping subprocess helpers.
5. Record actual step results, external native IDs and resulting execution/claim identities. Distinguish admission from completion.
6. On replay, return the recorded result or reconciliation requirement; do not repeat an ambiguous external mutation.

Proposed shared receipt states reuse `started`, `success`, `partial` and `failure` from existing human-decision records, adding `rejected` and `unknown` where supported. `success` means the named operation succeeded: an enqueue receipt is not a worker result. Historical rows keep their original producer/schema and status; missing old binding or recovery evidence remains explicit, not retroactively certified. A final receipt that could not be persisted leaves a discoverable started/unknown action and requires reconciliation even if stdout reported success.

Do not promise distributed exactly-once behavior. A local lock and journal cannot atomically commit a GitHub comment plus a label update. An action interrupted after the comment may be partial. Reconciliation checks provider identity and resulting state; it does not search text and guess ownership. Provider idempotency/preconditions are used where available; where absent, ambiguity stops replay. Existing expected-head merge protection does not atomically protect a late branch retarget or human veto; independent repository protections remain necessary.

Cancel before effects means no action. Cancel after effects begin stops future work only where the owner supports it and then reports/reconciles what happened. Releasing a lock, killing a process, or rolling back is an independent authorized action, not an automatic interpretation of “cancel.”

## 8. Budgets, waits and termination

Use existing configured limits as authority. Proposed additions clarify accounting; they do not raise any limit.

| Concern | Contract |
| --- | --- |
| Delegation | One accepted work contract and one execution owner; child work carries causal identity and explicit allowance. A new attempt/head/session does not reset the ticket's consumed allowance. |
| Cost | Sum known provider/attempt costs with attribution and coverage. Reservation uses configured worst-case/time limits where price data is unavailable; never claim a hard dollar cap without enforceable metering. |
| Concurrency | Count actual admission ownership, not lifecycle stage count. Respect existing per-ticket/merge and host-exclusive locks. |
| Waiting | Record reason, source, owner if known, wake condition and bounded reconsideration policy. Unknown ownership is not permission to steal a lock. |
| Progress | A new applicable result, resolved uncertainty, accepted contract revision or satisfied prerequisite is progress. Repeating the same action on unchanged evidence is not. |
| No progress | Same input revision and unchanged failure do not justify another model call. Stop under existing limits; propose a new discriminating read or report the terminal gap. |
| Recovery | Surviving child, uncertain launch or partial effect requires inspection/reconciliation; elapsed time alone never proves death. |
| Scheduling | Existing eligibility first. Any later ranking explains declared dependency leverage, resource fit and fairness; inferred code edges cannot create prerequisite authority. |

No persistent model is needed to wait. Existing timers/passes can observe the trigger; source revisions and durable consumption state prevent repeated judgment. Avoid nested full analyses when a deterministic status check settles the branch.

## 9. Security and human ergonomics

- The model's data plane contains untrusted repository text, issue comments, logs and extracted relationships. Only trusted code and explicit accepted policy control capabilities/effects.
- Tool allowlists, prompt warnings, worktrees and lifecycle records are not OS isolation. Reuse #3/I1 for separately selected and qualified filesystem/process/credential/network containment, including an explicit disposition for reviewer and gate execution.
- Provider disclosure is an independent decision. Read locally first; send only approved scoped evidence to the selected provider. Redaction is not a blanket DLP claim.
- The human sees concise recommendations and exact consequential effects, not an obligatory full report. Evidence links remain inspectable. Unknown/partial state has words, not color alone.
- Console and dashboard preserve keyboard use, clear cancellation, visible scope changes and stale-proposal refusal. Changing repository clears pending proposals and refreshes context.
- Read-only fleet navigation selects one repository through District. Bulk mutation and multi-runner coordination are outside this contract.

Acceptance scenarios, measurements, sequencing and removal criteria belong in the [implementation plan](agent-system-plan.md), rather than being repeated here.
