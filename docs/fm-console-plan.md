# Pi-based FM console: architecture and delivery plan

Status: **C0 read-interface review closed and accepted by the operator, 2026-09-05.** The current disposable harness is retained for continued use. A bounded C1 ticket draft and next-agent handoff are prepared locally; no issue is published or dispatched. This acceptance covers the read interaction and direction, not guarded actions, model qualification, a security boundary, production packaging or deployment. C0–C6 remain planning IDs, not GitHub issue numbers.

**C1 source delivery:** implemented and verified locally after F03/PR31 acceptance; see the [handoff delivery addendum](c1-read-interface-handoff.md) for source ownership, bridge resolution and exact checks. The existing harness now consumes the shared Python reader. C1 is not committed, published, merged, installed or deployed; C2–C6 remain unstarted.

## Decision

Build a Linux-only, purpose-built interactive FM console on upstream Pi. Start with a launcher and curated Pi package, not a fork or a replacement scheduler. Proposed user entry point: `factory chat`, with explicit repository selection and later fleet scope through District.

Use a hybrid delivery process: direct, human-led work to prove the interaction and settle authority; factory tickets for bounded implementation; human-controlled acceptance and deployment. Out-of-band means outside autonomous dispatch, not outside version control, independent review or verification.

## Product contract

A fresh session opens with a brief personalized greeting, a grounded count of issues needing attention and a command-discovery hint. Zero, unavailable and partial counts are explicit. No automatic report or briefing. The operator need not explain Factory, find its registry or manually load skills.

The FM is a conversational manager: understand the question, investigate within approved scope, give a sound recommendation in natural language, offer to carry out the recommended change, obtain confirmation, arrange the approved work and report its actual outcome. Context informs judgment; it is not a response template. Simple questions receive short answers, with relevant facts, uncertainty and inspectable citations rather than mandatory headings or repeated policy recitals. Briefings and detailed reports are opt-in.

Routine read-only investigation within the approved repository and disclosure boundary needs no repeated permission. Follow through on agreed investigation instead of restating the evidence or asking the same question again. Ask when scope, disclosure or authority must expand, or a material choice needs the operator. Do not offer an inspection or action absent from the available capabilities.

The operator can investigate a case, compare responses, prepare an exact action, approve it through a trusted interactive control, and inspect its actual receipt. Drafting tickets or discussing a strategy does not publish or dispatch work. Supported actions must be enumerated; unavailable actions remain explicit rather than becoming shell commands. Read-only C0 is a temporary execution boundary, not the intended FM personality or final capability ceiling.

Closing the console does not stop Factory. Reopening refreshes operational evidence. Conversation history is not authoritative state. Interactive and unattended FM operation share decisions and policy, not a mandatory immortal model session.

Completion includes both repository operation and explicit fleet navigation, durable continuity, controlled actions, packaged task skills, and verified installation. Read-only delivery is an intermediate checkpoint, not the whole product.

## Grounding and existing ownership

These observations describe the inspected checkout, not installed fleet state or current GitHub issue status:

