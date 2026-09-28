# [C1] Expose shared read-only Factory evidence through a versioned JSON CLI

Local ticket draft; not published, numbered, assigned or dispatched. C1 is a planning ID. C0's read-interface review was closed by the operator on 2026-09-05; this is not approval of real actions or deployment. If publication is later authorized, use normal `needs-triage` intake after refreshing ownership.

Producer dependency satisfied: #28 / merged PR31, `6ec35460e4879b8278bc7feb680b7fbb5e84ce08`. C1 source implementation and verification are recorded in the [handoff delivery addendum](c1-read-interface-handoff.md); the ticket remains local and unpublished.

F03/#28 owns the bounded non-persisting runtime reader. Reuse its merged contract for runtime facts; do not create another lifecycle projection or call the writable observer and filter afterward. This is a source-contract dependency, not a requirement to deploy F03 onto the host. Coordinate shared dashboard/test edits with the active #10/PR24 and #12/PR22 owners before editing.

## Scope

One outcome: a supported Python-owned read interface that the dashboard and existing Pi harness can consume without duplicated collectors, model inference or operational mutation.

Add `factory evidence --root <explicit-main-checkout>` (source equivalent: `python -B -m factory.cli evidence --root ...`). It reads exactly one bounded JSON object from stdin and emits exactly one JSON result on stdout. Require `schema_version:1` and `repository:"owner/name"` in every request; resolve the operator-selected root and verify repository identity before collection. No inferred cwd scope or model-supplied host paths.

Preserve the exercised C0 read menu:

| `op` | Required operation fields |
| --- | --- |
| `observe` | none |
| `inspect` | positive integer `number` |
| `capabilities` | none |
| `investigate` | `kind:"workflows"`, no target fields |
| `investigate` | `kind:"file"`, `path`, `ref` |
| `investigate` | `kind:"pr"`, `"checks"` or `"runs"`, positive integer PR `number` |
| `investigate` | `kind:"run"` or `"log"`, positive integer `run_id` |

Reject unsupported versions, duplicate JSON keys, booleans as IDs, incompatible/extra fields, unsafe paths/revisions and cross-repository targets. No arbitrary HTTP URLs, GraphQL, shell commands or provider configuration in requests.

- `observe`: compact Factory-selected cases and grounded attention count, with coverage. Unknown collection is not zero attention. No automatic first-case bundle or inference. Preserve the accepted `escalated`/`needs-info` attention meaning unless existing authoritative state requires a documented equivalent.
- `inspect`: reuse Factory's existing source selection, recorded decisions and supported artifacts. Read the merged #11 escalation-packet contract; select actual producer paths rather than perpetuating the prototype's legacy mismatch. Preserve prioritization, caps and source provenance.
- Investigation: discover registered workflow paths, read regular UTF-8 repository files at an immutable resolved commit, inspect PR head/base and available diff patches, query checks/statuses and Actions runs for the exact observed PR head, and read run/latest-attempt jobs plus bounded log prefixes. A successful main run is not evidence that a PR head passed.
- A complete Git tree confirming absence returns cited negative evidence (`file_not_found`, commit and missing path/component). Incomplete trees, inaccessible sources, unsupported file types and genuine absence remain distinct. Refuse symlinks/submodules before any content dereference. Preserve uncertainty about omitted patches, list pages and logs.
- `capabilities`: expose implemented operations, field/target limits, producer/schema support and `actions:[]`; report unavailable operations without promising them.

Result contract: `schema_version`, `ok`, `scope:{repository,root}`, `observed_at`, fresh per-read `observation_id`, `coverage:{status,notices}`, and `sources`. Preserve C0's `cases`, nullable `attention_count`, `case`, `investigation` and `capabilities` where applicable. Sources retain `id,label,text,truncated,path?/url?`; content identity is not freshness or authority. Preserve actual source timestamps and commit/head identities; observation time must not refresh historical facts. Partial failures retain usable sources with `ok:false`; fatal failures emit a bounded machine-readable error, never a false empty success. Finalize structured per-source error codes, required/null fields and exit semantics in public docs and migrate affected callers together. Proposed exit codes: 0 for successful reads (including documented bounded coverage), 1 for collection failure/partial failure, 2 for invalid invocation/request. Do not infer success solely from JSON parseability or discard partial JSON results because exit is nonzero.

Keep collection truly read-only on the accepted producer baseline: no state/lock creation, event append/reconciliation persistence, ownership rewrite, dispatch, repair or authentication changes. Reuse F03's non-persisting runtime semantics; keep its `dashboard --runtime-json` path strictly network-free. C1's explicit project/CI reads may use authenticated GitHub GETs. Do not route F03 through this slower interface or through the full snapshot.

