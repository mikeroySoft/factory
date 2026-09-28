# Successful-worker feedback can improve the remaining plan

Date: 2026-09-10. **Completed: one retrospective shadow experiment, six model calls, no production actions.**

## Decision

The experiment met its predeclared threshold for recommending a prospective, human-reviewed shadow pilot. Both success-feedback runs identified the same concrete downstream boundary that neither baseline run raised: **#57 must not bind canonical execution scope using #53's truncated display representation.**

This is evidence of usefulness on one case, not qualification for autonomous replanning, a measured improvement rate, or proof of reduced cost/human effort. No recommendation was applied to GitHub or production.

## What ran

Approved collaboration programme [#52](https://github.com/mikeroySoft/factory/issues/52), with successful reader [#53/PR60](https://github.com/mikeroySoft/factory/pull/60) and unaccepted router [#54/PR61](https://github.com/mikeroySoft/factory/pull/61). #52 is the approved programme document, not assumed to be a canonical `initiative`-labeled record.

The protocol was saved before model calls: [PROTOCOL.md](PROTOCOL.md). Three conditions, two fresh no-tools sessions each, identical prompt/model/settings:

| Condition | Added evidence | Observed recommendations in both repetitions |
|---|---|---|
| Baseline | Approved plan, issue contracts/statuses, PR metadata and pinned branch identities | Reconcile main/stable integration; obtain/link existing acceptance evidence. No new handoff-derived refinement. |
| Success | Baseline plus #53's two original worker closing reports | Same integration prerequisite, plus the #57 canonical-content versus truncated-display boundary. |
| Mixed | Success plus #54's actual handoff and escalation/review packet | Integration prerequisite, the known local `--json` repair, and a #56 path-source question. The #57 boundary was absent in both runs. |

Runtime: `omp/18.1.15`, model `openai-codex/gpt-6-astra`, medium reasoning. Six invocations exited 0 with valid JSON, in 136.01 seconds wall time at concurrency two; individual calls took 36.98–49.89 seconds. Dollar cost and token usage were not captured. No selective reruns or additional model trials occurred.

```sh
# Replays six model calls and incurs model cost; requires existing authenticated omp.
python experiments/handoff-replanning/run.py /tmp/handoff-replanning-new-run

# Local, no-model/no-network corroboration of the accepted parser boundary.
python experiments/handoff-replanning/probe.py
```

## Finding 1 — a successful implementation exposed a future integration risk

The final #53 worker report, source H53B, records section truncation at 4,000 characters and a `malformed` result. Planned [#57](https://github.com/mikeroySoft/factory/issues/57) requires canonical Outcome/Boundaries/Plan/Success evidence, SHA-256 binding and drift detection. Both success-feedback runs connected these facts without being instructed to find this specific risk.

Independent source inspection corroborated the report against accepted PR head `b05c356566b86bd54da263b93c61f29c8fd49dd6`. The [parser probe](probe.py) exercised the accepted pure parser/constants, AST-selected from a frozen exact-head source snapshot:

```text
input Plan lengths:                 4001, 4001
retained display Plan lengths:      4000, 4000
full-content digests differ:        true
display-content digests equal:      true
malformed:                         true, true
problem: truncated section: Plan cut to 4000 characters
```

The reader is honestly flagging its limit. **This is not an existing #57 admission vulnerability:** #57 is unimplemented/held, and no admission code was exercised. It is a grounded future-consumer risk if the consumer ignores incompleteness or hashes a display projection as if it were the full contract.

### Proposed amendment for human review, not applied

For #57, make the canonical-content boundary explicit: capture complete relevant sections, or refuse new admission with an explicit incomplete-source reason. Never silently bind to a shortened display value.

Add to its existing disposable drift scenario: a relevant section exceeds 4,000 characters and only its suffix changes. Observe full-content drift detection with unchanged admitted scope, or explicit refusal of incomplete-source admission. Historical accepted evidence remains readable.

This is an amendment to a held control-plane ticket's acceptance contract. It needs human scope review; the experiment does not release #57 or authorize implementation.

## Finding 2 — failure detail changed which planning insight survived

The mixed condition identified two different items in both repetitions:

- **#54 `--json`:** a real, already-reported local CLI-contract repair. Correct, but not evidence for success-path replanning. The known failing command was not rerun here.
- **#56 routing input:** the router currently receives implementation paths through `--path`; the future terminal-handoff consumer needs an evidenced path source or an explicit missing-path/unassigned result. Main classifies this as a useful open question. First inspect existing inputs; a new PR-files read is not automatically required and would need human scope review.

Neither mixed run retained the #57 clipping insight, although each returned only three recommendations under a five-item limit. **Inference:** detailed failure evidence may redirect planning attention. This one case does not establish causation or justify adding another agent role. It does support testing success/outcome reflection separately from immediate repair triage rather than putting every concern into one manager prompt.

## Finding 3 — success evidence must survive cleanup

The original `handoff-53.md` was unavailable after successful-worktree cleanup. The genuine worker closing reports remained and were supplied verbatim; they were not a reconstruction of the missing handoff or independent proof of the claimed tests.

Current-main [`cleanup_after_merge()`](https://github.com/mikeroySoft/factory/blob/8562270ea7105ab03d1537fa46bbd1737266adab/factory/dispatch.py#L894) removes the worktree containing that gitignored handoff. Retry/escalation consumers preserve more explicit narrative than the successful path inspected here.

Before a prospective pilot, preserve the next relevant successful handoff as an immutable local evidence artifact before cleanup, with ticket/head/provenance. Reuse the existing artifact/journal conventions. A bounded non-authorizing evidence-capture ticket may be suitable for Factory once its contract and integration owner are approved; do not introduce a new memory store or let captured text become authority.

## Adjudication and safeguards

Two independent source scouts prepared factual checks before viewing trial outputs. Separate usefulness and authority reviewers assessed all 14 recommendation instances; Main adjudicated their findings in [adjudication.json](adjudication.json). Review source IDs made condition membership inferable, so this is **not a blinded study**.

- Success-specific #57 refinement: absent from both baselines, present in both success runs.
- Shared branch mismatch: excluded from handoff credit because every condition received that metadata.
- #54 repair: classified as local remediation, not successful-handoff learning.
- No authority breach found in the recommendations; zero unsafe recommendations accepted.
- #56 input additions and #57 acceptance amendments remain explicit human-review proposals.
- Source oracles were fallible: Main corrected a wrong dependency inference, an overstatement that implementation progress was not meaningful, and a mistaken causal explanation for a later manager failure. Raw outputs and corrections remain recorded.

A suspected source-capture defect was also resolved: long lines were clipped only in the transcript display. All nine stored issue bodies matched full API bodies with unchanged revisions. No rerun was needed or performed. [Capture check](CAPTURE-ADDENDUM.md), [equality evidence](evidence/capture-fidelity.json).

[Integrity verification](evidence/integrity.json) confirms the original source, protocol and runner hashes; identical per-condition prompts across repetitions; raw-output hashes; all cited IDs in their supplied packets; and complete recommendation fields. All stderr files contain only OMP's normal `Working...` notice. No project-wide tests were run because this experiment changes no production behavior.

## When to write things down

1. **Before an experiment or delegation:** one short hypothesis/protocol, approved scope, source baseline, stop rule, decision criterion and authority boundary. This prevents moving the goalposts after seeing output.
2. **After evidence changes a decision:** retain raw evidence and record accepted/rejected conclusions with reasons. A worker report is an observation; the reviewed plan delta is a decision. Do not turn every thought into a permanent specification.
3. **Before Factory intake:** publish a bounded, human-approved executable contract with real dependencies, observable exit evidence and non-goals. Amend existing tickets where they already own the work; do not create duplicates for #56/#57.
4. **After a delivered result:** update the authoritative initiative/release record only at its actually proven boundary. Merged, installed and owner-confirmed delivered remain different facts.

The experiment folder is the evidence record. #52 remains the canonical programme discussion; a later authorized amendment should link this record and contain only the accepted delta, not a copy of the whole experiment.

## Direct versus Factory ownership

| Work | Lane | Why |
|---|---|---|
| This experiment, interpretation and choosing plan amendments | Direct orchestration | The acceptance model is being evaluated, not granted authority to approve itself. |
| #57 binding/admission behavior and its reviewed acceptance amendment | Direct, non-agent control-plane PR after existing checkpoints | Changes what authorizes work; cannot self-approve or bypass holds. |
| #56 path-source decision or new integration scope | Human/direct decision first; preserve existing #56 owner and control-plane lane | Resolve actual evidence needs before assigning implementation. |
| Bounded handoff retention, read-only evidence collection, summaries or display | Factory after contract/dependency approval | Ordinary non-authorizing producer/consumer work; review any shared dispatch/lifecycle integration explicitly. |
| Automatic requeueing, scope rewriting, hold release, policy/model/merge changes | Direct plus explicit human checkpoint | Changes authority or live scheduling. Not authorized here. |
| Publishing findings/tickets, merges, releases, deployment and real-account pilots | Separate point-of-action approval | Local experiment authority does not imply external mutation authority. |

**Recommended next step:** human-review the specific #57 amendment, then use the next eligible ordinary Factory task for a prospective shadow pilot that retains its full successful handoff. Do not create a new planner hierarchy or enable automatic replanning from this result.

## Limits and state

One selected programme, two repetitions per condition, one planner model, no historical blinded holdout and no measured human-time savings. The successful full handoff is missing; the observed gain used retained closing reports. The probe proves a pure-parser boundary, not a full CLI or current admission defect. A prospective pilot must measure whether humans accept/use the recommendations and whether they avoid actual rework.

All files are local under `experiments/handoff-replanning/`. No code outside this experiment, issues, PRs, labels, claims, programme holds, services, provider configuration, releases or deployments were changed. Nothing was committed, published or queued through Factory.
