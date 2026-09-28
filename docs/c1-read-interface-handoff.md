# C0 closeout → C1 agent handoff

Prepared 2026-09-05. Repository: `/home/mike/dev/mikeroysoft/factory` (`mikeroySoft/factory`).

## C1 source delivery — 2026-09-06 UTC

F03/#28 is accepted through merged PR31, revision `6ec35460e4879b8278bc7feb680b7fbb5e84ce08`. C1 is implemented and verified locally on branch `c1-read-interface` in `/home/mike/dev/mikeroysoft/factory-c1`; no C1 commit, publication, push, merge, installation or deployment was performed. The original preparation sections below remain historical.

- Production owner: `factory/evidence.py`, registered by `factory/cli.py`; shared dashboard selection/stage policy and briefing source reader. F03 remains network-free. Accepted escalation packets use `escalations/<number>.md`.
- The original launch command and provider/session configuration remain unchanged. Original `console/c0-prototype/evidence.py` is now a symlink to `/home/mike/dev/mikeroysoft/factory-c1/console/c0-prototype/evidence.py`. Its resolved file location selects the new Python owner, never the older selected checkout's collector. Keep that worktree in place while using this harness. Only `extension.ts` partial-exit handling and contract-dependent `check.py` were also migrated.
- PR22 and PR24 remained open when ownership was refreshed; no live hub peers were available. C1 edits are isolated from their worktrees and avoid their config/metrics changes. Refresh and reconcile these overlaps before any separately authorized merge.
- `python -B -m factory.cli evidence --help`: passed. Real capabilities: exit 0, `actions:[]`. Invalid mutation request: exit 2, `invalid_request`. Immutable missing-file read: exit 1, cited `file_not_found` at the accepted F03 commit.
- Focused gate: `python -B -m unittest discover -s tests -p test_evidence.py` — 19 tests passed. Full gate, run once after integration: `python -B -m unittest discover -s tests` — 107 tests passed, including F03 and dashboard contracts.
- Controlled CLI checks cover all operation kinds, exact PR-head/main separation, unsafe/malformed/oversized inputs, missing versus incomplete trees, symlink/submodule refusal, failed sibling reads, ANSI/OSC log clipping, source identity/time and audited no-write behavior. Separate actual 90-second blocked-stdin and five-hung-log probes returned `collection_timeout`; the log probe retained run/jobs citations.
- `python3 -B console/c0-prototype/check.py` passed from the original checkout. The worktree equivalent uses `--root /home/mike/dev/mikeroysoft/factory`; the implementation worktree itself is deliberately not a valid evidence root.
- Actual Pi 0.84.4 launcher, Browse-only: concise unknown-attention greeting, `/fm help`, `/fm inspect 28`, `/fm sources`, exact source display, `/fm investigate checks 22` at head `8c25fb735bc1f73ea41f7ae4dcbf5c6ed8768cdc`, and unsupported `/fm dispatch 28` refusal. No provider disclosure approved, inference or mutation performed; terminal exited 0.
- Live local history remains partial: `unsupported_record`, `event_limit`, `missing_enter`, `lock_unavailable`. Usable cases/sources survive with exit 1; attention is null where unknown. This is not a request to repair operational state.
- All 12 pre-existing auth/session and unrelated-file checksums stayed unchanged; no new session file was created. The worktree tool unexpectedly copied ignored runtime/state during creation; destination-only copies were removed, and no credentials/sessions remain in the implementation worktree.

C2–C6 and every separate authority, deployment and fleet checkpoint remain unchanged.

## Decision and authority

The operator explicitly closed **C0's read-interface review** and requested a bounded C1 ticket and next-agent handoff, while retaining the current harness for continued use. The accepted direction is the revised concise greeting, proportionate conversational investigation, explicit scope/coverage and inspectable evidence. This does not qualify a model, accept real action boundaries, establish an OS sandbox or approve production/fleet deployment.

Deliverable: [C1 ticket draft](c1-read-interface-ticket.md). It is local and unpublished, with `Blocked by: #28`. No C1 implementation, issue publication, label change, dispatch or installation was performed during closeout. [The console plan](fm-console-plan.md) records current acceptance and preserves earlier experiment results as history.

## First: refresh ownership and source baseline

The read-only ownership snapshot completed at **2026-09-05T23:14:15Z**. GitHub state changes; refresh it before acting. The local prototype's legacy observer reported #28 `in-flight`; this is not a new F01 ownership claim or proof of a live process.

