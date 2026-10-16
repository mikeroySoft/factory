# Review: #79 B2 source-versioned PR feedback

## Spec findings

**1. Review `source_updated_at` is always null — wrong REST field.**
`factory/feedback.py:258` reads `row.get('updated_at')` for `GET /pulls/{n}/reviews`. That endpoint returns `submitted_at`, not `updated_at` (GraphQL `PullRequestReview.updatedAt` is the actual update signal). Consequences: every review item has `source_updated_at: null` (visible in the handoff example), and any truncated review body unconditionally emits `change_detection_incomplete` (`factory/feedback.py:191-192`). Spec: "actual provider UTC update time when supported." README `README.md:735-736` only acknowledges nulls for check-run fields, so the doc also misstates this. Blocking.

**2. Exit-gate item 4 regression boundaries are missing from the diff.**
The only snapshot-level test is `tests/test_factory.py:2397-2449`. No test asserts that `factory dashboard --runtime-json` performs no `feedback.collect`/`github` call and keeps its schema, and no test covers a non-PR ticket gaining no `feedback` key. Issue text: "Add focused regression boundaries: … `--runtime-json` performs no feedback/GitHub read and its schema stays unchanged; non-PR tickets gain no fabricated feedback." The implementation plausibly satisfies both (`factory/dashboard.py:884-892` is inside `snapshot()` only), but the acceptance criterion is the test. Blocking.

**3. `collect` is unguarded inside `snapshot()`.**
`factory/dashboard.py:884-892` calls `feedback.collect(...)` outside the `try` that protects `github()` (`factory/dashboard.py:816-828`). Any uncaught exception (e.g. `repository['slug'].split('/',1)` at `factory/feedback.py:92`, or `sh(...)` at `factory/dashboard.py:874` on a non-git install) aborts the whole full snapshot that was previously resilient to GitHub failure. Spec: "One failed source must not discard usable independent sources." Non-blocking given the collector's internal catches, but it widens blast radius for existing consumers.

**4. `repo` is never populated; GraphQL `repository.id` is dead.**
`factory/dashboard.py:812` declares `repo: dict = {}` and nothing assigns `data["repository"]`, so `repo.get("id")` at `factory/dashboard.py:886` is always `None`; the `id` added at `factory/dashboard.py:124` is unused. Works only because `head()` backfills (`factory/feedback.py:145`). Non-blocking; remove or wire.

**5. Producer revision may come from an unrelated enclosing repository.**
`factory/dashboard.py:872-876` runs `git rev-parse HEAD` with `cwd=Path(__file__).parent` (the installed package dir). Under `uv tool`/`pipx` layouts, Git walks up and can resolve a user's enclosing checkout; the `git status --porcelain -- .` guard only nulls it if that tree reports changes there. Spec: `revision` must be a "verifiable source/build revision". Non-blocking; `[INFERENCE]` on `sh` failure behaviour.

## Convention findings

**6. Shared transport discards existing diagnostics.**
`factory/dashboard.py:215-216` replaces `proc.stderr` with the constant `"GitHub read failed"` for *all* callers, so the full snapshot's `errors` (`factory/dashboard.py:829`) loses auth/not-found causes operators previously saw. Sanitization is already done in the collector (`factory/feedback.py:126-128` use fixed messages regardless of exception text), so this change is unnecessary for #79 and degrades an existing consumer. Non-blocking.

**7. Local gate report relabeled "legacy".**
`factory/dashboard.html:1257` renders the gate as "Legacy local gate failures/context". README documents the gate as "the evidence of record" and a merge prerequisite; nothing in #79 reclassifies it. Non-blocking, but contradicts documented semantics.

**8. Unverifiable JS helpers.** `factory/dashboard.html:1009`, `:1083` depend on `safeSourceURL` and on `h()` tolerating `null` children (`:1082`), neither present in the diff and not exercised by the Python gate. `[INFERENCE]` — confirm in the browser smoke (exit-gate item 5).

VERDICT: REVISE
