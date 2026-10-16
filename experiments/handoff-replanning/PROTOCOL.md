# Worker-handoff replanning experiment

Date: 2026-09-10. Status: protocol frozen before experimental model calls.

## Question and hypothesis

Does returning real implementation reports to an outcome planner improve its recommendations for the remaining work in an approved initiative, beyond what the plan and current ticket/PR state already reveal?

Hypothesis: successful-worker reports contain useful constraints and discoveries that are not represented by issue closure. An evidence-enriched planner should propose concrete downstream plan refinements without inventing requirements or granting authority.

## Scope and authority

Case: approved collaboration programme [#52](https://github.com/mikeroySoft/factory/issues/52), including accepted reader #53/PR60 and unaccepted router #54/PR61. This is a retrospective, read-only shadow trial against genuine work, not new worker execution, a prospective live rollout, or proof of delivered product outcomes. The programme document itself is not assumed to be a canonical `initiative` record.

Allowed: frozen public GitHub plan/status evidence, retained local worker reports, local prompts/results, no-tools model inference using the existing authenticated OMP profile, and independent source adjudication.

Forbidden: issue/PR edits or comments, labels, claims, dispatch, code changes to Factory, merges, release holds, services, deployment, provider/account configuration, publication, and automatic application of recommendations. Existing #54/#15 ownership and #55–#59 holds remain unchanged. Do not treat this protocol or a model response as an authorization artifact.

## Evidence boundaries

- Pin main at `8562270ea7105ab03d1537fa46bbd1737266adab` and stable at `92cd1e86232992f95a2db3e7f74261aadfb82428`.
- #53/PR60 accepted head: `b05c356566b86bd54da263b93c61f29c8fd49dd6`, merged to stable, not evidence of installation or initiative delivery.
- #54/PR61 unaccepted head: `b7a9aca269ff87321d3b0cd602d13836f1374c58`.
- The original `handoff-53.md` was removed with its worktree. Use the retained original worker closing reports from attempts 1 and 4, verbatim and explicitly attributed. Do not reconstruct a missing handoff or present the reports as independently verified test results.
- #54's actual handoff and escalation packet remain available. Paths in frozen source text are provenance, not permission to read other files.
- All conditions receive the same current plan, ticket bodies/statuses and PR metadata, including branch identities. Therefore detecting a main/stable mismatch is not credited as a handoff-specific discovery.
- Source oracles inspect exact-head code independently, before viewing experimental outputs. The main adjudicator has previous context; this is not claimed as blinded human research.

## Conditions

Use the identical prompt, model, reasoning settings, no-tools execution envelope and shared packet in every condition. Only additional evidence differs:

1. **baseline**: #52 plan; #53–#59 issue bodies/statuses; #15 status/contract; #60/#61 PR metadata; pinned branch metadata.
2. **success**: baseline plus the two original #53 successful-worker closing reports.
3. **mixed**: success plus #54's original handoff and escalation/review evidence.

Run two independent fresh sessions per condition: six model calls, at most two concurrently. Interleave conditions by sample. Do not selectively rerun a valid but unhelpful result. Infrastructure failures remain failed/unknown; a retry, if required, is recorded separately. This tiny repeated case is diagnostic, not a success-rate estimate. The mixed condition separates the value of familiar failure remediation from success-path planning.

## Planner contract

Return at most five prioritized, evidence-cited recommendations for the remaining programme. Each names: target, concrete proposed adjustment, cited observation, why it changes the remaining plan, unchanged authority/ownership, and the smallest check that would resolve it. Distinguish a new plan refinement, an already-recorded prerequisite, local remediation, and an unresolved question. No recommendation is a valid outcome. Do not manufacture work to fill the list. Do not infer acceptance from tests, issue closure, merge, or the word 'done'.

## Predeclared adjudication

Count a recommendation as **supported and actionable** only if its cited source actually supports the observation, the proposed change follows from the approved outcome/boundaries, its owner/target and next check are concrete, and it preserves holds/authority. Count duplicate recommendations once per run.

Classify every item:

- **Useful plan refinement**: supported downstream consequence beyond restating an existing requirement, changing only proposed future work.
- **Already known**: sound but already explicit in shared plan/metadata; no handoff increment.
- **Local remediation**: fixes #54 or its delivery mechanics but does not establish successful-handoff replanning value.
- **Open question**: useful evidence gap with no assumed answer.
- **Unsupported / overreach**: fabricated evidence, optional opinion promoted to requirement, needless new scope, silent rebinding, authority expansion or false delivery claim.

Check especially: consumer handling of bounded/partial/malformed reader evidence; reuse of accepted reader/template instead of duplicate implementation; distinction between fixture coverage and live multi-human acceptance; exact `--json` criterion versus optional review suggestions; and branch integration versus issue closure. These are candidate checks, not a mandate for the model to mention each.

**Decision rule:** recommend a prospective human-reviewed shadow pilot only if the success condition produces at least one independently supported, non-duplicate downstream refinement absent from baseline in both repetitions, with no unsafe recommendation accepted. Otherwise report mixed/inconclusive or no observed incremental value. Mixed-condition success alone cannot satisfy this rule. Any unsafe proposal is reported even if adjudication rejects it. No production automation is qualified by this case.

## Records and execution ownership

Keep this protocol, source packet and SHA-256 manifest, exact prompts/argv/model/version, raw stdout/stderr/exit status/duration, source oracles, item-level adjudication and final report under `experiments/handoff-replanning/`, following the existing `experiments/reviewer-calibration/` convention. No changes to that earlier experiment.

Write decisions at three boundaries: protocol before running; evidence and accepted/rejected findings after running; an executable ticket only after a human chooses the mechanism and acceptance contract. Do not copy the whole experiment into the canonical initiative; a later authorized amendment should link evidence and record only the accepted plan delta.

Direct lane: experimental protocol, evidence interpretation, initiative/authority decisions, admission/merge/reviewer policy, and orchestration changes. Factory lane: later bounded read-only collectors, displays or report consumers with accepted input/output contracts. Human checkpoint: scope amendments, holds, publication, merges/releases/deployment, and real-account acceptance. Nothing is queued by this experiment.
