# Agent-native system proposal: grounding and evidence

Prepared 2026-09-11 UTC. This is the evidence/status companion to [the design](agent-system-design.md), [contracts](agent-system-contracts.md) and [implementation plan](agent-system-plan.md). It is not a live operational dashboard or an authorization record.

## 1. Observed source and executable baselines

| Plane | Observed fact | What it does not establish |
| --- | --- | --- |
| Local working tree | HEAD `36cf1da27a503241e6648fc015687c4d4d1a3646`; `main` behind its remote-tracking branch, with substantial modified/untracked dashboard, console, documentation and experiment work. | Current canonical source, installed behavior, or permission to reset/merge local work. |
| Canonical Factory main source | `git ls-remote origin refs/heads/main` returned `c75a5a7a384693d2c377fab7a543242535cde77d`. Main-source reads were pinned to that revision. | Installation, running-service revision, or accepted work on another branch. |
| Stable integration source | `git ls-remote origin refs/heads/stable` returned `92cd1e86232992f95a2db3e7f74261aadfb82428`. Its `factory/plan.py` was read; PR60 is merged with base `stable`. That path returned 404 at pinned main. | Availability of the accepted initiative reader on main or permission to integrate/release it. |
| Installed CLI | `/home/mike/.local/bin/factory` invokes the uv-installed interpreter; `factory --version` returned `0.3.0`. Its help includes `manage` and `evidence`. The actual evidence/runtime read paths were exercised below. | Exact package source commit, service executable/import provenance, all feature support, or deployment acceptance. |

The local source help lacks `manage` and `evidence`; the installed CLI has both. Local C1 handoff documents accurately record an earlier local-delivery checkpoint, but canonical main now contains `factory evidence`. A design based only on this working directory or those historical status paragraphs would propose rebuilding existing functionality.

All pre-existing local work is preserved. This task creates only the four linked proposal documents; it does not implement the proposals, update existing plans, change configuration, run the pipeline or mutate GitHub.

## 2. Source map

Factory links below are immutable at the canonical baseline unless explicitly marked historical/live. File/symbol links identify implementation owners; they are not claims that those files need blanket refactoring.