| Existing work | Observed state | C1 boundary |
| --- | --- | --- |
| [#28 / F03](https://github.com/mikeroySoft/factory/issues/28) | Open, operator-released, `ready-for-agent`; local case projection `in-flight` | Block C1 collection implementation on its accepted non-persisting runtime reader. Do not duplicate or take over its work. |
| [#27 / F02](https://github.com/mikeroySoft/factory/issues/27) | Closed; accepted PR30 merge `7d100734c4aae6b71e009aff43d512a479d2b0be` | Reuse accepted wait/resource/lifecycle semantics; no second scheduler or event model. |
| [#26 / F01](https://github.com/mikeroySoft/factory/issues/26) | Closed; accepted baseline recorded as `14496db16753e2f41b1ac38b750f2ca78fb904fd` | Its `lifecycle.observe()` can persist reconciliation and acquire locks. It is not a read-only API. |
| [#11 escalation packets](https://github.com/mikeroySoft/factory/issues/11) | Closed/completed; PR22's latest operator comment says PR23 landed | Consume accepted packet paths/selection; do not rebuild the producer or retain a legacy artifact-path mismatch. |
| [PR22 / #12 manager config](https://github.com/mikeroySoft/factory/pull/22) | Open, dirty; operator requested refresh/re-review after PR23 at 23:06Z | Coordinate dashboard/config/test edits. Previous approval was explicitly called stale. Do not repair this PR as part of C1. |
| [PR24 / #10 metrics](https://github.com/mikeroySoft/factory/pull/24) | Open, `factory-approved`; merge state reported unknown | Owns overlapping dashboard/stats/tests/docs changes. Coordinate; no takeover or merge action. |
| [#8 external-PR readiness](https://github.com/mikeroySoft/factory/issues/8) | Open; blocked by #7 | May consume overlapping raw check data. C1 does not derive its review/merge readiness policy. Not an additional C1 blocker. |

The inventory contained 21 open issues and two open PRs; no separate C1 evidence-API ticket was identified. Search again before any later authorized publication. Older ownership tables in the console plan are dated experiment evidence: **#28 is no longer held in that snapshot**. This handoff does not release District's separate installed-producer verification hold or other programmes.

### Preserve this working tree

At preparation, local HEAD is **`36cf1da27a503241e6648fc015687c4d4d1a3646`**, not the accepted F01/F02 source baseline. Installed `factory --version` reports **0.2.0**; that is not proof that a newer source contract is installed. Before this documentation closeout the working tree already contained:

```text
 M docs/manager-plan.md
?? UI-UX-ORCHESTRATION-HANDOFF.md
?? console/
?? docs/fm-console-plan.md
```

Those are existing work, not disposable cleanup. The two C1 documents are new closeout artifacts. Do not reset, clean, overwrite, auto-stash, rebase this dirty checkout or commit unrelated files. Establish a coordinated implementation branch/worktree from the accepted current source once #28 is ready, retaining the original working harness and documenting how its read owner resolves. A clean new worktree will not contain these untracked handoff/harness assets automatically. Transfer only the required source/docs deliberately; never copy `.runtime` credentials or transcripts into version control. Do not leave a nominally shared bridge silently importing the old implementation.

## What to build—and not build

Read the ticket's Scope / Touches / Exit gate / Out of scope sections first. It specifies a proposed `factory evidence --root ...` single-request JSON CLI, covering C0's observe, inspect, capabilities and bounded repository/CI investigation. **That public CLI does not yet exist.** C1 moves read ownership into Factory Python and leaves C0 as a thin consumer; it is not C2's `factory chat` packaging.

F03 owns `factory dashboard --runtime-json`, its schema and a network-free, bounded, non-persisting local runtime projection. C1 owns explicit project/CI evidence that can require authenticated GitHub GETs. Share accepted local read primitives; do not make F03 network-capable or invoke a writable full observation and then filter it. If #28 is still active, inspect/design against available contracts but stop before competing shared collection edits. Name the concrete unresolved producer, not an invented generic blocker.

The most important migration checks:

- Current C0 uses the **legacy checkout's collectors** and temporary Python monkeypatches for a pure lock lookup, disabled triage probe and bounded subprocess reads. That workaround is not production architecture and is not safe evidence that newer collectors cannot write. Trace current accepted callers before extraction.
- Preserve compact startup context, no automatic first-case/dispatcher bundle and on-demand resources. No model prompt redesign, SDK host or new renderer is necessary for C1.
- Keep scope, coverage, source identity and source freshness distinct. A new observation ID must not pretend a historical event or run is new.
- Current negative file evidence resolves immutable commit/tree entries before regular-file reads. Preserve symlink/submodule refusal and missing-versus-incomplete distinctions. Improve meaningful access/unavailability errors without exposing raw sensitive diagnostics.
- C0 parses useful partial results. A production nonzero exit must not cause the bridge/extension to discard valid partial JSON. Migrate callers together.
- Bound input, traversal/response reads, subprocess time, list pages and logs during collection. The existing log path deliberately permits capture of escape sequences and sanitizes them before evidence display; do not replace it with an unbounded capture or emit raw terminal control sequences.

Use existing repo conventions and language-server references for changed exported symbols. Keep the normal Python package dependency-free. Coordinate irreducibly shared mutations with active owners instead of inventing a second collector.

## Keep using the current harness

From the preserved checkout:

```bash
python3 -B console/c0-prototype/launch.py \
  --root /home/mike/dev/mikeroysoft/factory \
  --repository mikeroySoft/factory \
  --provider openai-codex --model gpt-5.5
```

Add `--continue` to resume the matching existing session; omit it for a fresh conversation. Omitting provider/model selects the existing local Ornith configuration. Pi is pinned to `@earendil-works/pi-coding-agent@0.84.4` by the prototype's package/lock files. Do not reinstall or switch providers merely for documentation or source inspection.

The user already completed the native OpenAI OAuth flow for this harness. Keep `.runtime/agent/auth.json` and local sessions untouched; no copied tokens, new login or external disclosure is implied by this handoff. `/fm help` discovers supported reads; `/fm brief` is opt-in. The current seven custom tools are `fm_observe`, `fm_inspect`, `fm_investigate`, `fm_capabilities`, `fm_source`, `fm_resource` and `fm_sample_preview`.

The sample proposal can be confirmed but reports `demo_confirmed_not_executed`, `executed:false` and no receipt. It never dispatches. Stock Pi still exposes operator built-ins such as authentication/settings/share commands: custom-tool isolation is **not an OS sandbox or closed operator surface**. Do not treat a chat “yes” as authorization for a real mutation that the current interface cannot perform.

## Verification baseline and completion evidence

Previously exercised, with details and local transcript pointers in the console plan:

- Actual terminal launch, bounded CI investigation, native source inspection and refresh/resume.
- Matched GPT-5.5/Ornith questions against the same PR head. Limited scenario evidence, not a benchmark or model qualification.
- Real run/job log evidence with ANSI sanitation, immutable file absence, unsafe requests and symlink refusal.
- `python3 -B console/c0-prototype/check.py` passed the recorded bounded-read/source/scope/negative-evidence checks. No state directory was created by absent-state reads.
- Seven custom tools, refused arbitrary file/shell requests and non-executing sample confirmation.

During this closeout, the read-only `observe` request succeeded with **partial coverage** at the timestamp above. No new provider comparison, full C0 check or production gate was run for the documentation-only change. The seven harness source/package files were hash-compared against their pre-closeout contents; see the delivery report for the comparison result.

C1 completion requires the ticket's real subprocess CLI scenarios, disposable state-preservation proof, source/partial-error/bounded-I/O checks, existing dashboard contract checks and preserved terminal harness smoke. Run the actual existing gate, `python -m unittest discover -s tests`, once after integration. Proposed focused test names/commands in the ticket are implementation targets, not claims that tests already exist. Keep default tests independent of GitHub credentials; separately report any live read smoke and unavailable services. Do not declare completion from imports, mocked field forwarding or a helper-only test.

## Copy-paste prompt for the next agent

```text
Work on C1 in /home/mike/dev/mikeroysoft/factory. Read docs/c1-read-interface-handoff.md and docs/c1-read-interface-ticket.md first; consult docs/fm-console-plan.md for the C0 acceptance and experiment evidence.

Implement only the bounded shared read-only Python evidence interface specified by that ticket, once #28/F03's non-persisting reader is accepted. First refresh #28, PR22 and PR24, and establish the current accepted source baseline without disturbing the older dirty checkout or untracked harness. If #28 remains unresolved, report the exact producer dependency and do not duplicate or take over its work.

Preserve the working C0 launcher, UI, provider/auth state and sessions. Reuse accepted Factory/F03 readers and migrate the C0 bridge as a thin consumer; no parallel collector, writable lifecycle observer or silent import of obsolete code. Keep F03 network-free. Verify the ticket's actual CLI scenarios, no-write/bounded-read guarantees, dashboard compatibility and continued terminal harness use, then run the existing Python gate.

No issue publication, label changes, worker dispatch, PR push/merge, provider/security changes, installation, timers, fleet rollout or C2–C6 work. Do not commit unrelated existing changes. Deliver code, exact verification results and remaining limitations; distinguish any blocked dependency from completed implementation.
```
