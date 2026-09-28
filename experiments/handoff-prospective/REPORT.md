# Named-pair handoff pilot — #6 → #7

Status: **TERMINAL — selected producer #6 escalated; zero planner model calls; no replacement case.**

The user selected producer [#6](https://github.com/mikeroySoft/factory/issues/6), head-specific PR-review publication, and consumer [#7](https://github.com/mikeroySoft/factory/issues/7), bounded SHA-aware re-review. The observer no longer searches for arbitrary tasks or rejects this pair because it touches dispatch/review implementation.

## Frozen evidence

- [Named-pair protocol amendment](PROTOCOL-NAMED-PAIR.md) supersedes generic discovery and claim-time baseline capture.
- [Original response files](live/baseline/raw/) and [baseline manifest](live/baseline/summary.json) froze #5/#6/#7 issue bodies, comments, identities, status, PR-history reads and main at **2026-09-11T00:04:28Z**.
- Baseline SHA-256: `8fe6233a79304c6942ca3318b9764a5163a53a98dbffce82b0db1ba75cafaec8`.
- [Enrollment](live/enrollment.json) opened **2026-09-11T00:07:01Z**, byte **57,419,488**, after checking the ledger for prior claims of either ticket. No such claim existed. `git ls-remote origin refs/heads/agent/6 refs/heads/agent/7` returned no branches; both PR-history GETs returned empty arrays.
- #5 was open/ready-for-human; #6 and #7 open/ready-for-agent. The real dependency lines #6 blocked by #5, #7 blocked by #6 were verified. No dependency was bypassed.

## Prospective outcome

- The observer selected only nominated producer #6 from its new canonical claim at **2026-09-11T03:29:57Z**. Eligibility was fixed at **03:29:59Z** without inspecting the handoff. The frozen #5/#6/#7 baseline remained the common plan/status evidence.
- Claim-time main was `d2af513f225dc6744186ed60cd51dad5ba051cbd`. Attempt 1 ended at **03:33:21Z** with worker process exit `0`, deterministic gate `PASS`, 201 seconds, clean head `d2af513f225dc6744186ed60cd51dad5ba051cbd`, and no source or test changes. The gate report separately records conflict-markers, test and leak-scan PASS. This proves only that unchanged source passed the gate.
- The full original handoff was preserved outside the worktree and corroborated after worker completion: **1,382 bytes**, SHA-256 `d6a58c854eaec142b075c3917da491f04162eb5723e9784dc18f16f7cac002de`, at that exact head. Its immutable object and observation records are under [`live/candidates/6-32c66136a4e7/handoff/`](live/candidates/6-32c66136a4e7/handoff/).
- The worker reported no implementation or commits. It said the ticket's requested `gh pr review` path could not explicitly bind a submitted review to the discovered commit and requested approval to use a REST-backed `gh api` publication path instead. This is a worker claim retained verbatim, not an accepted contract change or independently adjudicated source fact.
- Factory then recorded escalation at **03:33:23Z**: `agent/6: PR not published (no commits over main or existing PR target mismatch); inspect dispatcher log`. This message names alternative conditions and does not establish which one caused the failure; the pilot does not diagnose or fix it. [`outcome.json`](live/candidates/6-32c66136a4e7/outcome.json) records the terminal state at **03:33:24Z**. Its convenience field `worker_exit_is_success: false` means the selected delivery was not successful; the canonical attempt row separately and unambiguously records literal worker exit `0` and gate PASS.
- No PR was published for this attempt, no independent review binding or `approved` transition occurred, no accepted-head implementation source was frozen, and consumer #7 was not used as a trial target. The observer exited after `SELECTED_ESCALATED`, as predeclared.

## Checks exercised

`python -B experiments/handoff-prospective/observer.py --self-test` passes a disposable real-Git lifecycle scenario: dispatcher-touching #6 accepted without Consumer-marker wording; unrelated claims ignored; pre-start baseline and contract-drift refusal; partial-line deferral; accepted exact-head source/handoff capture; same-tick worker events; delayed crash replay; vanished-worktree handling; resume integrity. A live initial startup exposed that lifecycle rows alone cannot establish admission: the ledger contains nested gate-test worker/review activity using fixture ticket #7. The admission check now uses canonical `claimed` records, and the smoke includes an unclaimed #7 lifecycle row. The failed startup made no model calls or production changes.

Main independently verified every frozen baseline response hash and every saved handoff-object hash, then parsed all **405** case events. That review confirmed the canonical claim at 03:29:57Z, attempt 1 exit `0` / gate PASS at head `d2af513f225dc6744186ed60cd51dad5ba051cbd`, escalation at 03:33:23Z, and no case-local review or `approved` record. The actual supervised observer reached readiness after the correction and exited on the declared terminal condition. It used `restart=no`: unexpected failures remained visible rather than repeatedly recapturing evidence.

## Result and interpretation

The success-only six-call comparison did **not run**: baseline `0/2`, handoff `0/2`, source control `0/2`; total model calls `0/6`. The protocol forbids retrying #6, replacing it with a favorable case, or treating worker exit/gate as acceptance. There are therefore no model recommendations, unique findings, condition differences, adjudicated planning refinements, usage/cost records, or owner adoption decisions.

The independently reviewed conclusion is: **prospective capture worked; the scientific comparison is inconclusive because independent acceptance was absent.** The run proves that the passive observer selected the frozen named pair without historical leakage, retained the complete terminal handoff before cleanup, and reported a failed/escalated delivery without relabeling it success. It does **not** estimate handoff planning value, show the source-control distinction, validate the worker's proposed command change, prove #7 should change, or establish saved rework/time. The result is a transparent failed case, not a null comparison.

No production response is authorized by the experiment. #6's contract/ownership and #5/#7 dependencies remain for their existing owners. The experiment does not retry, relabel, comment, dispatch, merge, release a hold, edit a ticket, install, deploy, or automatically apply the handoff's suggestion.

The [previous generic enrollment](enrollments/20260910T215843Z-generic-superseded/) retains its original report, rejected #79 capture and evidence. Those are not results for this named-pair run.

## Owner review form — intentionally unanswered

- Accept / reject the recorded escalation disposition: ___
- Is the worker's command-binding claim adequately corroborated? yes / no — missing evidence: ___
- Keep #6 contract / amend it / implement by another authorized path: ___
- Preserve #5 and #7 dependency/ownership boundaries? yes / no — detail: ___
- Use / defer / discard the captured handoff for future human planning: ___
- Later observed rework or delivered-outcome effect, if any: ___

No response is simulated, and the pilot claims no avoided rework or time savings.
