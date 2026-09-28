# Prospective handoff pilot — READY/WAITING

Date: 2026-09-10. **No live case has enrolled and no planner model call has run. This is a verified waiting checkpoint, not a completed trial.**

## Current checkpoint

The corrected enrollment opened at `2026-09-10T21:58:43Z` on `.factory/events.jsonl` byte `54,795,967`, device `66306`, inode `3370000653`. The last complete pre-enrollment row had lifecycle event ID `a4980d7b-992d-4363-8fe1-7aabde8cb9cd` and line SHA-256 `4d9a6be393cef051220849f5413dd8f2bb6f9f4e36c669001a6d385177a5c0a9`. There was no partial boundary line.

The authoritative boundary record is [`live/enrollment.json`](live/enrollment.json). It binds both frozen protocol files with composite SHA-256 `ac71b0f4e2b4f7e30e0f54e8fe3538398af47132a6070b27128b2159ac08e3a7`:

- [`PROTOCOL.md`](PROTOCOL.md), SHA-256 `2614bab47a3cbd2be4442cf42e8fe6a2a75934d068e0e6457030b88b8303e949`;
- [`PROTOCOL-ADDENDUM-20260910.md`](PROTOCOL-ADDENDUM-20260910.md), SHA-256 `f0c8eae97357206342627d4fef8adc775cfeaac8a04b7f22823301a62ce2c39d`.

The supervised read-only process is named `handoff-prospective-observer`; it runs:

```text
python experiments/handoff-prospective/observer.py --interval 5
```

`hub describe/logs handoff-prospective-observer` shows process supervision. [`live/status.json`](live/status.json) is the durable phase record and currently says `READY_WAITING`, `model_calls: 0`. The process polls without a production lock, writes only beneath this experiment, and performs GitHub GETs only after a new claim needs a baseline. It is session-scoped (`persist=false`): the delegated agent and Main supervise it while this OMP project session remains alive; it is not claimed to survive session/broker shutdown.

## Frozen selection and evidence behavior

The first qualifying case must have a complete classic `claimed` row after the boundary, must never have been claimed before the boundary, and must already carry authorized ordinary `ready-for-agent` intake. The known #52/#53/#54/#56/#57 case, this experiment, and control-plane/authority-policy work are excluded. #6 remains untouched and blocked by #5 unless its existing owners independently change that state.

A candidate must explicitly reference a plan/initiative/consumer/downstream issue. Its context ticket status is only an **eligibility proxy**, not proof of approved plan context. If a case reaches accepted capture, Main must qualify ordinary-work and approved downstream-consumer context from the frozen baseline only, before inspecting handoff utility or running trials. A rejection ends this one-case enrollment rather than selecting a more favorable result.

After observing the claim, the observer preserves bounded original GitHub response bytes and provenance for the candidate, pre-claim comments, up to ten explicitly referenced context issues/comments, and the default-branch commit. A baseline ending after a worker outcome is marked missed. This is a **pre-outcome** baseline; capture is not guaranteed to finish before the worker starts. Fixed integer-derived endpoints prevent issue text from authorizing arbitrary reads.

For the selected ticket, handoff bytes are stored once by content hash outside the worktree. Event-specific immutable observation records separately preserve attempt, head, observed time, identity and completeness. A stable snapshot remains unproven until identical bytes are observed after worker completion. Missing originals are failures; logs are not reconstructions.

An accepted capture requires one exact head bound across a modern `review` record, `approved` record, gate head, current worktree head and captured PR identity/head. A later worker event or review failure invalidates an earlier approval binding. On a valid acceptance the exact binary diff and changed Git blobs are frozen before cleanup. The observer then remains supervised at `READY_FOR_CONTEXT_REVIEW`; it does not spend model tokens or apply recommendations.

## Setup review and checks

Independent setup review found that the initial observer named metadata by second plus content hash. Identical handoff bytes observed under distinct worker events in the same second could collide. The process was stopped before any candidate, and that empty enrollment remains under [`enrollments/20260910T215058Z-superseded/`](enrollments/20260910T215058Z-superseded/). The addendum and corrected enrollment were frozen afterward; historical records were not rewritten.