Bound work while reading, not only returned strings. Preserve at least C0's ceilings unless a stricter documented producer limit applies: 4096-byte request, 90-second collection, 20-second command, 1 MiB GitHub JSON, first 100 list entries, 20,000-byte cited source, at most five latest-attempt job-log prefixes (failed jobs first). Expose clipping/errors; preserve useful evidence around independent failures. ANSI logs must be safely captured and rendered; diagnostics must not expose credentials or raw host configuration. These are bounds, not a claim of comprehensive DLP or an OS sandbox.

Keep one implementation owner for shared collection. Extract only genuinely shared read logic into a Factory module; remove the C0 monkeypatch workaround rather than promote global subprocess/lock-helper replacement into production. Route the C0 bridge through that owner as a thin compatibility entry point, preserving its existing command and schema-1 consumer behavior. Preserve dashboard transport, display, caching and action behavior; migrate only affected read callsites. Keep the user's current harness usable throughout the cutover.

## Touches

- `factory/cli.py` command registration; one focused `factory/evidence.py` read owner if needed.
- Existing `factory/dashboard.py`, `factory/briefing.py` and accepted F03 read primitives where sharing requires it. Inspect definitions/references before changing exported contracts.
- `console/c0-prototype/evidence.py` as the thin existing entry point; `extension.ts` only if needed to preserve partial-result handling across the new exit semantics. No UX/model/resource redesign.
- Relevant behavioral tests under `tests/` (proposed focused file: `tests/test_evidence.py`), existing public CLI/schema documentation, and contract-dependent C0 checks.

Pointers describe likely ownership, not permission for adjacent refactoring. Python runtime remains dependency-free; normal Factory operation acquires no Node/Pi dependency.

## Exit gate

Use disposable repositories and controlled external-command stand-ins; no production tickets, provider inference or live state mutation is needed for deterministic verification.

1. Invoke the real new CLI via subprocess, not just imported helpers. Exercise every operation with real temporary file/event inputs and controlled GitHub responses. Parse the documented envelope and verify exact repository/revision association and exit semantics.
2. Reproduce the CI diagnosis boundary: a workflow exists on main but is absent at the PR head; there are no runs/checks on that head and a successful run on a different SHA. Preserve these distinct facts, without deciding that CI failed or changing the gate. Exercise workflow discovery, diff omissions and successful log capture.
3. Cover malformed/oversized requests, wrong scope, unsafe paths/revisions, symlinks, missing versus truncated trees, missing permissions/unavailable service responses, oversized JSON, truncated logs and hung reads. Prove bounded work/output and usable partial evidence. Tests defend behavior, not exact prose or subprocess argument ordering.
4. Compare disposable state before/after repeated reads, including an absent state directory and F03 interruption/partial-history evidence. No files, locks, events or ownership state are created/changed. Observation identity/time advances while unchanged source identities/times remain stable. F03's own no-network/read-only gate remains green.
5. Existing dashboard snapshots and selected evidence retain their documented contracts. Exercise both shared consumers; no duplicate collector or writable lifecycle observer hides behind a wrapper.
6. Run the preserved Pi launcher and inspect a source through the migrated bridge in Browse-only mode. Verify concise greeting/help, case/source reads, new investigation commands and refusal of unsupported mutation. This is a C1 compatibility smoke, not C2 packaging or another provider comparison.

Commands to run after implementation (the new `evidence` command and focused test file do not exist at drafting time):

```bash
python -B -m factory.cli evidence --help
printf '%s\n' '{"schema_version":1,"repository":"mikeroySoft/factory","op":"capabilities"}' | python -B -m factory.cli evidence --root /home/mike/dev/mikeroysoft/factory
python -m unittest discover -s tests -p test_evidence.py
python -m unittest discover -s tests
python3 -B console/c0-prototype/check.py
```

The full `unittest discover` command is the existing `.factory.toml`/CI gate; run it once after integration. The focused tests must create their own disposable state and run without live GitHub credentials. `check.py` is a separate existing live-read smoke requiring `gh` access. Report actual command results, representative success/partial/error JSON, bounds and state-preservation evidence; do not mark a scaffold or helper-only test as complete.

## Out of scope

C2 `factory chat` packaging; C3/C4 prepare/apply and real worker dispatch; manager execution/notes; B2 feedback production; new lifecycle/runtime projection; #8's external-PR readiness policy; automatic retries/CI reruns; workflow fixes; issue/PR/comment/label publication; merges; changes to gates, worker profiles, timers, installed packages or fleet configuration. No SDK host, renderer, daemon, HTTP/MCP service, database, generic API proxy or general-purpose model shell. Existing authority, review and deployment checkpoints remain in force.
