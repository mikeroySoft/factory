# Review: PR for #79 (head `7bd5805` vs `8fba9c5`)

Gate (conflict-markers/test/leak-scan) PASS on target host; not re-evaluated here.

## Required fixes

### R1. Repository native ID is never populated in `snapshot()`; an initial head-read failure then discards every independently read review/thread item
- `factory/dashboard.py:812` declares `repo: dict = {}` and nothing ever assigns it from `data["repository"]`, so `factory/dashboard.py:886` always passes `repository={"id": None, ...}`. The `id` added to the main query at `factory/dashboard.py:124` is dead.
- `factory/feedback.py:176-178`: `item()` drops any record with `identity_missing` when `repository['id']` is None. The ID is only filled at `factory/feedback.py:145` when `head()` succeeds. Reviews/threads are collected (`feedback.py:246-306`) *before* the final head read (`feedback.py:339`).
- Trigger: initial `HEAD_QUERY` fails (timeout, rate limit, transient `gh` error) while REST reviews and GraphQL threads succeed. Impact: all retained facts are thrown away as `identity_missing` even though the full-snapshot query already had the repository ID in hand. Violates issue §Scope "One failed source must not discard usable independent sources" / §Bounded observation "A failed or partial reviews read cannot erase a successful checks read, and the converse also holds". `tests/test_feedback.py:243-252` only exercises a *final* head failure (repository ID already filled), so this path is untested.
- Fix: assign `repo = data["repository"]` inside the `try` at `dashboard.py:816` and pass its `id`.

### R2. Ad hoc journal parser treats the documented NUL-invalidated tail as incomplete provenance, permanently marking `reviews` coverage partial
- `factory/dashboard.py:856-869` re-implements event parsing: any line that fails `json.loads` sets `provenance_complete = False`.
- README "Execution event contract" (at this head): "Readers accept only newline-terminated JSON objects and skip malformed JSON … On the next append an unterminated tail is invalidated with a NUL byte and newline before the new record." That invalidated row is a normal, contract-conformant artifact, not lost coverage.
- Impact: once any such row exists in the 2 MB tail, every PR's feedback gets `provenance_partial` (`feedback.py:367-368`), `coverage.reviews.status="partial"`, `truncated=true`, and a different `observation_id`, for every snapshot until the row ages out. Issue §Scope: "Reuse the existing full-snapshot GitHub transport and accepted bounded lifecycle/event readers for provenance"; the diff adds a second, divergent reader instead. Fix: skip malformed/non-object rows as the existing reader does (only the clipped first line / unterminated last line should degrade completeness), or reuse `lifecycle.read_events` semantics over the bounded text.

### R3. `producer.revision` computation runs unguarded `git` in the package directory on every full snapshot
- `factory/dashboard.py:875-878`: `sh(["git","rev-parse","HEAD"], cwd=<installed factory/ dir>)`. README "Install" documents `uv tool install git+…`, where the package directory is not a Git checkout; `git rev-parse HEAD` exits 128 there.
- `[INFERENCE]` `sh` is not in the diff; if it raises on nonzero exit (typical `check=True` helper), `snapshot()` raises outside the `except` at `dashboard.py:828`, breaking `--json`/`/api/snapshot` for every installed (non-source) user. If it returns stderr/empty text, `sha()` yields `None` and this is benign. Either confirm `sh` swallows failure or wrap with try/except → `None`. Issue §Normative schema: "`revision` is a verifiable source/build revision when available, otherwise null".

## Optional suggestions

- `factory/dashboard.py:216`: `RuntimeError("GitHub read failed")` replaces the previous `proc.stderr` text for the **existing** main snapshot query. `feedback.request()` (`feedback.py:125-128`) already substitutes constant messages, so sanitizing here only degrades operator diagnostics (`errors[]` now says "github: GitHub read failed" with no cause, e.g. auth). Keep stderr for the `query is None` path.
- Cost: each selected ticket with a PR now spawns up to 6 serial `gh` subprocesses with a 30 s group deadline (`feedback.py:91`) inside `snapshot()`, behind a 15 s HTTP cache. Issue asks to "Preserve full-snapshot cadence". Not blocking without measurements, but worth noting in the handoff.
- Exit gate 4 asks for a regression that `--runtime-json` performs no feedback/GitHub read; no such test is in the diff. Existing F03 tests may already cover it (unverifiable here); if not, add one.
- `factory/feedback.py:373` imports private `lifecycle._lifecycle`. Prefer a public validator if one exists.
- `factory/feedback.py:258`: REST reviews expose `submitted_at`, not `updated_at`, so `source_updated_at` is always null for reviews and any truncated review body triggers `change_detection_incomplete`. Null is defensible (submission time ≠ edit time) but document it alongside the check-run note in README.
- `factory/briefing.py:~320-333`: every feedback item source embeds the full `producer/observed_at/repository/pr/owner` JSON. This satisfies "retain containing identities" but multiplies bytes per item and rotates every item's `S<hash>` ID each poll (via `observed_at`). A slimmer identity (`repository.id`, `pr.id`, `owner.issue.number`, `observation_id`) would fit more items under `CONTEXT_CAP`.
- `factory/dashboard.html:994, 1035, 1050, 1082` pass `dataset: {…}` to `h()`. `[INFERENCE]` Verify the existing helper handles a `dataset` key (not visible in diff); otherwise it becomes a bogus attribute.
- `tests/test_factory.py:2400`: importing fixtures from `tests.test_feedback` couples the two suites; fine for now.

VERDICT: REVISE