The corrected disposable check drives the event reader, not just a capture helper. In one synthetic tick it supplies same-byte worker `result`, `exit`, classic `attempt`, a head-bound review, and a head/gate/review/PR-bound `approved` event. It verifies one content object with separate observations, exact-head source freeze, exclusion of historical/known/control work, late-baseline rejection, partial-line deferral, context hold and restart recovery. It also repeats no-event polling to prove unchanged bytes do not collide.

```text
python experiments/handoff-prospective/observer.py --self-test
PASS observer checks: content-addressed capture; delayed crash replay; same-tick result/exit/attempt/approved; exact-head review/PR/source binding; vanished-worktree handling; prior/known/control exclusion; late baseline rejection; partial-line deferral; context hold; restart state
```

Full local check evidence: [`checks/observer-smoke.txt`](checks/observer-smoke.txt). These are observer checks with synthetic inputs, **not live pilot evidence**. No project build, lint, format or test suite ran.

The experiment-local [`omp-isolation.yml`](omp-isolation.yml) disables memory, Mnemopi auto-recall/retention, autolearn/auto-continue and advisor. `PI_CONFIG_FILES=<overlay> omp config get ... --json` confirmed all six effective values without a model call or persistent config edit. A future trial process will also pass `--config <overlay>`. Evidence: [`checks/omp-isolation.txt`](checks/omp-isolation.txt).

## Predeclared future trials

Only after a bound successful capture and Main's baseline-only context qualification will the pilot prepare the three already frozen conditions:

1. common approved pre-outcome plan/status baseline;
2. the same baseline plus complete corroborated worker handoff;
3. the same baseline plus deterministic capped exact-head source evidence, with no handoff text.

There will be two fresh no-tools sessions per condition, six calls maximum, concurrency two, identical planner instructions/model/settings, no selective retry, and infrastructure failure left failed/unknown. Later plan/status and acceptance metadata will not be privileged to one arm. Raw prompt, argv, output, stderr, exit, duration, hashes and actual available usage will be retained. No runner was added while there is no enrolled case; this avoids unreviewed future infrastructure and model spending.

## What this checkpoint proves

- The enrollment boundary and selection rules existed before any admitted worker outcome.
- The corrected local observer can preserve identical handoff bytes under multiple event identities without collision and can freeze a head-bound synthetic source case.
- Historical/resumed, known-case, control-plane and too-late synthetic candidates are rejected by the implemented path.
- Memory/advisor/autolearn isolation values are effective for the future trial envelope.
- The current process is supervised and waiting with zero model calls.

## What it does not prove

There is no live selected task, worker handoff, gate result, independent acceptance, PR/head freeze, merge, installation, outcome delivery, model comparison, accepted planning refinement, owner adoption, avoided rework or saved time. Synthetic checks cannot establish any of those. A Factory reviewer acceptance, if later observed, is independent automated code acceptance only; it is not human owner acceptance or real-world delivery.

## Exact external prerequisite

Wait for the first naturally newly claimed task after `2026-09-10T21:58:43Z` that passes the frozen ordinary-intake and context-proxy rules and whose authoritative baseline finishes before its first worker outcome. Do not dispatch or alter work to create that case. If it succeeds, additionally wait for a complete corroborated handoff and exact-head bound review/approval/PR record, then ask Main to qualify the downstream plan/consumer from baseline evidence before any of the six calls. If the selected task escalates or capture integrity fails, record that outcome and stop; do not substitute another case.

## Owner review form — intentionally unanswered

- Candidate is ordinary implementation work: accept / reject — reason: ___
- Referenced item is an approved downstream plan/consumer: accept / reject — evidence: ___
- Each proposed plan delta: accept / reject — reason: ___
- Evidence adequate? yes / no — missing: ___
- Use now / defer / discard: ___
- Existing owner, hold and authority preserved? yes / no — detail: ___
- Later observed rework or outcome effect, if any: ___

No owner response is simulated. GitHub issues, PRs, labels, holds, services, timers, deployment and production code remain outside this experiment's authority.