- `factory/cli.py:8-18` has no chat or manage entry point. `pyproject.toml:11-12` keeps the Python runtime dependency-free.
- `factory/dashboard.py:snapshot` and `cached_snapshot` collect operational evidence. `factory/briefing.py:sources_for` selects bounded sources and earlier human decisions, with gaps/truncation reported.
- `factory/briefing.py:run_model` is a fixed no-tools OMP transport. Reuse evidence selection, not a second model call nested inside every Pi conversation turn. Existing dashboard advice remains read-only.
- `factory/dashboard.py:act` validates a human request and records intent/results, but the inspected server route calls it directly. It does not enforce an approved-preview fingerprint or general freshness/authorization check. Browser-side protections are not a sufficient tool contract.
- `docs/manager-plan.md` owns manager authority, proposed manager stages, scoped notes, lifecycle/evidence work and all existing programme holds. This plan neither supersedes those contracts nor assumes their implementation. Relevant recorded work includes #13 manager decisions, #14 notes, #15 PR management, #26/F01 lifecycle, B2 feedback and B5 receipts. Refresh their actual state before scheduling; those references are not accepted-prerequisite claims.
- Upstream Pi now publishes as [`@earendil-works/pi-coding-agent`](https://github.com/earendil-works/pi/tree/v0.84.4/packages/coding-agent). C0 pins release **0.84.4**, including its existing terminal UI; the original `badlogic/pi-mono` reference is historical. Provider compatibility findings below apply only to the exercised release/model.

## Architecture and invariants

### Runtime and packaging

Keep the console package in this repository, provisionally `console/`, beside the Python Factory implementation. Add only the launcher to Python; Pi/Node remains an explicitly installed optional console dependency. Pin the tested Pi release and lock dependencies. Normal dispatch must not require Node or download packages.

Launch a dedicated resource configuration: explicit FM prompt, extensions, tools and skills, without automatic discovery of unrelated project/global coding resources. Authenticate through supported provider facilities without copying secrets into prompts, journals or diagnostics. Document which Factory evidence goes to the selected provider. Reuse existing auth only where the pinned runtime demonstrably supports it; no promise that OMP and upstream Pi auth stores are interchangeable.

Use Pi's existing terminal UI. Reach for the SDK only if the prototype demonstrates a concrete extension limitation. No custom terminal renderer, new daemon, network listener, database or new scheduler.

### Operational interface

A small Python-owned JSON command interface serves the Pi extension. Exact command names and versioned request/result shapes are settled in C0/C1; the intended operations are:

| Operation | Contract |
| --- | --- |
| Observe scope | Explicit repository identity, observation time, coverage/errors, current cases and decisions; unknown is not healthy/empty. |
| Inspect case | Bounded, cited evidence for an identified case; only recorded/allowed artifacts; no arbitrary path or shell execution. |
| Investigate repository/CI | Bounded reads of repository files at identified revisions, workflow definitions, PR diffs/head SHAs, check contexts, Actions runs/jobs and relevant logs. Resolve through the repository owner with provenance, time and explicit unavailable/truncated results; no arbitrary host paths, shell or network access. |
| Discover capabilities | Report the actual supported read operations, target/revision limits and accepted action menu. Missing capability is distinct from absent evidence; the FM must not promise a read it cannot perform. |
| Prepare decision | Closed supported action, exact target and values, rationale, source/head/state preconditions and immutable proposal identity. No external mutation. |
| Apply confirmed decision | Accept a trusted human confirmation bound to that proposal, revalidate under the owning coordination mechanism, record intent, execute only allowed steps and return a durable receipt. |
| Retrieve/record context | Read shared decisions and scoped notes; explicit human-approved standing instructions use the existing manager-note ownership and durability contract. |

Prefer invoking the existing Python CLI with structured stdin/stdout over introducing HTTP or MCP. Extract shared logic only where both dashboard and console actually consume it. The Python module owns policy; TypeScript adapts it to Pi, rather than reproducing GitHub semantics.

The model can request a proposal but cannot manufacture confirmation: no model-controlled `approved: true` field, shell escape or direct mutation tool. A trusted console handler renders the exact proposal and obtains operator approval. Policy-changing, security, release and host-management actions remain under existing human authority. FM delegates product code changes to workers and never approves its own implementation.

Once confirmed, delegated implementation uses Factory's existing intake, ownership, worker/process and review mechanisms, not a new console scheduler or an unrestricted agent-spawn tool. Bind the work request to the approved objective, repository, target case, scope, acceptance gate and effects. Return the existing case/run identity, distinguish admission/queued/running from completed work, and inspect real results before claiming success. Delegation cannot bypass holds, steal claimed work, widen scope, grant a worker new authority or substitute for independent review.

Repairing a CI workflow is not inherently weakening verification. Diagnose the actual cause and propose a change that preserves the accepted gate; changes to protected verification/authorization machinery retain their existing human-controlled review and merge checkpoint. LLM-provider availability, GitHub API access, workflow execution and installed Factory state are distinct evidence domains.

Preview approval expires when relevant scope, head, ownership, human intervention or proposed values change. The console and unattended manager use the same locks/guards; separate processes must not produce duplicate action. Replay returns the recorded outcome, not another mutation. Partial or ambiguous external results remain partial/unknown and require reconciliation, never blind retry. Intent persistence failure means no action.

Pi extensions execute trusted host code; a tool allowlist is not an OS sandbox. Ship only reviewed pinned extensions, disable default model shell/edit/write tools, and ensure conversational evidence cannot register capabilities or authorize mutations.

### State and scope

Use existing Factory events/decision IDs and the accepted manager-note mechanism. Pi transcripts hold conversation, not a parallel operational ledger. Until the manager-note producer is accepted, read existing decisions and explicitly report missing continuity; do not create a console-only memory system.

Repository identity is explicit in every request, proposal and receipt. Switching scope clears pending proposals and refreshes evidence; a ticket number alone never identifies a fleet case. District remains the fleet registry/host-policy owner. Fleet navigation selects a repository for mutations; no bulk fleet action is included.

Refresh on startup/resume, scope change, operator refresh and before action. Long conversations expose evidence age. No permanent background model call is required for awareness. Do not promise push notifications or continuous live updates in this scope.

Use a light conversational system prompt for role, grounding and tool boundaries. Retrieve relevant decisions, programme context and FM skills on demand rather than injecting large planning excerpts into every turn. Package case diagnosis, scoped implementation/retry/split/routing decisions, optional briefings and checkable work requests. Skills guide internal investigation and judgment, not obligatory response sections; code enforces authority.

## Delivery slices

Each slice needs a checkable exit gate before issue publication. These are planning briefs, not ready-to-dispatch issue bodies.

### C0 — Prove the operator interaction (direct work)

- Scope: throwaway read-only Pi extension using real Factory evidence; demonstrate concise greeting, natural question answering, bounded repository/CI investigation, case/source inspection, scope identity, refresh and resume. Exercise a non-executing preview/confirmation interaction, including a clearly marked sample delegated-work proposal.
- Touches: disposable prototype outside production entry points; pinned upstream Pi runtime. No dashboard or dispatch behavior changes.
- Exit: run the actual Linux terminal through an approved provider flow. Ask a real operational question, perform the needed reads, distinguish observations from hypotheses and give a concise recommendation with inspectable evidence. A follow-up "yes" to an investigation must result in that investigation, not another report or repeated permission request. Show unavailable capabilities/evidence honestly, interrupt/resume with fresh state, and confirm custom-tool isolation. Operator evaluates conversational usefulness, recommendation soundness and keyboard interaction, not just a correctly rendered briefing.
- Model comparison: establish a frontier-model reference interaction after explicit provider/authentication and evidence-disclosure approval, then compare Ornith on the same questions, available tools and controlled evidence. Use fresh conversations and the same lightweight prompt; record model/runtime, reasoning settings, context/output limits and tool outcomes. Evaluate factual accuracy, completed investigation, capability honesty, recommendation quality and conversational brevity. Keep prompt/tool changes separate from model changes; stock Pi with broader tools is not a model-only comparison. No model is accepted by reputation, and this plan does not authorize external disclosure.
- Handoff: accepted conversational interaction, package location, launcher behavior, bounded read/capability/result shapes, confirmation mechanism and initial action menu including scoped delegated implementation. Resolve SDK need from observed runtime limitations, not model quality.
- Excludes: production mutation, deployment, custom rendering and claims of persistent manager integration.

**Review closeout:** the operator explicitly authorized closing C0's read-interface review and preparing C1. The revised concise entry, conversational investigation and inspectable read evidence are the accepted direction for that slice. Earlier experimental findings below remain historical evidence, not broader acceptance. The non-executing proposal demo does not accept or enable real manager/worker actions. Preserve the current launcher, provider/auth state and working harness while C1 moves collection ownership into Python.

### C1 — Expose shared read-only FM evidence (Factory ticket)

- Depends on: C0 read-interface acceptance (recorded above) and the accepted non-persisting runtime reader from F03/#28. Coordinate existing dashboard/evidence owners; do not duplicate F03 or reuse a writable lifecycle observer.
- Scope: expose observe/inspect and bounded repository/CI investigation through Python JSON commands using existing collectors and repository/GitHub owners. Include revision-specific workflow/file reads, PR diffs/head SHAs, checks and Actions runs/jobs/logs, plus capability discovery. Preserve provenance, coverage, scope and timestamps; share collection with the dashboard rather than diverging. Extend only missing reads needed by accepted investigation scenarios, not a generic shell or arbitrary API proxy.
- Touches: `factory/cli.py`, existing dashboard/briefing and repository/GitHub read implementations, one shared module only if extraction is necessary; relevant behavior checks/docs.
- Exit: invoke real commands in a disposable controlled repository. Diagnose missing CI checks by inspecting workflow triggers, exact PR heads and matching check/run evidence; distinguish no run, failure, missing permissions and incomplete evidence without guessing a cause. Demonstrate cited case evidence, explicit partial-source failure, malformed request rejection and unsafe artifact refusal. Existing dashboard evidence behavior remains intact. Run the existing Python gate.
- Excludes: manager execution, new feedback collection already owned by B2, mutations and model inference.
- Prepared artifacts: [bounded C1 ticket draft](c1-read-interface-ticket.md) and [handoff with next-agent prompt](c1-read-interface-handoff.md). These are local preparation, not issue publication or dispatch.

### C2 — Ship the interactive read-only console (Factory ticket)

- Depends on: C1; C0 accepted terminal interaction.
- Scope: optional Linux launcher, pinned Pi package, isolated FM resources, lightweight conversational prompt, on-demand context/skills, concise greeting, useful case/repository/CI investigation, optional briefings, refresh and resumed-session re-observation.
- Touches: proposed `console/`, small Python launcher/CLI entry and user installation documentation. Keep Node out of ordinary Factory execution.
- Exit: actual `factory chat` opens on the chosen repository without an automatic briefing. An ordinary question triggers appropriate investigation and a proportionate, fact-grounded recommendation; an agreed follow-up is performed rather than re-offered. Unsupported reads are admitted accurately. Restart updates the greeting/evidence when source state changes. Exercise keyboard use, cancellation, provider failure and a narrow terminal. Verify no model shell/edit/write capability or ambient skill leakage.
- Excludes: live mutations and a separate chat implementation for the dashboard.

### C3 — Establish the shared guarded action interface (Factory ticket; human merge checkpoint)

- Depends on: C1 and an accepted authority/action-menu design from C0. Reconcile the existing #13/#15 and dashboard decision ownership before publishing; extend/reuse accepted implementation rather than creating a rival executor.
- Scope: Python prepare/apply contract for the agreed bounded operator actions, including scoped implementation requests through the accepted existing work-intake/worker mechanism. Exact previews, trusted confirmation binding, stale-state refusal, shared coordination, durable decision identity and honest results apply equally to delegation. Settle whether a supported request attaches to an eligible existing case or needs an explicitly approved minimal ticket-publication path; do not leave delegation as a promise without an executable route. Migrate existing dashboard callers of supported actions through the same guarded implementation; retain browser transport protections.
- Touches: current `dashboard.act` and its callers, shared decision implementation and accepted lifecycle/manager interfaces where applicable.
- Exit: a disposable real-command scenario shows one confirmed action and a shared receipt, including a scoped implementation request admitted through the existing worker owner and returning its case/run identity. A held/ineligible or already-claimed target is refused or reported blocked without duplicate dispatch. Competing console/manager attempts execute once; changed head/ownership and model-forged confirmation execute nothing; failed intent write blocks action; crash/replay and partial external success do not duplicate mutations. Existing dashboard actions pass through the same guards. Run the actual Python gate.
- Excludes: changing autonomous authority, bypassing gates, direct merge commands, direct product-code editing by the FM or general-purpose mutation escape hatches.
- Control: human review and merge because this changes the factory's own authorization machinery. Establish the hold outside the candidate's control before dispatching this ticket; if that cannot be enforced, implement this slice directly in a reviewed branch.

### C4 — Connect confirmed console decisions (Factory ticket)

- Depends on: C2 + C3.
- Scope: natural recommendation and offer to act, followed by Pi proposal display, trusted point-of-action confirmation, cancellation, apply and receipt rendering for exactly C3's supported menu. A conversational "yes" does not itself become model-created authorization; the host binds confirmation to the exact proposal. Inspect delegated work through the existing case/run reader.
- Touches: console extension only, plus contract-dependent checks/docs. No copied Python policy.
- Exit: operate the real terminal against a disposable repository: question → investigate → recommend → offer implementation → exact preview → confirm → existing worker admission → inspect real work/review result → report outcome. A queued receipt is not completion. Cancellation has no mutation, changed state requires a new preview, prompt injection cannot confirm, and partial/unknown outcomes remain visible. Dashboard and console use the same decision and work identities. Repeat from a resumed session without duplicate action.
- Excludes: publication or splitting beyond the minimal work-intake route explicitly accepted in C3; arbitrary agent/process spawning; duplicated scheduling or policy. Unsupported requests remain drafts or explicit blockers, not fake successful actions.

### C5 — Share durable FM continuity (Factory ticket)

- Depends on: C4 and the accepted manager notes/decision producer, currently described by #13/#14. Add no replacement store while that prerequisite is unresolved.
- Scope: retrieve relevant earlier decisions and operator-approved scoped standing instructions; use the same storage/reader as unattended FM. Preserve provenance, supersession and failed/partial outcomes. Conversation compaction cannot grant new authority.
- Touches: console context integration and existing manager-note interface only as required for a shared contract.
- Exit: record an authorized scoped instruction, close the console, start a fresh session, and run the unattended manager's actual context-building path in a disposable setup. Both see the same instruction/decision identity. Superseded instructions and unavailable history are explicit; one repository's notes do not become another's policy.
- Excludes: automatic promotion of chat text into policy, global vector memory or a permanent FM process.

### C6 — Connect District fleet navigation (District ticket)

- Depends on: C2's accepted installed launcher/read interface and District's existing registry/projection ownership. This may proceed while C3–C5 are implemented; its acceptance uses C5 when checking durable cross-scope isolation.
- Scope: enter from fleet context, inspect health/attention, select a registered repository and launch/switch the same console package with explicit identity. Reuse District's installed interfaces; do not copy the registry or build another console.
- Touches: District CLI/registry adapter and installation docs; exact paths and gate command must be read in that repository before publication.
- Exit: real District entry shows an unavailable repository distinctly from healthy, selects the intended Factory checkout, and never reuses a proposal or note scope from a different repository. Exercise against the installed accepted Factory/Pi versions, not only a source checkout.
- Excludes: bulk mutation, fleet scheduling changes, onboarding/removal, deployment and a new fleet manager implementation.

## Dependency and execution policy

Graph: C0 + accepted F03/#28 reader → C1 → C2; C1 → C3; C2 + C3 → C4 → C5; C2 → C6. C5 additionally requires the accepted manager-note producer. Final acceptance requires C4 + C5 + C6. Existing manager/evidence programmes keep their own dependencies and holds.

After C1, console work and guarded-action work can run concurrently with separate file ownership. Serialize the irreducible shared Python/dashboard edits with existing active owners. One human integration owner owns contract handoffs, acceptance evidence and release decisions.

Before publishing any ticket:

1. Refresh actual open issues, claims, assignees and PRs; reuse or amend unclaimed overlapping work rather than duplicate it. Coordinate a claimed contract with its owner.
2. Turn the slice into the standard Scope / Touches / Exit gate / Out of scope body. Include actual commands and accepted producer examples, not speculative interfaces. The current Factory gate is `python -m unittest discover -s tests`; add an explicit console verification command only after the package exists and the command has run.
3. Assign real issue numbers through separately authorized publication. Use normal `needs-triage`, never invented `Blocked by: #C1` or direct ready labels. An unresolved producer is a real intake hold, not prose that a worker can ignore.
4. Use same-repository numbered blockers only. Cross-repository acceptance is a human release checkpoint with accepted commit, installed version/schema and example evidence. No bare District number in a Factory blocker.
5. Code can be factory-built, but authorization/gate/provider-security changes and installation require human control. Do not change running factory configuration or install candidate code while it is implementing itself. Ordinary implementation retains the existing deterministic gate and independent review.

## Final acceptance and rollout (direct operator checkpoint)

Use an explicitly authorized disposable GitHub repository for mutation scenarios and the real Linux CLI. Authentication and provider disclosure approval precede model calls; confirm exact external changes at execution time.

Demonstrate this complete path: fresh launch → concise greeting → ordinary operational question → actual investigation → sound recommendation → offer to implement → exact preview → human confirmation → existing worker/process carries out scoped work → inspect implementation and independent review results → dashboard receipt → terminal close → fresh session and unattended FM consume the same durable decision. Then switch via District to another repository and verify scope isolation. Exercise stale state, unavailable evidence/capability, cancellation, competing operation and partial/unknown outcome alongside the happy path. Delegated code changes must produce real checked results; a successful queue submission alone does not accept the product.

Record terminal evidence, source citations, commands, versions and observed receipts. Include the missing-CI-checks conversation as a regression scenario: inspect actual workflows and PR-head runs, do not confuse provider availability with CI access, and do not equate repair with weakening a gate. The operator accepts natural conversation, completed investigations, sound recommendations and confirmation usability. A unit suite, attractive briefing or accurate citation IDs alone is insufficient.

Install only after separate authorization, using a pinned accepted package/runtime and documented rollback to the previous console version. Factory timers, dispatch policy, worker profiles and existing dashboard remain unchanged. Removing the console must leave the unattended pipeline functional. Retire the disposable prototype after its accepted behavior is implemented; keep the durable acceptance record, not duplicate production code.

## Immediate next step

Review the local C1 source delivery and verification in the [handoff](c1-read-interface-handoff.md) against the [ticket](c1-read-interface-ticket.md). Preserve the linked implementation worktree while using the current harness. Refresh PR22/PR24 shared-file ownership before any separately authorized integration. No publication, merge, installation, deployment, C2–C6 work or release of other programme holds is implied.

## C0 experiment record — 2026-09-05

### Disposition and reproducible launch

**Disposable implementation, not a production console.** Files are confined to
`console/c0-prototype/` plus this plan update. `factory chat` does not exist.
`factory/`, production CLI entry points, dashboard/dispatch behavior, managed
installations, timers and host configuration are intentionally unchanged.

**Current entry behavior (operator revision):** after the disclosure choice,
show a brief personalized greeting, the number of issues currently classified
by Factory as `escalated` or `needs-info`, and `/fm help` for command discovery.
Zero and unavailable counts are stated explicitly; the bounded snapshot is not
a claim of complete coverage. Entry makes no model request and does not print
the full command list. Ordinary questions receive proportionate conversational
answers; a full briefing is opt-in through `/fm brief` or an explicit request.
The automatic-opening-briefing exercises below describe the earlier iteration.

### Conversational harness revision and comparison

The revised harness has seven tools: `fm_observe`, `fm_inspect`,
`fm_investigate`, `fm_capabilities`, `fm_source`, `fm_resource` and
`fm_sample_preview`. The prompt is conversational; per-turn awareness contains
scope/time/coverage and source metadata, not full case bodies or the programme
plan. Case evidence, procedures and earlier decisions are fetched on demand.

`/fm investigate workflows` discovers registered workflow paths;
`file <ref> <path>` reads a regular repository file at a resolved commit;
`pr|checks|runs <PR>` inspects exact PR heads and matching evidence;
`run|log <run-id>` reads run/jobs or bounded job-log prefixes.
`/fm capabilities` reports the implemented menu and limits. Reads are scoped
GETs, not a generic API proxy or host filesystem tool. Complete negative tree
observations return cited `file_not_found`; truncated trees, inaccessible reads
and unsupported file types remain distinct. ANSI log styling is removed after
bounded pipe capture; no raw log escape sequences are emitted to the terminal.

The operator explicitly approved OpenAI evidence disclosure, selected GPT-5.5,
and completed native Pi ChatGPT OAuth in `.runtime/agent/auth.json`. No Codex/OMP
credentials were copied. Launch the exercised route from this checkout:

```bash
python3 -B console/c0-prototype/launch.py \
  --root /home/mike/dev/mikeroysoft/factory \
  --repository mikeroySoft/factory \
  --provider openai-codex --model gpt-5.5
```

Omit `--continue` for a fresh conversation; add it to resume that exact
repository/provider/model session. Ornith remains the default route. Explicit
`--provider openai --model <id>` uses native API-key authentication but was not
exercised. There is no automatic provider fallback.

**Observed comparison:** both fresh sessions used Pi 0.84.4, the same tools and
light prompt, thinking off, and the same three conversational questions about
PR #22. Both observed head `8c25fb735bc1f73ea41f7ae4dcbf5c6ed8768cdc`.
The file-error distinction was improved between the second and third question
for both models. GPT-5.5's answers were 160/46/79 whitespace-delimited words;
Ornith's were 381/323/428. Both performed the requested reads and follow-up.
GPT-5.5 gave the more direct recommendation and distinguished the old base SHA
from current main. Ornith remained more report-like, offered an unavailable
doctor/tree investigation and overstated that a new run would pass. Model
quality is therefore still an acceptance concern; this is one CI scenario,
not a broad benchmark. Catalog limits differ (GPT-5.5 272K context/128K output;
Ornith 131072/4096); none of these answers was truncated.

The final GPT session read real run `33991859881` and job `101375393268` logs,
identified `Ran 76 tests ... OK`, and correctly kept that successful main run
separate from PR #22. Native isolation reported all seven FM tools in the actual
provider payload and zero ambient skills/context files. Path traversal and shell
escape were refused. The scoped implementation sample confirmed with
`demo_confirmed_not_executed`, `executed:false`, `receipt:null`; it never
publishes or dispatches work.
A real GPT process resume refreshed from `22:32:25Z` to `22:48:27Z`, requested
disclosure again, and refused an unconfirmed sample left pending before exit.
Choosing Browse-only added no model answer. All verification Pi processes
exited cleanly; the operator's Ornith service and native auth were retained.

Local comparison transcripts (unpublished, not sanitized for sharing):
`.runtime/sessions/03fe2d4dd3a606ec/2026-09-05T22-32-24-031Z_01a073b3-94df-755a-ad0d-693d07593b47.jsonl`
and
`.runtime/sessions/f64847d404c8b653/2026-09-05T22-33-21-490Z_01a073b4-7552-70cb-ba2e-2194b6d02284.jsonl`.
`check.py` passed its real bounded-read, source, scope, negative-evidence,
symlink refusal, ANSI sanitation and absent-state checks. C0 remains read-only;
the read-interface review is now closed as recorded above; guarded real actions and broader product/security acceptance remain open.

### Original local launch and earlier experiment evidence

From this checkout:

```bash
# One-time local dependency installation only; already performed for this review.
npm ci --ignore-scripts --no-audit --no-fund --prefix console/c0-prototype

python3 -B console/c0-prototype/launch.py \
  --root /home/mike/dev/mikeroysoft/factory \
  --repository mikeroySoft/factory

# Resume the same scope's latest Pi conversation; still requires fresh observation.
python3 -B console/c0-prototype/launch.py \
  --root /home/mike/dev/mikeroysoft/factory \
  --repository mikeroySoft/factory --continue

# Repeatable read-boundary smoke; no inference.
python3 -B console/c0-prototype/check.py
```

Prerequisites exercised: Linux PTY, Node **26.7.0** (Pi requires Node ≥22.19),
npm **12.0.2**, Python **3.12.14**, `gh` **2.97.0** using the existing GitHub
keyring login, this Factory source checkout, and the operator's existing
`rocm serve ornith` endpoint. No runtime is downloaded by the launcher.

Runtime: **`@earendil-works/pi-coding-agent@0.84.4`**; its package-lock and
upstream shrinkwrap resolved Pi AI/agent/TUI/client/protocol dependencies to
0.84.4. Published package integrity:
`sha512-jmOlrqUmvhh/siNWFRXjYLJzhKFIHNsAQaysRwzQPQFnPAaV/vhqHsLH/MBsIISA1Rjj7WTUFR3nJrpXoLx39w==`.
Upstream [release package](https://github.com/earendil-works/pi/blob/v0.84.4/packages/coding-agent/package.json)
was checked independently of the installed global binary.

**Approved provider:** `http://127.0.0.1:11435/v1`,
`ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M`, Pi provider name `c0-ornith`,
OpenAI Chat Completions protocol. The operator explicitly chose this endpoint
and started the service; C0 did not start, install, restart or reconfigure it.
The model catalog subsequently returned that exact ID. No provider login was
needed: the local endpoint is keyless; Pi's `local-no-auth` value is a documented
local-provider placeholder, not a copied credential. This was the original local
experiment; the subsequently approved and exercised OAuth route is recorded above.

The exercised launcher disables Ornith's thinking channel using Pi's native
`samplingParams.chat_template_kwargs.enable_thinking:false`. The first long
briefing hit `stopReason:length` at 4096 output tokens. The final source-backed
opening completed with `stopReason:stop`, 979 output tokens and inspectable
citations. This is provider compatibility evidence, not a judgment-quality pass.

Before each launch/resume/reload's first inference, Pi's native select dialog
shows the repository, endpoint and disclosure categories. Default is **Browse
only — send nothing**; Escape/timeout also declines. Approval binds the exact
selected provider/model and covers selected issue/PR text, repository/workflow
files at requested revisions, checks, bounded Actions runs/jobs/logs, artifacts,
on-demand programme excerpts and conversation. No credentials, raw host
configuration or other repository is included. Declining still permits read-only
inspection and sample controls. Approval does not accept C0 interaction design.

The launcher accepts explicit root/repository, provider/model and `--continue`, never
arbitrary Pi argv, `@file` arguments or piped prompts. It creates mode-0700
prototype runtime directories, an isolated Pi agent directory and scope-specific
Pi transcripts under `console/c0-prototype/.runtime/`. These transcripts are
conversation evidence, **not a manager decision/notes store**. Pending previews
and observation caches exist only in extension memory.

### Evidence ownership and limitations

`evidence.py` imports this checkout's `dashboard.configure/snapshot` and
`briefing.sources_for`. It never calls `briefing.respond/run_model`, OMP,
`dashboard.act`, dispatch, triage or a model. The disposable adapter replaces
the incidental mkdir-producing lock-path helper with a pure path lookup,
disables the collector's inference-service availability probe, discards raw
subprocess stderr, sets `GIT_OPTIONAL_LOCKS=0`, and bounds read subprocesses.
The extension invokes it directly with JSON stdin, no shell; abort kills the
bridge process group. No public C1 command or production shared module was added.

`observe` returns compact cases and an attention count. It no longer selects an
opening case, preloads a case's source bundle or includes dispatcher run excerpts.
`inspect` explicitly invokes the existing source selector. Detailed programme
resources are also opt-in; no new priority, admission or dispatch policy is added.

The bridge's observation is explicitly **partial**: newest 100 issues/PRs,
bounded label/assignment/timeline/comment/check selections, bounded selected
source text, legacy stage/artifact heuristics and incomplete service-error
semantics. Existing collector whole-file/stdout reads remain inherited limits;
this is not F03's bounded, network-free runtime projection. Missing cases and
sources are unavailable, not healthy or nonexistent. The standard selector
still has the old escalation-path mismatch described in the manager plan;
C0 reports it rather than implementing #11/C1 integration.

New GitHub investigation reads bound response bytes during collection:
1 MiB JSON, one page of at most 100 list entries, 20 KiB per cited source and
up to five latest-attempt job-log prefixes (failed jobs first). Each command is
bounded to 20 seconds and a read to 90 seconds. Partial failures preserve useful
sources with `ok:false` and partial coverage. These limits do not repair the
inherited legacy collector limits above.

Programme decisions are fixed, cited excerpts of `docs/manager-plan.md`
(authority §4.3/4.4, current/proposed ownership, publication/hold ledger), exposed
alongside the curated `briefing`, `diagnosis` and `tickets` procedures. They are
dated planning evidence, not live state or new authority. #13/#14/#15 remained
open prerequisite work; merged #26 is not proof this checkout/installation has
F01. B2/B5 and the manager-note producer are not implemented by this prototype.
The existing source selector can surface the real #21 human-decision record
`191edbcc8d7d4dbc9cd3126cfe494160`; started intent and successful outcome remain
distinct, with the final recorded outcome selected.

Live state changed independently during C0: Pi inspection observed #27 with
`needs-triage`, no assignee, `updated_at=2026-09-05T20:23:13Z`; subsequent evidence
showed the existing dispatcher had claimed it for attempt 1. This demonstrates
why the dated hold ledger is not a current-state source.
**C0 performed no GitHub publication, comment, issue edit or label operation.**
It neither reverses someone else's newer state nor releases #28, B-programme,
District, installation, security, visual-acceptance or later-console checkpoints.

Selected source bodies remain untrusted. Common credential-token/private-key
forms fail closed at the extension's evidence boundary; no secret stores are
copied or exposed. This is not a comprehensive secret-classification/DLP
product. A later external-provider rollout needs an accepted evidence-export
policy, not an assumption that arbitrary historical worker logs are sanitized.

### Observed terminal exercises

These are agent-operated Linux PTY observations, **not human acceptance**.
Pi's stock editor, Markdown/tool display, spinner, source output and select
dialogs were used; no custom editor, renderer, fork, SDK session host or listener.

| Scenario | Observed result |
| --- | --- |
| Startup disclosure | Native keyboard dialog displays scope/endpoint/data categories; Browse-only is the default. Explicit approval precedes startup inference. |
| Provider failure | Before the operator started 11435, the real Pi request returned `Connection error`. No automatic retry/action; footer changed to `REFRESH REQUIRED`. No substitution to the available 13305 provider. |
| Native tool interaction | Ornith emitted a real `fm_inspect` call with schema 1 and explicit `mikeroySoft/factory` scope; real Factory sources were returned through Pi, not a nested OMP response. |
| Complete opening briefing | Final launch at `20:53:09Z` included the real #27 source bundle before inference. Pi rendered a complete decision question, attention rationale, recommended owner/next step, earlier decisions, uncertainty and supplied source IDs. Judgment defects below prevent acceptance. |
| Real case/source | `/fm inspect 11` and `/fm source S14838670430762645284` displayed the existing gate report at `wt-11/.factory/gate-report-11.md`. Current-state source `S8524467614005264364` showed open PR23, approval label and empty CI evidence; a gate PASS was not promoted to merge eligibility. |
| Unavailable evidence | `/fm inspect 999999` returned `unknown_case` and explicit bounded-coverage uncertainty. `/fm source ../../.env` was refused because source IDs are not paths. No arbitrary file was opened. |
| Interruption | Escape aborted live Ornith generation; the Pi transcript recorded `stopReason: aborted`. Evidence became stale, the sample was cleared, and the next turn required reobservation. |
| Actual process resume | Ctrl+D exited Pi with status 0. The exact `--continue` command reopened the same conversation and collected at `2026-09-05T20:37:50.079425+00:00`, replacing the prior `20:31:07.697480+00:00` observation before inference. Historical source update times were not rewritten. Confirming an old sample ID returned `No matching pending SAMPLE`. |
| Trusted sample confirmation | Exact repository, issue #11, sample identity and never-posted comment displayed; Cancel was initially selected. Down/Enter returned `demo_confirmed_not_executed`, `executed:false`, `receipt:null`. A second sample cancelled with Escape returned `cancelled_not_executed`. No Factory event/receipt was fabricated. |
| Model/resource isolation | `/fm isolation` reported exactly the five FM tools in both active tools and the actual provider payload, with `skills:0`, `contextFiles:0`. `!printf C0_SHELL_SHOULD_NOT_RUN` was intercepted with exit 126; no shell output was produced. |
| Bridge boundary smoke | `python3 -B console/c0-prototype/check.py` passed: real evidence, advancing observation IDs/times, wrong-scope/unknown-case/extra-approved-field/boolean-number rejection, absent state directory stays absent, production code/config bytes unchanged. This does not claim a sandbox or executor acceptance. |
| Final source display and cancellation | A manual refresh completed before `/fm cancel` took effect; cancellation then cleared the sample and marked the scope stale. `/fm source S17900140546263045332` displayed real issue text with `UNTRUSTED SOURCE`, repository, observation time and `scope stale:true`. This exercise does **not** prove interruption of an actively reading bridge subprocess. |
| Session-scope guard | `/new` was refused with instructions to exit and use the scope-bound launcher. Built-in switch/fork/tree/compact paths are cancelled; actual process `--continue` remains the supported C0 resume. |

**Material acceptance gaps found, not hidden by the successful render:**

- One earlier continuation invented a #27/PR43 merge and three source IDs not
  present in its actual selected evidence. The final fresh opening used real
  supplied IDs, but still conflated F01 acceptance with installation while also
  acknowledging the legacy checkout. Citations alone do not validate claims.
  Model recommendations and source interpretation need human review; **C0 is
  not ready to be accepted as reliable operational advice**.
- Ornith sometimes said it would inspect a case without issuing the tool call.
  A native `before_provider_request` replacement requesting named
  `tool_choice:fm_inspect` was observed in extension metadata, yet the actual
  stack returned a plain-text answer. The experiment did not isolate whether
  the provider or runtime ignored it. That ineffective constraint was removed;
  the final opening loads the real source bundle directly through the existing
  Factory selector instead. Ordinary model-initiated inspect/source calls
  were separately exercised successfully.
- Escape during inference and sample-dialog cancellation were verified. A
  manual-read cancellation race, full adversarial testing, external providers,
  multi-repository operation, compaction continuity, production receipts and
  closed-surface security are not accepted by these exercises.

Retained local Pi conversations are under `.runtime/sessions/f64847d404c8b653/`:

- `2026-09-05T20-25-53-213Z_01a0733f-c13d-76f2-b0aa-8f71058a4512.jsonl`:
  initial failure, tool interaction, abort, samples and actual resume.
- `2026-09-05T20-43-57-969Z_01a07350-4e91-7941-9857-9b73e455416f.jsonl`:
  intermediate source investigation and the unsupported continuation.
- `2026-09-05T20-53-07-888Z_01a07358-b2b0-73a6-a7f7-3327536e6fbd.jsonl`:
  final source-backed opening and terminal source/cancellation checks.

These are local review evidence, not publishable sanitized transcripts. Human
comprehension, recommendation quality, confirmation usability and installation
acceptance remain open.

### Concrete handoff proposals — not C1–C6 implementation approval

**Package location:** retain `console/` in this repository. Keep this experiment
under `console/c0-prototype/` until accepted behavior is replaced, then remove it.
Production Python remains dependency-free; pinned Node/Pi is optional and never
downloaded from normal Factory dispatch. Do not install C0 into the managed fleet.

**Launcher:** eventual `factory chat --repo <explicit checkout>` resolves and
displays canonical root/repository, checks the installed pinned console runtime,
and invokes one curated package. Fail with a useful prerequisite message rather
than silently installing, selecting a provider, using the ambient cwd or copying
auth. Explicit scope change clears previews and refreshes. Resume restores only
conversation and reobserves before advice. Keep local-only evidence browsing
available when inference is unavailable. Do not expose raw Pi package/attachment
argv through the Factory launcher.

**Versioned read interface:** C0 exercises this disposable stdin shape:

```json
{"schema_version":1,"op":"inspect","repository":"mikeroySoft/factory","number":11}
```

`observe` omits `number`; the canonical root is operator-supplied process context,
not a model-controlled path. Results contain `schema_version`, `ok`,
`scope:{repository,root}`, `observed_at`, fresh `observation_id`,
`coverage:{status,notices}`, and bounded `sources`. Observe adds `cases` and
`attention_count` (null when unknown); inspect adds `case`. Investigation adds
target/revision metadata; capability discovery adds the fixed read menu.
Sources retain `id,label,text,truncated,path?/url?`.
Failures add `error:{code,message}`, retaining partial sources when available,
never raw stderr.
Observation identity means **one read**, not an F01 event or semantic revision.
Source hashes identify the selected content, not authorization or freshness.

The current C0 read tools include `fm_investigate` and `fm_capabilities` alongside
observe, inspect, source and resource retrieval. Investigation kinds are
`workflows` (no target fields), `file` (`path,ref`), `pr|checks|runs` (`number`)
and `run|log` (`run_id`); requests reject unrelated fields. These remain
disposable schema-1 operations, not an accepted C1 public interface.
Production adds `fm_prepare` only after C3 acceptance. C1 must finalize source
errors/times/revisions, producer versions and bounds with the existing owners.

**Prepare/apply separation:** propose immutable prepare results with
`schema_version`, `scope`, `proposal_id`, `action`, typed `target/values`,
`rationale`, `evidence_refs`, `preconditions`, `expires_at`, and exact effects.
Production `apply` is a trusted host operation, **not a model tool**. It accepts
only the selected proposal identity and host-issued confirmation binding, never
model-supplied `approved:true` or replacement values. The shared Python owner must
revalidate relevant state/head/ownership/human activity under existing locks,
persist intent before mutation, and return the same durable decision identity
and per-step `success|failure|partial|unknown` receipt on replay. This remains
C3's human-controlled authorization work, including migrating dashboard callers;
`dashboard.act()` is not an acceptable tool wrapper.

**Confirmation mechanism:** Pi native select with **Cancel first** proved
sufficient for the sample. Only the `/fm confirm <sample-id>` command opens it;
the model can produce a preview but has no confirm/apply capability. Display
scope, exact target/values, consequences, identity and expiry. Production approval
must bind the immutable fingerprint/revisions and expire on relevant change;
the C0 random sample ID and in-memory object prove interaction only, not that
guarded execution contract. Never render a demo outcome as a real receipt.

**Initial supported menu proposed for C3 review:** (1) exact case comment;
(2) answer and normal re-triage; (3) human handoff without claiming assignment;
(4) guarded requeue; (5) request scoped implementation through the existing
Factory work owner. Define typed named operations, not label arrays, arbitrary
commands or free-form agent-spawn authority. Implementation requests need an
accepted executable intake route, an approved scope/exit gate and a real case/run
identity. Reuse an eligible existing case; any necessary minimal new-ticket
publication must be separately included in the accepted menu and exact preview.
Comment-only may be an intermediate checkpoint, not final delivery.

Broad ticket publication/splitting/rewriting, profile changes, closure,
assignment takeover, PR approval/merge, cleanup, standing instructions,
release/security/host-management and bulk/fleet actions remain outside this menu.
Existing owners, holds, review gates and separate authority remain mandatory;
delegation cannot be used to acquire otherwise unavailable capabilities.

**Extensions versus SDK:** extensions suffice for the demonstrated C0 briefing,
tools, sources, refresh/resume, scope display and sample confirmation, using
Pi's existing terminal UI. Runtime/provider-specific issues were observed:
`ctx.getSystemPromptOptions()` was absent in the bundled 0.84.4 CLI despite the
unbundled documentation; reading `before_agent_start.systemPromptOptions` worked
and reported zero ambient resources. The named-tool request experiment above
also did not enforce a call; that is not evidence that changing hosts would
fix this provider. Neither observation required an SDK for the retained C0.

The material remaining limitation is stock Pi's operator command surface:
`/login`, `/logout`, `/share`, `/settings`, `/scoped-models` and built-in
`/llama.cpp` are not removed by tool/resource flags, and built-in command dispatch
precedes extension input hooks. CLI `@file` parsing also precedes those hooks
(the C0 launcher rejects positional arguments). Extensions are trusted host code,
not an OS sandbox. Therefore **do not recommend an SDK merely to reproduce C0**.
If human review requires a genuinely closed operator-command surface, this
observed interception gap justifies evaluating an SDK-owned host/submit boundary
while reusing upstream terminal components; it does not justify a Pi fork or a
new renderer now. A stock-Pi prototype must not be advertised as that boundary.

**Checkpoint:** C0's read-interface review is closed under explicit operator authorization. The bounded C1 ticket and next-agent prompt are prepared locally, not published or implemented. The current harness is preserved for continued use. C1 depends on the accepted #28 reader; no remaining programme hold, manager authority or installation status is advanced by this closeout.
