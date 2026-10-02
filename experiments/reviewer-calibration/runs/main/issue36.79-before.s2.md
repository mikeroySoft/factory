# Review: #79 B2 — source-versioned PR feedback

Scope reviewed: diff `8fba9c5..7bd5805` against issue #79 text + triage brief. Gate (conflict-markers/test/leak-scan) is PASS and not re-litigated. No AGENTS.md/CONTRIBUTING.md at this head; README is the only convention source.

## Required fixes

### R1. Repository native ID is never passed to the collector; a failed *initial* head read drops every item
- `factory/dashboard.py:811` declares `repo: dict = {}` but nothing assigns `data["repository"]` to it; `factory/dashboard.py:884` passes `repository={"id": repo.get("id"), ...}` → always `None`. The `id` added to the snapshot GraphQL (`factory/dashboard.py:124`) is therefore unused.
- `factory/feedback.py` `item()` (≈L170–173) refuses any item when `not repository['id']`, emitting `identity_missing`. `repository['id']` is only populated inside `head()` (≈L145). Trigger: the initial `HEAD_QUERY` fails or times out (transport error, rate limit) while reviews/threads/checks succeed. Impact: all independently-read items are discarded as `identity_missing`, violating issue §Normative schema `errors`: "One failed source must not discard usable independent sources" and §Scope: "When a PR is present but a feedback source cannot be read, return the schema-1 feedback object with … retained independent facts." `tests/test_feedback.py:238` only exercises a failed *final* head; the initial-fail path is untested.
- Fix: assign `repo = data["repository"]` in `snapshot()` next to the existing issue/PR extraction so the already-fetched native ID reaches `collect`. No new abstraction needed.

### R2. `producer.revision` can report an unrelated repository's HEAD
- `factory/dashboard.py:874–877`: `sh(["git","rev-parse","HEAD"], cwd=<package dir>)` + `git status --porcelain -- .`. Trigger: Factory installed into a venv/site-packages that lives *inside* another Git checkout (e.g. `uv sync` `.venv` in a consumer repo, gitignored). `rev-parse HEAD` resolves the enclosing repo's commit; `status --porcelain -- .` is empty for an ignored tree → a foreign SHA is emitted as a "clean" producer revision. Impact: violates issue §Normative schema `producer`: "`revision` is a verifiable source/build revision when available, otherwise null." Handoff item 7 and #15 would key on a fabricated revision.
- Also `[INFERENCE]`: behaviour of existing `sh()` on nonzero exit (plain `uv tool install` → not a git repo) is not visible in the diff; if it raises, the whole full snapshot fails for every installed deployment. Confirm.
- Fix: verify the toplevel actually contains the package (e.g. `git ls-files --error-unmatch feedback.py` with `cwd=source_root`, nonzero → `None`), and treat any git failure as `None`.

### R3. `[INFERENCE]` Factory-review provenance keys on `exit.outcome == "approved"`, which README does not attribute to review-stage exits
- `factory/feedback.py` (≈L370–372) requires an `exit` row with `outcome == 'approved'` (APPROVE) / `'product_feedback'` (REVISE) for `stage == 'review'`. README "Outcomes and uncertainty" (`README.md` at this head) says `product_feedback` is "a parsed successful reviewer requested REVISE" but lists `approved` as "the corresponding operation succeeded" (the approval/label operation, recorded separately from the review invocation per "How a ticket moves" step 5), with `APPROVE`/`REVISE` listed under *reasons*. If the real producer in `factory/dispatch.py` exits the review scope as `completed`/reason `APPROVE`, no production APPROVE review ever yields a `factory_review` item; only the hand-built fixture at `tests/test_feedback.py:270–279` matches. That would fail exit-gate 1 ("a provenance-backed Factory review") in production while passing tests.
- Action: check the review-stage `exit` writer in `dispatch.py`; if it emits `completed`, match on `kind=="result"` verdict + exit presence (any non-mechanism-failure outcome) instead. If it does emit `approved`, drop this finding.

## Optional suggestions (non-blocking)

- `factory/feedback.py` (≈L381): `ownership_unverified` is recorded via `gap('pr', …)`, which flips `coverage.pr.status` to `partial` for every non-Factory PR even when the PR read was complete. Coverage is defined as read completeness; ownership is already explicit in `owner.relation`. Consider recording this in `owner.evidence` only, so `partial` keeps its "incomplete read" meaning (and `observation_id` isn't perturbed by it).
- Snapshot cadence (issue §Existing read-surface integration: "Preserve full-snapshot cadence"): `collect` runs serially per selected PR at `factory/dashboard.py:881–890`, up to 8 `gh` subprocesses per PR with a 30 s group budget. With tens of open PRs the `/api/snapshot` wall time grows linearly; the 15 s cache will not hide it. No bound exists on the sum across PRs. Worth measuring in the smoke run and recording in the handoff.
- `factory/dashboard.py:216`: `RuntimeError("GitHub read failed")` replaces the previous stderr-bearing message for the *legacy* snapshot path too, so `errors: ["github: GitHub read failed"]` loses diagnostics for existing consumers. Feedback already sanitizes in `request()`; the legacy path could keep stderr.
- `factory/dashboard.html:993,1052–1057`: `feedbackEvidence` passes `null` children and a `dataset:` prop to `h(...)`. Whether `h` tolerates `null` (vs appending the text "null") and maps `dataset` isn't visible in the diff; the exit-gate 5 browser check should confirm no literal `null` text renders.
- Exit gate 4 names a `--runtime-json` no-feedback/no-GitHub regression check; none is added in the diff. If existing runtime tests already patch `subprocess`/`github`, cite them in the handoff; otherwise add one assertion.
- `factory/feedback.py:16,≈L364`: imports and calls private `lifecycle._lifecycle`. Not a documented rule, but a public reader (`read_events` is documented in README) would avoid coupling to a private name.
- Unused after R1 only if you keep it: `issues.nodes.id` is used; `repository.id` is used once R1 lands.

## Spec coverage summary

Satisfied in the diff: schema-1 envelope and item fields with explicit nulls; `source_revision` over exactly the listed fields; `observation_id` excludes timestamps/URLs/order; fixed constants; two-head reads with race handling; per-source independence (`tests/test_feedback.py:134–149`); legacy verdict rejection; `--runtime-json` untouched; `sources_for` appends feedback after human constraints with omission notices; Review drawer/Inbox render body via `pre()`; README/CHANGELOG documented.

VERDICT: REVISE
