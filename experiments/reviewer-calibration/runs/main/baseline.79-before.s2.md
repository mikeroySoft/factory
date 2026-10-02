# Review: #79 (B2) — `7bd5805` vs `8fba9c5`

## Standards (README only; no AGENTS.md/CONTRIBUTING.md at this head)

No convention violations found. New README section documents schema location, bounds, unknown/partial behavior, read-only authority as required.

## Spec findings

### F1 — `github()` transport semantics changed for the existing full snapshot (REVISE)

`factory/dashboard.py:220-221`: `if value.get("errors"): raise RuntimeError("GitHub query was incomplete")`. The pre-diff transport returned `json.loads(proc.stdout)["data"]` unconditionally. GitHub GraphQL routinely returns partial `data` alongside `errors` (e.g. inaccessible upstream node, per-field permission errors). After this change, any such response discards the entire issues/PR collection for the full snapshot and every ticket vanishes behind `errors: ["github: GitHub query was incomplete"]`. Issue scope: "Preserve … current snapshot fields for their existing consumers" and "Reuse the existing full-snapshot GitHub transport" — reuse, not resemanticize. `[INFERENCE]` on frequency in production; the behavioral change itself is in the diff.

`factory/dashboard.py:216`: `raise RuntimeError("GitHub read failed")` replaces the previous `proc.stderr.strip()` diagnostic. Sanitization is required for the *feedback* `errors[]` (collector already uses fixed messages at `factory/feedback.py` `gap()` sites), not for the existing snapshot `errors` list, which loses actionable operator diagnostics (auth, rate limit, scope). Fix: keep the legacy default-query path unchanged; apply sanitization/`errors`-check only on the `endpoint`/`query` branches the collector uses.

### F2 — Native repository ID never threaded from the snapshot query (non-blocking)

`factory/dashboard.py:124` adds `repository{ id … }` to `GRAPHQL`, and `factory/dashboard.py:812` declares `repo: dict = {}`, but nothing assigns `repo = data["repository"]`. `factory/dashboard.py:886` therefore always passes `repository={"id": None, …}`. The collector recovers the ID from `HEAD_QUERY` (`factory/feedback.py` `head()`), so the schema output is correct, but the query field and the variable are dead. Either wire `repo = data["repository"]` (enables the `identity_conflict` cross-check at `factory/feedback.py` `head()`) or drop both.

### F3 — Unconditional `git rev-parse` on the installed package per full snapshot (risk, non-blocking)

`factory/dashboard.py:875-878`: `sh(["git","rev-parse","HEAD"], cwd=Path(__file__).parent)` and `git status --porcelain` run on every full snapshot when any PR exists. For `uv tool install` deployments (README "Install") the package dir is not a git checkout. `sh` is not in the diff; `[INFERENCE]` whether it raises or returns `""` on nonzero exit. If it raises, the whole `snapshot()` fails outside the `try` at `factory/dashboard.py:852`. Wrap in the same `except` discipline as the provenance read and yield `revision=None`.

### F4 — Exit gate 4 regression boundary for `--runtime-json` not in diff (non-blocking)

Exit gate 4 requires an assertion that `factory dashboard --runtime-json` performs no feedback/GitHub read. `tests/test_factory.py:2397-2447` (`FeedbackSnapshotTest`) covers legacy fields, non-PR ticket (#81), and partial provenance, but no runtime-json boundary. Existing F03 tests may already assert no `gh` invocation (not visible in packet) — if so, state that in handoff; otherwise add it.

## Verified against spec (no finding)

- Schema-1 envelope, required item fields, explicit nulls, enums: `factory/feedback.py` `collect()`/`item()`.
- `source_revision` over exactly `REVISION_FIELDS`; `observation_id` excludes `observed_at`/`coverage.*.observed_at`/URLs/order: `factory/feedback.py` `REVISION_FIELDS`, final `digest({...})`.
- Bounds `ITEM_LIMIT/PAGE_LIMIT/BODY_LIMIT/ERROR_LIMIT/DETAIL_TIMEOUT` as fixed constants; checks source splits its two pages between check-runs and statuses (documented in README).
- Head race → all sources `partial`, both SHAs in reason, relevance `unknown`; final head unavailable → null head, no promotion.
- `thread_outdated=true` → `historical` regardless of SHA equality; missing source SHA → `unknown` + `source_head_missing`.
- Factory review requires lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, bracketing enter/exit; provenance-free `VERDICT` rejected (`tests/test_feedback.py` `test_factory_provenance_is_not_rendered_verdict_text`).
- Ownership requires closing-issue link + `claimed` event; branch/author never used.
- Briefing: feedback appended after human decisions; omission/shortening reported; unsupported/unknown schema not interpreted (`tests/test_briefing.py` three new tests).
- HTML: body rendered via existing `pre()`; no new action control; runtime-json branch untouched.

## Verdict rationale

F1 changes a preserved existing path's failure semantics and diagnostic content; the issue explicitly requires preserving the full-snapshot contract for existing consumers. F2–F4 are fix-alongside.

VERDICT: REVISE
