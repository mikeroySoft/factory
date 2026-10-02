# Review: #79 B2 source-versioned PR feedback (head `7bd5805`)

Standards axis: no AGENTS.md/CONTRIBUTING.md at this head; README is the only documented convention source. Spec axis: issue #79 body + triage brief.

## Required fixes

**R1. Exit gate 4 regression boundaries are incomplete in the diff.**
Issue #79, Exit gate 4 enumerates: "`factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged" and "no mutation command, event append, model call, or new durable state occurs." The diff adds:
- `tests/test_feedback.py:1-280` — collector contract only.
- `tests/test_factory.py:2397-2448` — `FeedbackSnapshotTest` exercises the full snapshot only; `github` is mocked by `side_effect=read` (`tests/test_factory.py:2422`) so a GitHub mutation would be swallowed as a "read", and nothing asserts `.factory/events.jsonl` is unchanged after `snapshot()`.
- `tests/test_briefing.py:102-105` — asserts no model/`subprocess.run` for `sources_for` only.
No test in the diff touches `--runtime-json`. The brief ("confirming no mutation/model/GitHub-write calls and no change to runtime-json output") makes these explicit acceptance items. Fix: add a runtime-json assertion (patch `feedback.collect` and `dashboard.github` with `AssertionError` side effects and compare the schema key set to the existing F03 contract), and in `FeedbackSnapshotTest` assert `events.jsonl` bytes are identical before/after `snapshot()` and that no `gh` argv other than `api`/GET reads was invoked. If an existing test already proves the runtime path never invokes `gh`, cite it in the handoff instead of duplicating; the diff gives no such pointer.

**R2. Producer-revision probe can break the full snapshot on installed deployments.** `[INFERENCE]` — depends on `sh()` semantics not in the diff.
`factory/dashboard.py:875-878` runs `sh(["git","rev-parse","HEAD"], cwd=Path(__file__).resolve().parent)` and `git status --porcelain` whenever any PR exists. Trigger: README-documented install paths (`uv tool install git+…`, `pipx`) put `factory/` in site-packages with no `.git`; `git rev-parse` exits 128. These calls sit outside the `try` at `factory/dashboard.py:852-873` and outside the GitHub `try/except` at `:813-829`. If `sh` raises on nonzero exit, `snapshot()` (and therefore `--json`, `/api/snapshot`, Inbox) fails for every installed user — regressing the existing consumers the issue requires preserved ("Preserve full-snapshot cadence and existing ticket/PR fields"). Fix: wrap in `try/except (OSError, subprocess.SubprocessError)` → `revision = None`, which is also the issue's documented fallback ("otherwise null"). If `sh` is already non-raising, downgrade this to a no-op; the diff should still state that in the handoff since `producer.revision` is a handoff deliverable (Exit gate 7).

## Optional suggestions

- `factory/dashboard.py:812` declares `repo: dict = {}` and never assigns it; `repo.get("id")` at `:886` is always `None`, and the `repository{ id … }` added to `GRAPHQL` at `factory/dashboard.py:124` is unread. The collector recovers the ID via `HEAD_QUERY` (`factory/feedback.py:146`), so behavior is correct; the dead field/variable should go or be wired (`repo = data["repository"]`), saving the collector's identity-conflict check a guaranteed-`None` comparison.
- `factory/dashboard.py:216` replaces `proc.stderr` with the fixed string `"GitHub read failed"` for the *shared* transport. Feedback already sanitizes at `factory/feedback.py:125-128`, so the main-snapshot `errors[]` (`:829`) and the briefing "Snapshot collection errors" source lose auth/not-found diagnostics operators previously saw. Suggest sanitizing only on the feedback-supplied `endpoint`/`query` branch, keeping the legacy no-arg path's stderr.
- Per-PR cost: each selected PR with a PR now adds up to 8 `gh` subprocesses and a 30 s budget inside the `for issue in issues` loop (`factory/dashboard.py:883-891`). Bounded per the spec, but the aggregate is N×30 s worst case on the 15 s-cached `/api/snapshot`. Worth a sentence in README's feedback section so operators understand cadence impact.
- `factory/feedback.py:260` passes `updated=row.get('updated_at')` for REST reviews; that endpoint exposes `submitted_at`, not `updated_at`, so `source_updated_at` is always null for `review` items and any truncated review body always emits `change_detection_incomplete` (`:193-194`). Acceptable under the spec's "when supported", but document it next to the check-run null note in README.
- `factory/feedback.py:330` uses `row.get('creator')` for check runs; the check-runs API has `app`, not `creator`, so `check_run` author is always `{login:null, kind:"unknown"}`. `app.slug` would give evidence-backed `bot` attribution without new abstraction.
- `factory/dashboard.html:1257` relabels the deterministic gate report as "Legacy local gate context/failures". README §How a ticket moves step 4–6 describes the gate as the evidence of record for merge; "legacy" is misleading for an unchanged authority. Likewise `factory/dashboard.html:1082` drops the `PASS`/`FAIL` chip classes on verdict pills (visual regression, not a rule). Suggest neutral wording ("local gate report") and keeping the chip state classes.
- `factory/dashboard.html:1006-1009` calls `safeSourceURL`, which is not in the diff. If it is not an existing helper, `tabReview` throws on first render. Confirm in the browser smoke (Exit gate 5) and name the helper in the handoff.
- `factory/feedback.py:237-242` removes a conflicting item but leaves `counts[source]` incremented; harmless at ≤100 but trivially fixable with `counts[source] -= 1`.

No net-new abstractions beyond the brief: `factory/feedback.py` is the single module the issue mandates; `github()`'s `endpoint`/`query` parameters (`factory/dashboard.py:196-197`) are the "reuse the existing full-snapshot GitHub transport" requirement, not a second client.

VERDICT: REVISE
