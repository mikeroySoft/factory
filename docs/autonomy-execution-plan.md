# Factory whole-autonomy execution plan

Plan date: 2026-09-10. Source baseline: `428a03e3262b58b63e82b0c33f71123e510640fc`
on branch `autonomy-safety`.

This is an execution plan, not blanket authorization. **Only S0, the merge-safety tranche,
is authorized now.** Every other unit is `PLANNED — HELD` until a human authorizes that unit
or wave. This plan does not change issues, labels, holds, queues, services, providers, security
policy, releases, installations, deployments, or GitHub settings.

The local IDs in this document (`S0`, `A1`, `I1`, `L01`–`L12`, and `B1`–`B5`) are planning
handles, **not GitHub issue numbers**. A future runnable dependency may name only a real,
verified same-repository issue number. No held work becomes runnable because it appears here.

## 1. Sources, baseline, and truth rules

Planning inputs:

- the source and repository gate at the baseline above;
- [`docs/manager-plan.md`](manager-plan.md), including its implemented manager history;
- the historical local B1–B5 addendum, used as planning input only and not changed by this plan;
- live Factory issues [#3](https://github.com/mikeroySoft/factory/issues/3),
  [#5](https://github.com/mikeroySoft/factory/issues/5)–[#9](https://github.com/mikeroySoft/factory/issues/9),
  [#15](https://github.com/mikeroySoft/factory/issues/15),
  [#34](https://github.com/mikeroySoft/factory/issues/34), and
  [#52](https://github.com/mikeroySoft/factory/issues/52)–[#59](https://github.com/mikeroySoft/factory/issues/59);
- the intake and delivery gap assessments prepared for this programme.

Truth is recorded by boundary, not inferred:

- proposed, queued, claimed, executed, gate-passed, reviewed, approved, merged, released,
  installed, publicly published, used, and outcome-delivered are different states;
- issue closure, child closure, PR merge, a label, a model statement, or a successful process
  exit proves only its own state;
- missing, partial, legacy, stale, contradictory, or unavailable evidence remains explicit and
  cannot be promoted to success;
- live GitHub state and accepted source revisions outrank this dated plan at execution time.
  Re-read issue body, labels, assignees, PR head, checks, locks, and source before each release.

The designated baseline contains the current dispatch, triage, manager, dashboard, evidence,
lifecycle, runtime, stats, and learning modules. Live #53 is closed, but its `factory plan`
implementation is not present in this baseline (`factory/plan.py` is absent). A future consumer
must first integrate or otherwise verify the accepted #53 source revision; issue closure alone is
not an implementation claim for this branch.

The repository gate of record is exactly:

```sh
python -m unittest discover -s tests
```

It comes from `.factory.toml`. No wave installs `pytest` or edits verification policy merely to
make historical ticket text pass.

## 2. Crosscutting contracts

### 2.1 Lanes and independent acceptance

| Lane | Use | Required control |
|---|---|---|
| **Direct** | Control plane, action authority, admission, verification, merge semantics, release policy, host isolation, and recovery code | Operator-owned non-`agent/<n>` branch/PR. The normal Factory merge selector cannot land it. Independent non-agent Spec and Standards review, the repository gate, green CI, an exact-head check, and explicit human approval are required. |
| **Factory** | Bounded read-only producers, ordinary consumers, presentation, content generation, and other work that cannot weaken or grant its own authority | Normal ticket → worker → gate → independent reviewer → PR path. The implementer/model cannot approve its own work. Control-plane owners review integration before merge. |
| **Human checkpoint** | Policy choice, hold release, real-account acceptance, release, rollout, install, provider/security changes, and external publication confirmation | No autonomous mutation. Record the exact approved target and scope at the point of action. |

A Factory-lane ticket that grows into authority, verification, or release-policy work stops and
returns for re-scope into the Direct lane. Conversely, a read-only consumer does not get a Direct
controller merely because its input comes from the control plane.

### 2.2 A1 — authority enforcement prerequisite

**Outcome.** Action authority is enforced at every fresh owning boundary, not described only by
labels, prompts, branch names, or comments.

**Existing implementation.** The manager has a closed, code-applied decision menu; issue text is
treated as untrusted; human activity stops manager action; gate, reviewer, CI, current-main,
locks, and human requested-changes are separate merge conditions. The dashboard previews exact
human actions. These controls must remain.

**Gap.** Initiative and programme holds are presently descriptive until #55 is accepted. Forced
`--ticket`, stale frontier data, a shared GitHub login, manager prose, or a manually applied label
must never manufacture authority. Verification config and release operations need an explicit
direct-only boundary. The current `factory-held` label is not itself an enforcement mechanism.

**Owner/reuse.** Reuse #55 for initiative-kind exclusion after its existing dependencies. Reuse
#56/#57 for their narrower routed-handoff and plan-binding controls. Do not create a competing
hold framework.

**Paths.** `factory/triage.py`; `factory/dispatch.py` admission, refresh, review, approval, and
merge boundaries; `factory/manage.py`; the accepted plan reader from #53; `factory/dashboard.py`
action application; `factory/config.py`; focused tests and existing user documentation.

**Dependencies/lane.** Direct. #55 remains blocked by #53 and #15 exactly as its live issue says.
#53 source acceptance and the #15 ownership handoff are real checkpoints. A1 does not authorize
removing #55's `factory-held` label.

**Acceptance scenario.** In a disposable repository, attempt worker admission, manager action,
manual-label approval, forced dispatch, refresh, and merge against: an initiative, a held issue,
a changed head, ambiguous ownership, human intervention, an active foreign lock, and a request
to alter verification or release policy. Every path re-reads authoritative state and refuses with
a specific reason before mutation. The same ordinary ticket still follows the existing path.

**Status.** `PLANNED — HELD`; no issue, label, or branch mutation authorized.

### 2.3 I1 — execution isolation prerequisite

**Outcome.** A worker receives only the repository/worktree, credentials, network, process, and
resource access explicitly required for its ticket; compromise cannot silently become host or
fleet authority.

**Existing implementation.** Ticket/worktree and host resource locks limit concurrency, but
workers still run as host processes with the operator's ambient credentials. Lifecycle evidence
records processes and launches; it is observability, not containment.

**Gap.** #3 proposes only a configurable worker argv prefix. That is a useful seam, not a sandbox,
image, egress policy, credential policy, or proof that bypass is impossible. Reviewer, manager,
gate, release, and installation trust boundaries also need an explicit disposition; they must not
be wrapped accidentally under the worker policy.

**Owner/reuse.** Reuse #3 for the prefix seam. Any actual sandbox profile/qualification needs a
new issue only after the human chooses runtime, image provenance, mounts, egress, credentials,
and update ownership. Do not expand #3's current body silently.

**Paths.** #3 owns `factory/config.py`, `factory/onboard.py`, `factory/dispatch.py:run_worker`,
`factory/templates/factory.toml`, `README.md`, and focused tests. Host profile/image/service files
must be named only after the policy decision; they are not authorized by this plan.

**Dependencies/lane.** Direct, followed by a Human checkpoint for the host policy and any install.
It is required before widening worker autonomy, concurrency, repository scope, or credential use;
it does not block the already-authorized S0 source change.

**Acceptance scenario.** In a disposable host/repository, the wrapped worker can edit only its
worktree and write expected handoff/gate artifacts, cannot read a seeded forbidden credential,
cannot mutate `main`, cannot escape the allowed network policy, and leaves a truthful lifecycle
and lock trail on success, denial, timeout, and termination. An unwrapped configuration is shown
as explicitly legacy/unisolated, not “safe.” Reviewer and gate behavior remain unchanged unless
a separately approved policy says otherwise.

**Status.** `PLANNED — HELD`; #3 remains open; no runtime, image, credential, network, service, or
installation change authorized.

### 2.4 S0 — exact-head merge-safety tranche

**Outcome.** Only evidence earned successfully for the exact immutable PR head can authorize that
head's integration.

**Required contract.** The implementation may choose its internal record shape; consumers rely on
these invariants, not field names:

1. a failed reviewer process can never yield approval, even if its output prints `APPROVE`;
2. deterministic gate success and independent review success are bound to the exact head assessed;
3. every successful refresh/rebase re-runs the gate and obtains a fresh independent review for the
   refreshed head before approval can be restored;
4. stale, missing, ambiguous, partial, or legacy unbound evidence is non-authorizing;
5. the merge request itself uses the provider's expected-head precondition, so a last-moment head
   change cannot be merged;
6. human requested-changes veto, CI fail-closed behavior, current-main containment, ticket/merge
   locks, and one-merge-per-pass remain intact.

**Existing implementation.** `factory/dispatch.py` has worker/gate/review bounces, approval labels,
refresh/re-gate, fail-closed CI, current-main comparison, human veto, locks, and merge recording.
The prior delivery assessment found approval was not durably head-bound, successful refresh did
not require a fresh review, merge did not pin the expected head, and reviewer output parsing could
outlive reviewer process failure.

**Owner/reuse.** The authorized safety implementation in this worktree. No issue/hold changes are
part of S0.

**Paths.** `factory/dispatch.py`; only the affected focused tests under `tests/`; `README.md` and
`CHANGELOG.md` for the accepted contract. No new state store; reuse `.factory/events.jsonl` through
the existing journal writer.

**Dependencies/lane.** Direct. No dependency on B1–B5, #3, #5–#9, #15, #34, or #52–#59.

**Acceptance scenario.** Controlled real git/GitHub-command stand-ins exercise: reviewer exits
nonzero after printing APPROVE; approval from an older head; successful refresh; head changes
between final check and merge; missing/legacy evidence; CI pending/fail/cancel/missing; requested
changes; behind-main; and competing merge passes. Only one exact, current, fully evidenced head
merges. The complete repository gate then passes once, independent reviews approve that same head,
and CI is green before the authorized PR can merge.

**Status.** `AUTHORIZED NOW — IMPLEMENTATION/ACCEPTANCE IN PROGRESS`. Merge to `main` will not mean
release, installation, rollout, public publication, or delivered outcome.

## 3. Twelve closed-loop units

### L01 — intake reconciliation

**Outcome.** Every supported new issue enters one visible intake state exactly once or receives an
explicit unsupported/refused result; no issue is silently invisible or repeatedly spammed.

**Existing vs gap.** The Agent task template adds `needs-triage`, and `factory triage` processes
that label. Blank/CLI/API-created unlabeled issues are never swept. A `wontfix-proposal` comment
leaves `needs-triage`, so the timer can repeat the proposal every pass. `factory init` does not
provision a `wontfix-proposal` label.

**Owner/reuse.** No live issue owns these two gaps. Create one intake-reconciliation ticket only
after the human chooses the supported creation routes and wontfix stopping state.

**Paths.** `.github/ISSUE_TEMPLATE/agent_task.md`; optional
`.github/ISSUE_TEMPLATE/config.yml` only if blank issues are deliberately disabled;
`factory/triage.py:list_needs_triage/apply_decision`; `factory/onboard.py`; `factory/config.py`
label vocabulary; dashboard intake explanations; focused tests and README.

**Dependencies/lane.** Direct because this changes admission and disposition authority. Depends
on A1. It can be designed in parallel with I1 but shares `config.py`, `onboard.py`, README, and
tests, so one integration owner serializes those files.

**Acceptance scenario.** Through controlled template, blank UI, `gh`, and API creation paths,
observe each supported issue by the next completed intake pass. Repeated passes create no duplicate
triage or wontfix proposal for unchanged evidence. Unsupported routes are disabled or visibly
reported according to the chosen policy. No path closes a human issue automatically.

**Status.** `PLANNED — HELD`; policy decision required; no issue publication authorized.

### L02 — viability, prioritization, and disposition

**Outcome.** A human can ask “should we do this, in what order, and who decides the disposition?”
and receive bounded, evidence-cited, uncertainty-preserving recommendations; only an authorized
human applies closure or deferral.

**Existing vs gap.** `factory manage` already processes opt-in `needs-review` PRs before opt-in
`needs-viability` issues and emits cited `BUILD | DONT_BUILD | DEFER` recommendations. Issue BUILD
re-enters `needs-triage`; other results remain proposals. The manager-unconfigured case is silent,
and prior-art evidence samples only recent items, so an older duplicate can be missed. There is no
cross-request priority contract or durable reasoned disposition queue.

**Owner/reuse.** Preserve the existing viability producer. No live issue owns the silent-no-op,
older-prior-art, or priority contract. A future ticket must not duplicate #5–#9's external PR lane
or #52's initiative roadmap.

**Paths.** `factory/manage.py:viability_pass`; `factory/evidence.py:viability_sources`;
`factory/dashboard.py`/`dashboard.html`; `factory/stats.py`; `.factory/events.jsonl`; focused tests
and README. Any disposition stays through existing confirmed `/api/act`/GitHub human surfaces.

**Dependencies/lane.** Factory for read-only evidence, ranking, and consumer work after A1; Direct
plus Human checkpoint for any new state-changing disposition rule. L04's initiative evidence may
enrich ranking later but ordinary issues must continue without initiatives.

**Acceptance scenario.** Opt in an issue with an older duplicate, a high-value unique issue, an
unavailable evidence source, and an unconfigured manager. Results cite what was actually searched,
show coverage/uncertainty, separate value from implementation confidence, and produce a stable
priority explanation. BUILD queues only through triage; DONT_BUILD/DEFER neither close nor hide the
issue. Re-observation does not duplicate a consumed request.

**Status.** `PLANNED — HELD`; viability baseline is implemented, expansion is not authorized.

### L03 — clarification, specification shaping, and re-entry

**Outcome.** A specific question leads to a versioned human answer and one deliberate re-entry into
triage or human handling, with the executable contract visibly shaped before admission.

**Existing vs gap.** Triage can ask a specific `needs-info` question. The dashboard offers explicit
human actions to answer and re-triage/queue/send to human. No producer notices a reporter reply and
re-arms triage automatically; comments alone are not commands. The current issue template gives a
useful Scope/Touches/Exit gate/Out of scope shape but does not version a clarification decision.

**Owner/reuse.** No live issue owns reporter-reply reconciliation. #56 concerns terminal routed
escalation handoffs, not pre-dispatch `needs-info`; do not conflate them.

**Paths.** `factory/triage.py`; `factory/dashboard.py:act`; `factory/dashboard.html` needs-info
flows; `.github/ISSUE_TEMPLATE/agent_task.md`; `.factory/events.jsonl`; focused tests and README.

**Dependencies/lane.** Direct for actor recognition and re-entry mutation, after A1 and L01. A
read-only clarification summary may use the Factory lane. Human comments remain context until an
explicitly accepted action binds them.

**Acceptance scenario.** A reporter answers after the recorded question; bot comments, edits to old
comments, answers by an unauthorized actor, duplicated webhooks/polls, and a reply arriving during
human takeover are also supplied. Exactly one explicitly authorized re-entry occurs, preserves the
question/answer/source IDs, and produces a complete revised acceptance contract or returns to
`needs-info`. No answer directly authorizes worker execution, merge, or release.

**Status.** `PLANNED — HELD`; actor/re-entry policy decision required.

### L04 — planning, dependencies, and initiative outcomes

**Outcome.** Humans share one GitHub-native initiative plan, admit immutable executable slices,
see real dependencies and drift, and declare delivery only from outcome evidence.

**Existing vs gap.** #52 defines the product contract and remains open. #53 is live-closed, but its
source is absent from this baseline and must be integrated/verified before use. #54 is open
`ready-for-human`; its attempted read-only router has not been accepted into this baseline.
#55–#59 remain `factory-held`. No initiative stage may be inferred from child issue closure.

**Owner/reuse.** Reuse the entire #52 family; do not open a rival initiative/roadmap store:

- #53 — canonical initiative template and read-only plan reader; verify accepted source first;
- #54 — deterministic read-only decision-owner routing; resume only through its human handoff;
- #55 — enforced initiative exclusion from triage, dispatch, manager, and merge;
- #56 — durable routed terminal human handoff;
- #57 — immutable execution-slice binding and drift;
- #58 — shared roadmap/owner-attention consumers;
- #59 — real two-human acceptance checkpoint.

**Paths.** The accepted #53 plan reader/CLI/template; `factory/config.py`; `factory/evidence.py`;
`factory/brief.py`; `factory/triage.py`; `factory/dispatch.py`; `factory/manage.py`;
`factory/lifecycle.py`; `factory/dashboard.py`/`dashboard.html`; focused tests and README.

**Dependencies/lane.** Preserve the live graph exactly: `#53 → #54`; `#53 + #15 → #55`;
`#54 + #55 → #56`; `#53 + #55 → #57`; `#56 + #57 → #58 → #59`. #55/#56/#57 are Direct;
#58 is a bounded Factory consumer only after producers are accepted; #59 is a Human checkpoint.
Serialize #56 and #57 writers around shared dispatch/manage seams even though they are not given a
fake dependency edge.

**Acceptance scenario.** On one authorized pilot repository with one runner and two distinct,
consenting human GitHub identities, shape an initiative, bind one slice, edit the plan during work,
route a terminal question, claim/answer it, explicitly re-enter, inspect the actual result, and
revise outcome evidence. A restart sees the same accepted state; unknown attribution stays unknown;
child closure does not mark delivered. Team routing is accepted only in a verified org-owned pilot.

**Status.** `PLANNED — HELD`. All #55–#59 holds remain. #54 remains with the human. No accounts,
permissions, team mentions, issue edits, or pilot repository are authorized.

### L05 — implementation validation and acceptance evidence

**Outcome.** Each executable slice carries an immutable, human-approved contract through worker,
gate, review, and result; acceptance reports the last proven boundary instead of self-declared done.

**Existing vs gap.** `factory/brief.py`, `dispatch.worker_round`, `factory/gate.py`, the independent
review stage, handoffs, and the lifecycle journal already separate attempts and outcomes. S0 closes
head binding at review/merge. Initiative-linked contract binding and drift remain #57. Existing
briefs are deterministic context, not a signed specification or acceptance result.

**Owner/reuse.** Reuse #57 for initiative-linked slice binding. Ordinary issue contracts remain the
Agent task issue body and accepted comments; do not require an initiative. Reuse B4/B5 later for
history and causal receipt display rather than adding a second evidence ledger.

**Paths.** `.github/ISSUE_TEMPLATE/agent_task.md`; `factory/brief.py`;
`factory/dispatch.py:build_prompt/worker_round/process_ticket`; `factory/gate.py`;
`factory/lifecycle.py`; `.factory/events.jsonl`; accepted #57 plan reader/binding; focused tests and
README.

**Dependencies/lane.** Direct for contract/admission and verification evidence. Depends on S0 and
A1; initiative binding additionally depends on #53/#55 as #57 specifies. I1 is required before
claiming broad worker containment, not before source-level validation work.

**Acceptance scenario.** Admit a bound slice, mutate unrelated discussion, mutate relevant plan
content, advance the PR head, fail a gate, fail reviewer execution, pass review, and interrupt an
attempt. The original execution contract is retained; relevant drift blocks new admission but does
not silently rewrite running work; gate/review evidence names the assessed head; each failure and
unknown is distinct; only observed acceptance evidence can advance the slice.

**Status.** `PLANNED — HELD` beyond S0; #57 hold remains.

### L06 — immediate and late PR review remediation

**Outcome.** Immediate reviewer findings and later provider CI/review/thread feedback each reach one
bounded, owned remediation attempt or a diagnosed terminal handoff, without duplicate delivery or
self-approval.

**Existing vs gap.** Immediate review → worker bounce → re-gate → re-review is implemented and
bounded; S0 makes success exact-head and rejects failed reviewer output. Later feedback is not
closed: current code sees aggregate CI/human veto but does not collect source-versioned reviews,
inline threads, check runs/reruns, relevance, or partial coverage, and it has no durable delivery
protocol. Live #15 remains open `ready-for-human`; its earlier PR-frontier attempt is not accepted.

**Owner/reuse.** Preserve the historical split:

- **B2** (unpublished, no GitHub number): one bounded read-only feedback collector. It preserves
  source IDs and semantic revisions, provider links/times, source and observed heads, current/
  historical/unknown relevance, review/thread/check disposition, ownership provenance, independent
  source coverage/errors, and deterministic observation identity. It never infers delivered,
  fixed, ready, or approved.
- **B3**: amend and resume existing **#15**, never create a competing PR-frontier ticket. It consumes
  accepted B2 evidence and uses the existing journal to separate observation, manager decision,
  selected delivery, worker launch/execution, gate/review, approval, merge/close, partial failure,
  and unknown. A durable launch acknowledgement means observed launch, not “agent understood.”

The live #15 body does not contain this B2 prerequisite. At a separately authorized, unclaimed
handoff, publish B2 through normal triage, obtain its real issue number and accepted revision/example,
then add that real same-repository blocker and the B3 contract to #15 before re-entry. Never rewrite
an actively claimed issue or invent a blocker number. #13/#26 remain concrete predecessors from
the historical contract; B5 must not block #15.

**Paths.** B2: `factory/dashboard.py`, proposed narrow `factory/feedback.py` only if a shared module
is needed, `factory/briefing.py`, `factory/dashboard.html`, focused tests, README. B3/#15:
`factory/manage.py`, `factory/dispatch.py` prompt/worker/review/refresh/approval/merge paths,
`factory/lifecycle.py`, `.factory/events.jsonl`, and accepted B2 projection. No event bus/database.

**Dependencies/lane.** B2 is Factory/read-only after S0. B3/#15 is Direct control-plane work after
B2 acceptance and the live #15 ownership handoff. #5→#6→#7→#8→#9 is a separate opt-in external/
human PR review-only lane: useful later, but not a blocker for Factory-owned PR remediation and
never gains fix, push, merge, or close authority.

**Acceptance scenario.** Provide simultaneous red CI, a current-head changes-requested review, and
an unresolved inline thread; then reorder unchanged results, edit an existing comment, rerun a
check, advance the head at each boundary, make one source partial, interrupt before and after
worker launch, and race two passes. Each source revision is delivered at most once to one admitted
owned worktree; new feedback waits; unknown launch parks; a fresh successful head must re-gate and
receive independent review before approval. CLOSE may close an owned Factory PR with diagnosis,
but a human-authored issue stays open with proposal-only disposition and truthful partial results.

**Status.** `PLANNED — HELD`; B2 unpublished; #15 unchanged and with the human; #5–#9 unchanged.

### L07 — integration and head-bound safety

**Outcome.** Integration lands only the exact reviewed/gated current head, while the operator can
see every blocker and distinguish eligibility, merge, and later delivery.

**Existing vs gap.** The merge stage already checks approval label, CI, current-main, human veto,
locks, and one PR per pass. S0 closes the authorization gaps. Historical **B1** remains an
unpublished consumer proposal for truthful board/Inbox/briefing readiness: show all simultaneous
blockers, evidence freshness/completeness, and next owner without changing merge policy.

**Owner/reuse.** S0 owns merge correctness. Reuse B1 rather than redefining #8/#9 external-PR
“ready.” B1 has no GitHub number; #27/F02 wait/resource evidence is present in this baseline but must
be reverified at publication.

**Paths.** S0 paths above. B1: `factory/dashboard.py:pr_record` and snapshot assembly;
`factory/dashboard.html` board/Inbox/ticket explanations; `factory/briefing.py`; only a shared
predicate seam in `factory/dispatch.py` if needed; focused tests and README.

**Dependencies/lane.** S0 Direct and authorized now. B1 is Factory/read-only after S0 and verified
#27/F02 acceptance, serialized with B2/B4 dashboard writers. It is not a prerequisite for merge
correctness or #15.

**Acceptance scenario.** The S0 matrix proves enforcement. Separately, a disposable dashboard shows
zero checks, missing/unparseable checks, pending, fail/cancel, skipped-only, pass-plus-skipped,
missing/stale approval, requested changes, behind/unknown-current-main, and fully eligible evidence.
All simultaneous blockers and their owners appear consistently; “eligible at observation” is never
displayed as merged.

**Status.** S0 `AUTHORIZED NOW`; B1 `PLANNED — HELD / UNPUBLISHED`.

### L08 — operational recovery, partial mutations, and termination

**Outcome.** After crash, timeout, cancellation, ambiguous provider response, or partial mutation,
Factory either resumes from a proven boundary or parks for a human; it never repeats an uncertain
external effect or reports false completion.

**Existing vs gap.** `factory/lifecycle.py` and runtime projections preserve execution identities,
parent/child/process/lock evidence, interruptions, and unknowns in `.factory/events.jsonl`.
Manager decisions are recorded before mutation; failed/partial manager mutations are not blindly
replayed. Full causal receipts and authoritative history consumers remain the historical B4/B5
gaps, and future B3/release operations add new recovery boundaries.

**Owner/reuse.** Historical **B4** (unpublished) renders accepted lifecycle/F03 history in the
existing Timeline/Attempts without artifact-time inference. Historical **B5** (unpublished) links
confirmed human decision receipts to admission, execution, and later PR outcome using explicit
identities. B5 consumes #15/B3 identities but never blocks #15.

**Paths.** Producer controls: `factory/lifecycle.py`, `factory/runtime_events.py`,
`factory/runtime_local.py`, `factory/dispatch.py`, `factory/manage.py`,
`.factory/events.jsonl`. Consumers: `factory/dashboard.py`, `factory/dashboard.html`,
`factory/briefing.py`, README. B4 consumes accepted #28/F03; B5 reuses existing `/api/act` receipts.

**Dependencies/lane.** Direct for append/decision/action/reconciliation semantics; Factory for B4/B5
read-only UI consumers after producer acceptance. B4 follows verified #28/F03 and is serialized
with B1. B5 follows #15, #28, and accepted B1/B4 integration; these are publication checkpoints
until real issue IDs exist, never fake runnable blocker lines.

**Acceptance scenario.** At every external-effect boundary, terminate before intent write, after
intent/before call, during call, after remote success/before local result, after partial multi-step
success, and during final journal write. Restart with complete, truncated, torn, duplicate, and
unreadable journal evidence. Proven nonlaunch may be retried only within existing bounds; proven
launch/result is linked once; ambiguity parks as unknown. UI history retains concurrent executions,
partial outcomes, and missing boundaries without invented duration or success.

**Status.** `PLANNED — HELD`; B4/B5 remain unpublished; existing recovery behavior is preserved.

### L09 — release, rollout, and recovery

**Outcome.** A reviewed source state becomes an explicitly approved release, then a separately
approved installation/rollout, with exact provenance and a rehearsed recovery path.

**Existing vs gap.** There is no repository release automation. Version, tag, `stable`, GitHub
Release, and installation are human-managed. A prior assessment observed `main` and `stable`
diverged; re-measure at release time rather than treating that snapshot as current. `main` merge is
not release; a tag is not installation; installation is not delivered outcome.

**Owner/reuse.** No live issue owns the release loop. Create a release-policy ticket only after the
human resolves the decisions in §7. Initial implementation is intentionally a **human-approved
release PR**, not autonomous release authority.

**Paths/surfaces.** `factory/__init__.py` version; `CHANGELOG.md`; generated public content in
`docs/index.html` after L10 exists; `README.md` install/version statements; current
`.github/workflows/ci.yml` only as evidence unless a separately authorized verification-policy
change is approved; Git refs `main`, `stable`, `v*`; GitHub Release; District/host installed-engine
metadata and service units only in a separately authorized rollout.

**Dependencies/lane.** Direct plus Human checkpoints. Release-policy design depends on S0, A1, I1
policy, and L08 recovery semantics. A release PR uses the same exact-head independent reviews,
repository gate, and green CI. Any workflow/signing/permission change is its own authorization.

**Acceptance scenario.** First run the full prepare → review → main merge → tag/release/stable →
install → verify → rollback sequence against a disposable repository and installation target.
Then, for a real release, the human approves the exact release PR and later approves the exact tag,
stable update, publication, rollout target/ring, and rollback ref. Verification records source SHA,
version, tag object, release asset/hash if any, stable relationship, installed revision/schema,
service health, and previous known-good ref. A failure at any step stops subsequent steps and keeps
truthful partial state; rollback restores and verifies the recorded previous ref.

**Status.** `PLANNED — HELD`; no release PR, tag, stable move, GitHub Release, install, service reload,
or rollout authorized.

### L10 — public changelog/roadmap generation and publication verification

**Outcome.** Public release notes and roadmap are deterministically generated from approved sources,
reviewed as a normal content PR, and verified on the actually served Pages site after merge.

**Existing vs gap.** `CHANGELOG.md` and `docs/index.html` are hand-maintained. Repository Pages
already serves `main:/docs`; content generation and post-publication verification are missing. #58
is the internal initiative/owner dashboard, not this public marketing surface.

**Owner/reuse.** New content-generation ticket after policy approval; no GitHub number exists.
Reuse `CHANGELOG.md`, live GitHub Releases/issues/initiatives, and accepted L04 outcome facts. Do not
scrape local worker logs or publish private `.factory` paths. L10 does not replace #58.

**Paths.** `CHANGELOG.md`; `docs/index.html`; a single small stdlib generator/checker path selected
in the ticket after inspecting the then-current site (no framework or database); focused checks;
README publication instructions if changed. Pages repository settings remain external evidence,
not source-controlled authorization.

**Dependencies/lane.** Factory for generator and content PR after L04 and release metadata contracts
are accepted. Independent review checks wording/provenance. Main merge triggers the existing Pages
publication, but the canonical Pages URL and expected content revision must be human-confirmed at
execution. Changing Pages settings is a separate Human checkpoint.

**Acceptance scenario.** From frozen approved changelog, release, and roadmap inputs, two runs
produce byte-identical generated sections. Closed/held/unknown initiatives are represented without
invented delivery; private paths and untrusted HTML are escaped. A stale generated file fails the
checker. After the content PR merges, fetch the configured canonical Pages URL until the bounded
publication deadline and verify the expected source/release revision and links; timeout or mismatch
records publication unknown/failed, never success.

**Status.** `PLANNED — HELD`; no content PR, Pages setting, or publication action authorized.

### L11 — shipped-outcome feedback

**Outcome.** Product owners can compare promised success evidence with observed post-release use and
explicitly confirm, reject, or defer an outcome; implementation completion cannot mark it delivered.

**Existing vs gap.** Factory records engineering/process outcomes and human-touch metrics, but has
no product-outcome loop. #52 declares initiative Success evidence and owner-confirmed delivery;
#58/#59 plan consumers and a real pilot. No telemetry collection, privacy policy, outcome window,
or automatic initiative mutation is approved.

**Owner/reuse.** Reuse #52/#58/#59 and B5 causal receipts. Create only a narrow outcome-evidence
producer ticket if the pilot proves existing GitHub evidence insufficient. The initiative owner,
not the runner/model, owns the delivery declaration.

**Paths.** Accepted plan reader/initiative projection; `factory/evidence.py`; `factory/stats.py`;
`factory/dashboard.py`/`dashboard.html`; `factory/briefing.py`; `.factory/events.jsonl`; public
release/install/publication evidence from L09/L10. Product telemetry paths are deliberately unnamed
until privacy/data-retention decisions are approved.

**Dependencies/lane.** Factory for bounded read-only evidence and display after L04, L08, L09, and
L10; Human checkpoint for outcome declaration or any telemetry/data-policy change. #59 remains the
programme acceptance owner.

**Acceptance scenario.** One released and installed change has owner-approved success evidence; one
has contrary evidence; one has missing/partial evidence. The same surface shows delivered, not met,
and unknown/deferred respectively, with source times and coverage. PR/issue closure alone changes
none of them. A restart preserves the decision and its evidence; a later observation appends history
rather than rewriting the original claim.

**Status.** `PLANNED — HELD`; no telemetry, customer contact, initiative mutation, or delivered
claim authorized.

### L12 — learning, evaluation, and qualification

**Outcome.** Lessons, reviewer changes, routing changes, and wider autonomy are promoted only when
independent, reproducible evidence shows benefit without unacceptable misses or unnecessary blocks.

**Existing vs gap.** `factory learn` distils bounded recent evidence and can propose restricted
harness-context PRs; `factory stats` reports pass/human-touch metrics. #34's calibration experiment
and artifacts are present, while #34 remains open and no production reviewer policy changed. The
experiment demonstrated missed real defects and showed that a self-reported “criteria coverage”
line is not a valid independent signal. Its four-case/two-ticket sample is not a rate.

**Owner/reuse.** Reuse #34 and `experiments/reviewer-calibration/`; do not create a second benchmark
harness or publish 0%/100% rates from the small sample. #5–#9 may later supply external-PR evidence
but are not blockers for expanding the offline corpus.

**Paths.** `experiments/reviewer-calibration/`; `factory/learn.py`; `factory/stats.py`;
`.factory-lessons.md`; `.factory/manager/notes.md`; `.factory/events.jsonl`; reviewer prompt in
`factory/dispatch.py` only after a separately approved verification-policy change; relevant docs.

**Dependencies/lane.** Factory for corpus collection, bounded reports, and ordinary context PRs.
Direct plus Human checkpoint for reviewer contract/model, gate, routing authority, budget/concurrency,
or production rollout changes. Uses L05–L11 evidence when available; no learning job may grant
itself authority.

**Acceptance scenario.** Freeze an independently adjudicated corpus with defect-bearing and clean
heads, run baseline and candidate at repeated samples, report missed required defects, unnecessary
REVISE demands, invalid outputs, cost, and latency with confidence/coverage limits, and reproduce
from pinned prompts/source/model settings. Candidate changes remain a reviewed proposal until human
thresholds are met. A failed or ambiguous evaluation leaves production unchanged.

**Status.** `PLANNED — HELD`; #34 stays open; no reviewer/gate/model/provider change authorized.

## 4. Existing-ticket and historical-contract crosswalk

| Work | Role in this programme | Current disposition |
|---|---|---|
| #3 | Worker wrapper seam for I1; not complete sandboxing | Open; held by this plan pending isolation policy; no label change |
| #5 → #6 → #7 → #8 → #9 | Opt-in external/human PR review-only lane: discover, publish review, re-review changed head, show CI readiness, render queue | Open; distinct and nonblocking for S0/B2/B3; never fix/push/merge/close external PRs |
| #15 | Existing owner for B3 Factory-owned PR frontier and late remediation | Open `ready-for-human`; do not rewrite/requeue while owned; B2 amendment/handoff requires separate authorization |
| #34 | Reviewer calibration experiment and remaining qualification work | Experiment landed; issue remains open; no production coverage claim/policy change |
| #52 | Collaboration programme and authoritative product contract | Open documentation parent; child closure does not prove outcome |
| #53 | Plan template/read-only reader prerequisite | Live issue closed; accepted source absent from baseline, so verify/integrate revision before consumption |
| #54 | Read-only decision-owner routing | Open `ready-for-human`; resume only through explicit handoff |
| #55 | Enforce initiative exclusion across execution paths | `factory-held`; depends on #53 and #15; Direct lane |
| #56 | Durable routed terminal human handoffs | `factory-held`; depends on #54 and #55; Direct lane |
| #57 | Bind execution slices to initiative revisions | `factory-held`; depends on #53 and #55; Direct lane |
| #58 | Shared internal roadmap/owner-attention consumer | `factory-held`; depends on #56 and #57; Factory lane only after producers accepted |
| #59 | Two-human initiative-to-delivery acceptance | `factory-held`; depends on #58; Human checkpoint |
| B1 | Truthful Factory PR readiness/admission explanation | Unpublished/no number; L07 consumer; after verified #27/F02 |
| B2 | Source-versioned late feedback producer | Unpublished/no number; L06 Factory lane; must precede amended #15/B3 |
| B3 | Bounded late-feedback delivery | Amendment to #15, not a new issue; Direct lane after B2 handoff |
| B4 | Authoritative lifecycle history consumer | Unpublished/no number; L08; after verified #28/F03 |
| B5 | Human-decision → claim/execution/outcome causal receipts | Unpublished/no number; after #15/#28/B1/B4; never blocks #15 |

## 5. Sequencing, checkpoints, and file ownership

### Wave 0 — authorized now

1. Implement S0 in the designated worktree.
2. Run the exact repository gate once after all S0 code/docs/tests settle.
3. Obtain independent non-agent Spec and Standards reviews on the exact proposed head.
4. Require green CI and re-check the exact head immediately before the authorized merge.
5. Stop at `main`. Release, install, deployment, Pages changes, hold release, and issue mutation
   remain unauthorized.

### Future Wave 1 — prerequisites (separate authorization)

- Reconcile the accepted #53 source into the then-current baseline without recreating it.
- Decide A1 admission/authority policy and I1 isolation policy.
- Implement #3's seam and qualify the selected sandbox only if separately authorized.
- Publish/execute L01 and L03 authority work only after their human policy decisions.

Parallelism: read-only #53 source reconciliation and isolation design may proceed independently.
Code touching `config.py`, `onboard.py`, README, or shared tests is serialized under one integration
owner. No simultaneous direct writers to `dispatch.py`, `manage.py`, or `triage.py`.

### Future Wave 2 — bounded evidence and advice (separate authorization)

- B2 late-feedback producer, L02 viability/prioritization evidence, expanded #34 corpus, and other
disjoint read-only producers may run in parallel after their inputs and file owners are fixed.
- #5–#9 may proceed on their own dependency chain but do not delay Factory-owned PR safety.
- B1, B2, and B4 all touch dashboard/briefing consumers: serialize them in the practical order
  **B2 → B1 → B4**, unless one integration owner proves disjoint hunks and owns the combined PR.

### Future Wave 3 — bounded action and collaboration (separate authorization)

- Accepted B2 + live #15 handoff → amended B3/#15.
- Verified #53 + accepted #15 → #55.
- #54 + #55 → #56; #53 + #55 → #57.
- Serialize #56/#57 shared dispatch/manage integration; then #58; then human pilot #59.
- L05 validation binding and L08 recovery producers land before any widened autonomy claim.

Every control-plane PR in this wave uses the Direct lane and an independent review/gate/CI/head
checkpoint. Existing issue dependencies govern scheduling; this plan does not mutate them.

### Future Wave 4 — release and public delivery (separate authorization)

- Authorize the L09 release-policy ticket and L10 generator ticket separately.
- Land ordinary content generation through the Factory lane; land release authority through Direct.
- Prepare a human-approved release PR. Its merge is only release-candidate source.
- Obtain separate point-of-action approval for tag/stable/GitHub Release, rollout/install, and any
  rollback. Verify Pages publication separately from merge.

### Future Wave 5 — outcomes and qualification (separate authorization)

- Run #59/L11 with real consenting humans only after its producers and environment are authorized.
- Feed explicit outcomes into L12 evaluation.
- Widen routing, budgets, concurrency, models, or automation only through a new approved policy PR
  whose evidence meets the chosen qualification thresholds.

### Shared-file ownership rule

Before each future wave, one integration owner publishes an ownership ledger. At minimum:

| Files/seams | Exclusive wave owner |
|---|---|
| `factory/dispatch.py`, `factory/manage.py`, `factory/triage.py` | one control-plane integrator; other work queues behind it |
| `factory/lifecycle.py`, `factory/runtime_events.py`, `.factory/events.jsonl` schema | one evidence producer owner; consumers wait for accepted handoff |
| `factory/dashboard.py`, `factory/dashboard.html`, `factory/briefing.py` | one consumer integrator across B1/B2/B4/B5/#58 |
| `factory/config.py`, `factory/onboard.py`, `factory/templates/*` | one configuration/onboarding owner across I1/L01/#54 |
| `README.md`, `CHANGELOG.md`, `docs/index.html` | one final documentation/content integrator per PR |

Parallel work owns disjoint files and consumes a written accepted interface. Shared files are not
edited concurrently by independent workers. A dependency is recorded only when behavior depends on
another accepted contract; file scheduling alone is not turned into a permanent fake blocker.

## 6. Operational acceptance metrics

These are programme exit floors. They do not authorize production experiments or data collection.
Where a policy threshold is still open, §7 names it.

| Loop | Metric and floor |
|---|---|
| Intake | 100% of supported creation routes reach a visible intake/refusal state by the next completed pass; zero duplicate unchanged triage/wontfix comments in replay/restart scenarios |
| Viability | 100% of recommendations cite supplied evidence and coverage; zero DONT_BUILD/DEFER auto-closures; unavailable manager/evidence is visible, never silent success |
| Clarification | Exactly one authorized re-entry per accepted answer revision; zero comment-as-command admissions; re-entry latency no more than one completed scheduled pass after acceptance |
| Planning | 100% of initiative-linked admitted slices carry a valid accepted baseline; zero child-closure-derived delivery declarations; all unknown attribution remains explicit |
| Validation | 100% of gate/review approvals used downstream identify the exact assessed head; zero failed-process approvals; every accepted slice has observable exit evidence |
| Review remediation | Zero duplicate delivery for an unchanged feedback revision across replay/race/restart; 100% of admitted fixes end in fresh gate/review evidence or a diagnosed handoff |
| Integration | Zero merges from stale/missing/legacy evidence, failed reviewer exit, active veto, red/pending/missing CI, behind-main head, lock violation, or expected-head mismatch |
| Recovery | Zero blind replays after ambiguous external effects; every injected crash ends at a proven boundary or explicit unknown/partial state; no torn row hides earlier valid evidence |
| Release/rollout | 100% of releases record source SHA, version, tag/stable relationship, review/gate/CI result, installed revision, previous ref, and rollback verification; zero automatic release authority |
| Public site | Generated sections are deterministic and checker-clean; post-merge Pages verification observes the expected revision/links within the approved bounded window or reports failure/unknown |
| Outcome | 100% of initiatives marked delivered have owner-confirmed success evidence tied to a released/installed observation; merged-only work is never counted delivered |
| Learning | Every promoted policy/model/prompt change has a pinned reproducible comparison and independent adjudication; no production change from a failed/ambiguous run |

Operational dashboards must also retain: escalation rate, human-resolved percentage, time in
`ready-for-human`, re-queues, first-gate pass by worker, reviewer required-defect misses,
unnecessary REVISE demands, duplicate-suppression counts, unknown/partial recovery counts, release
lead time, rollout/rollback result, Pages publication lag, and outcome-evidence coverage. A metric
without complete source coverage is reported with its denominator and gaps, not as a fleet rate.

## 7. Unresolved human decisions

No implementation may guess these:

1. **Supported intake routes:** disable blank issues, automatically reconcile all unlabeled issues,
   or support an explicit subset; and what durable state stops a wontfix proposal without closing a
   human issue.
2. **Clarification authority:** which actors may accept/revise an answer, whether reporter replies
   merely notify or can arm a confirmed re-entry, and the replay/cooldown rule.
3. **Prioritization policy:** ranking inputs and weights, exhaustive-vs-bounded duplicate search,
   portfolio capacity, deferral expiry, and who may apply final disposition.
4. **#53 integration:** the accepted source revision and clean way to bring it onto the then-current
   branch before #54/#55/#57 consume it.
5. **#15 ownership:** whether to salvage the existing PR/handoff or replace its implementation on a
   Direct branch after B2; when its body may be amended; who accepts B2's producer handoff.
6. **Late feedback policy:** source/page/body/time bounds, the stale-PR interval, which unresolved
   historical findings may be carried forward, and manager FIX/CLOSE authority within B3.
7. **Collaboration holds:** whether to implement #55/#56/#57 on human-reviewed non-agent branches or
   first install independently enforced branch protection; when, if ever, each #55–#59 hold may be
   released.
8. **Isolation:** runtime/container, trusted image and update owner, filesystem mounts, UID, network/
   egress, GitHub credential scope, secrets, CPU/memory/time limits, artifact extraction, and whether
   reviewer/manager/gate need separate profiles.
9. **Release policy:** versioning, tag naming/signing, how to reconcile `stable` and `main`, artifact
   target and hashes, GitHub Release ownership, release-PR approvers, and whether CI workflow changes
   are warranted.
10. **Rollout policy:** environments/rings, installed-revision proof, health window, rollback trigger,
    previous-ref retention, District/service authority, and who approves each production target.
11. **Public content:** canonical Pages URL, source-of-truth fields for changelog/roadmap, generation
    cadence, embargo/private-data rules, link policy, and publication verification timeout/owner.
12. **Outcome evidence:** initiative owner, success window, acceptable evidence, privacy/retention,
    whether any telemetry is allowed, and who may declare/revoke delivered status.
13. **Qualification thresholds:** corpus size/diversity, maximum missed required defects, maximum
    unnecessary REVISE rate, model/cost/latency limits, independent adjudicator, and evidence needed
    before widening autonomy. A reviewer self-reported coverage score is explicitly insufficient.
14. **External PR lane:** whether and when #5–#9 should enter normal intake; it stays review-only and
    nonblocking for the Factory-owned merge-safety/late-remediation programme.

## 8. Programme completion

The whole-autonomy programme is complete only when all authorized units have their measurable
scenarios recorded, all Direct changes have exact-head independent review/gate/CI evidence, every
remaining hold is either explicitly retained or separately released, the release/install/publication
boundaries are proven separately, #59's real two-human pilot is accepted, and outcome/qualification
evidence meets the human-chosen thresholds.

Until then, the truthful status is the narrowest proven boundary. In particular:

```text
merged to main ≠ released ≠ installed/rolled out ≠ publicly verified ≠ outcome delivered
```