| ID | Primary source | Used for |
| --- | --- | --- |
| S1 | [Canonical README](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/README.md) | CLI, lifecycle/runtime/evidence/feedback contracts, exact-head safety, limits and completion distinctions. |
| S2 | [Evidence module](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/evidence.py), [CLI](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/cli.py) | Existing single-request schema-1 read owner and capability discovery. |
| S3 | [Dispatcher](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/dispatch.py), [lifecycle](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/lifecycle.py) | `build_prompt`, `worker_round`, `review`, `approve_pr`, `merge_pass_locked`, `cleanup_after_merge`; execution/journal ownership. |
| S4 | [Dashboard](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/dashboard.py), [manager](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/manage.py) | Separate current action paths and recorded decisions; shared read selection; model/menu versus human action semantics. |
| S5 | [Brief](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/brief.py), [learn](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/learn.py), [stats](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/stats.py) | Existing deterministic context, lessons/curation and observational worker/brief metrics. |
| S6 | [Feedback module](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/factory/feedback.py), [B2/#79](https://github.com/mikeroySoft/factory/issues/79) (live issue) | Source/version/head identity and coverage; already-landed producer, separate later delivery owner. |
| S7 | [Autonomy execution plan](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/docs/autonomy-execution-plan.md), [canonical manager plan](https://github.com/mikeroySoft/factory/blob/c75a5a7a384693d2c377fab7a543242535cde77d/docs/manager-plan.md) | Existing lanes, twelve loops, policy checkpoints and programme ownership. Their embedded status tables are dated planning records. |
| S8 | [Collaboration programme #52](https://github.com/mikeroySoft/factory/issues/52) (live issue) | Canonical initiative vocabulary; owner versus claim; existing #53–#59 dependencies, holds and outcome acceptance. |
| S9 | [Local FM console plan](fm-console-plan.md), [C1 handoff](c1-read-interface-handoff.md), [codebase history plan](codebase-history-plan.md) | Accepted/historical local interaction direction and prototype integration. These are working-tree documents, not a claim of current deployment. |
| S10 | [Handoff replanning report](../experiments/handoff-replanning/REPORT.md), [prospective report](../experiments/handoff-prospective/REPORT.md) | Actual limited experimental evidence and the separately owned waiting pilot. |
| S11 | [District PRD](https://github.com/mikeroySoft/district/blob/6ba74c3bd99774c74a234c0b1690963822129704/PRD.md), [host owner](https://github.com/mikeroySoft/district/blob/6ba74c3bd99774c74a234c0b1690963822129704/district/host.py) | Registry/host-versus-repository ownership. No fleet registry or live installation was operated for this proposal. |
| S12 | [Accepted stable initiative reader](https://github.com/mikeroySoft/factory/blob/92cd1e86232992f95a2db3e7f74261aadfb82428/factory/plan.py), [PR60](https://github.com/mikeroySoft/factory/pull/60) (live PR metadata) | Separate accepted source baseline, declared initiative stages and explicit 4,000-character section clipping; not admission authority. |

Three read-only research slices examined control/evidence, worker/knowledge, and programme/fleet ownership. Primary-source reconciliation preserves these distinctions: full lifecycle observation can persist reconciliation; action journaling alone does not establish exactly-once execution; B2 is now on main despite older “unpublished” planning text.

## 3. What already exists and what remains a proposal

| Concern | Grounded current state | Design consequence |
| --- | --- | --- |
| Machine-readable observation | C1 schema-1 `capabilities/observe/inspect/investigate` exists on canonical source and the installed capability call works. Sources, coverage, errors and strict scope are already part of its contract. [S1–S2] | Extend it; do not invent another evidence server. |
| Runtime truth | F01 causal executions, F02 resource/wait evidence and F03 bounded non-persisting runtime reads exist. Full dashboard reconciliation can write; F03 does not. [S1, S3–S4] | Preserve reader effect classes and existing IDs; never route a read-only client through writable observation. |
| Merge safety | Current source binds successful gate/review/approval evidence to exact PR head, refreshes with fresh review, checks configured PR target and uses expected-head merge protection. [S1, S3] | Preserve this implemented baseline. The proposal does not present previously fixed approval defects as current. |
| Feedback evidence | `feedback.collect` supplies simultaneous review/thread/check facts with source revisions, independent coverage and head-race semantics. #79 was observed closed; the module is on pinned main. [S1, S6] | Existing B2 feeds #15; no new B2 ticket. Closure alone does not establish installed support or #15 acceptance. |
| Action coherence | `dashboard.act` validates a request, allocates a fresh decision ID, records intent, executes steps and records a success/partial/failure result. Manager `apply` and dispatch own other paths. No common preview-bound public apply contract is established by those records alone. [S4] | C3/B5 is the real seam. An audit record is not replay suppression; replacing a subprocess helper alone cannot implement authority or atomic external effects. |
| Deterministic orientation | `brief.py` extracts up to 12 nouns, ranks up to 15 files, includes up to three PR references and caps generated text at 6,000 characters. `ensure()` returns an existing brief file without validating a contract/head cache key. [S5] | Keep the existing brief and make applicability explicit; adding a second briefing engine would duplicate work. A file cache saves computation but does not prove freshness. |
| Successful handoff continuity | Current cleanup removes the worktree containing the worker handoff. The retrospective experiment could not retrieve the original #53 handoff after cleanup and used retained closing reports instead. [S3, S10] | Retain available complete artifacts with provenance before cleanup. Do not reconstruct originals from narrative and call them captured evidence. |
| Durable context | Worker lessons and manager notes already exist; manager notes are bounded replaceable text, not an append-only structured acceptance ledger. [S1, S4–S5] | Reuse their ownership; make proposed/accepted/superseded knowledge and applicability explicit where consumers need it. |
| Metrics | Existing first-gate/attempt/cost/worker and brief cohorts are usable observations. This investigation did not establish complete token, tool-I/O or human-time metering. [S1, S5] | Qualify full-loop cost and decision quality; do not claim existing cohort rates prove causal benefit. |
| Shared outcomes | #52 defines explicit initiative stages and separates initiative owner, decision owner and execution claim. Child closure never establishes delivery. Its consumer/admission work has existing holds and dependencies. [S7–S8] | Use that domain contract; do not create a parallel roadmap or turn a person field into permission. |
| Graph navigation | Existing codebase extraction is optional, revision-pinned and coverage-aware. It is a structural/history view, not a worker scheduling or authority source. [S1, S9] | Compare narrow retrieval against present briefs before adding startup work. |

## 4. Actual read-only interface probes

These commands were run for this design investigation. No model inference, pipeline dispatch, gate suite, GitHub mutation or service operation was invoked. Timings are single local observations, not benchmark claims.

### Command discoverability

```text
python -B -m factory.cli --help
  local source: init doctor install triage dispatch gate stats learn dashboard codebase

factory --help
  installed:   init doctor install triage dispatch manage gate stats learn dashboard evidence codebase

factory --version
  0.3.0
```

The executable wrapper points at `/home/mike/.local/share/uv/tools/factory/bin/python3`. This observation explains the CLI/source difference; it does not inspect the service's actual imported module.

### Installed capabilities

Invoked `/home/mike/.local/bin/factory evidence --root /home/mike/dev/mikeroysoft/factory` with one stdin object:

```json
{"schema_version":1,"repository":"mikeroySoft/factory","op":"capabilities"}
```

Observed at `2026-09-11T00:38:12.742369+00:00`: exit 0; elapsed approximately 0.068 seconds; `ok:true`; correct repository/root; `coverage.status:"complete"`; `errors:[]`; evidence/runtime schema 1 support; `actions:[]`. It advertised a 4,096-byte request bound, 500,000-byte response bound, 90-second whole-read limit and fixed supported read kinds.

**Inference for design:** the useful foundation is already a cheap deterministic operation. An agent should discover support rather than receive a large static description that might be stale. The empty action menu is a real capability limit, not a reason to synthesize a shell action.

### Installed bounded runtime

Invoked `/home/mike/.local/bin/factory dashboard --runtime-json`.

Observed at `2026-09-11T00:38:32.652619Z`: exit 0; elapsed approximately 0.119 seconds; schema 1; correct repository. At that observation the dispatcher service was inactive, timer active, and admission capacity was `configured:2, active:0, complete:true`.

Selected history fields were:

```json
{
  "complete": false,
  "truncated": true,
  "gaps": ["byte_limit", "unsupported_record", "event_limit", "missing_enter"],
  "bytes_read": 1048576,
  "event_limit": 512,
  "retained_events": 512
}
```

The returned execution array had 62 entries, all with `state:"completed"`; this is only the returned window, **not proof that every historical execution or unresolved descendant was represented**. Structured errors carried the same history/entry gaps. No repair was attempted.

**Inference for design:** bounded evidence works and is fast on this observation, but “exit 0,” “no active admission locks,” “all returned executions completed,” and “complete execution history” are different facts. Decision-ready views and targeted history must preserve those distinctions rather than cosmetically changing unknown to healthy.

## 5. Experimental evidence and its limits

The [retrospective handoff experiment](../experiments/handoff-replanning/REPORT.md) ran six model calls: baseline, success feedback, and mixed success/failure feedback, two repetitions each, on one programme. Both success-feedback runs raised the same concrete future-consumer risk: a canonical execution scope must not be bound from a truncated display representation. The baseline runs did not raise it; the mixed runs raised different concerns.

What this supports: investigate successful-result feedback separately from immediate repair triage, and retain original handoffs before cleanup. What it does not support: broad autonomous replanning, a measured improvement rate, a causal claim about failure salience, or reduced dollar/human-time cost. The report explicitly says token/dollar cost was not captured.

The [prospective report](../experiments/handoff-prospective/REPORT.md) inspected for this proposal says `READY_WAITING`, zero planner model calls, for the separately authorized #6→#7 pair, with #5 still its external prerequisite. It is a protocol/capture readiness record, not a completed trial. This task does not alter, resume, monitor or re-enroll that experiment. Its current live state may later differ from the inspected report.

Historical discussion about orientation cost was used as background, not imported as a new measured result: no transcript-cost analysis was rerun here, and the current canonical brief is materially different from earlier bringup descriptions.

## 6. Scope of verification and remaining uncertainty

Verified for this proposal: source-backed architecture/ownership, actual local-versus-installed CLI divergence, real installed read-capability support, real bounded runtime semantics, and existing experiment conclusions at their stated scope. The document set is also checked for internal links, headings and cross-contract consistency before delivery.

Document checks passed: four documents, 23 local links/anchors with no broken targets, balanced code fences, and nine declared delivery slices with 12 dependency edges and no cycle. The document set contains 20 external citations; this count is not a claim that every remote URL was independently fetched.

Two independent read-only reviews examined correctness/authority and coherence/resource economy. Their actionable findings were incorporated: preserve existing receipt vocabulary, require durable applicable proposals, specify schema discovery, pin stable separately, serialize shared reader edits, make the first guarded action concrete, expose earlier improvement dispositions, and predeclare efficiency metrics/confirmation. Main also made legacy unbound scope explicit. Review and document checks qualify this proposal's consistency, not future implementation or production safety.

Not verified: running services' exact imported source, whole-fleet health, permissions/branch protections, generalized isolation, complete backlog coverage, current #15/B5/C3 acceptance beyond the cited programme/source evidence, production learning gains, or any end-to-end release/outcome completion. These are deliberately not inferred from a package version, issue closure or a design document.

No production tests are run because no production behavior changes. Implementation slices require their own real-interface and scenario proof under the proposed plan; this document does not report those future checks as completed.
