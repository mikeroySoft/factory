## Findings

### 1. Snapshot never supplies the repository native ID it queries; a failed initial head read then discards every independent item — `factory/dashboard.py:812`, `factory/dashboard.py:886`, `factory/feedback.py:176-178`

- `dashboard.py:812` introduces `repo: dict = {}` and `dashboard.py:886` passes `repository={"id": repo.get("id"), ...}`, but nothing in the diff assigns `repo` from `data["repository"]`. The newly added `id` field in `GRAPHQL` (`dashboard.py:124`) is therefore dead; the collector always receives `repository.id = None` from the full snapshot.
- `feedback.py:176-178`: `item()` drops any record when `repository['id']` is falsy. The only remaining source of the repository ID is `head()` (`feedback.py:145`). If the initial `HEAD_QUERY` fails or is malformed (timeout, `malformed_response`, `identity_conflict`), reviews/threads/checks reads still succeed but every item is rejected with `identity_missing`, even though the snapshot already observed the native ID.
- Spec violated: "One failed source must not discard usable independent sources" and Touches: "pass native PR/repository/issue/provenance ... into the shared collector". `tests/test_feedback.py` only covers *final*-head failure (`test_final_head_failure_retains_details_without_promoting_them`), so this path is untested. `tests/test_factory.py:2422` returns `REPO["id"]` in the mocked GraphQL payload, which is never consumed.
- Fix: set `repo = data["repository"]` after `data = github()` (line 814) and add an initial-head-failure case asserting items survive.

### 2. Producer revision shells out to `git` inside the installed package directory on every full snapshot — `factory/dashboard.py:875-878`

`sh(["git", "rev-parse", "HEAD"], cwd=source_root)` runs whenever `raw_prs` is non-empty. On a `uv tool install` / `pipx` deployment, `source_root` is a site-packages directory with no `.git`, so `git rev-parse` exits 128. `[INFERENCE]` `sh` is not in the diff; if it raises on nonzero exit, `snapshot()` fails for every installed user, breaking "existing full-snapshot consumers still work". Even if it returns stderr text, `sha()` nulls it, which is correct per spec. Wrap in `try/except` (or check `(source_root / ".git")` first) so a non-checkout install yields `revision=None` without touching snapshot success.

### 3. Non-blocking

- `factory/dashboard.py:214-215`: the main-snapshot `github()` failure now raises `"GitHub read failed"` instead of `proc.stderr`; `errors[]` in the existing full snapshot loses its diagnostic text. Spec's sanitization requirement targets feedback `errors[]`; applying it to the pre-existing transport changes an existing consumer-visible string. Acceptable but note it in CHANGELOG or scope it to feedback calls.
- `factory/dashboard.html:1257`, `1260`, `1262`: gate/CI fact wording rewritten to "Legacy local gate ..." — not requested by #79 (gate text is explicitly preserved, not reinterpreted). Scope creep, not a criterion failure.

Everything else checked against schema 1 (envelope keys, `REVISION_FIELDS` at `feedback.py:24-26`, `observation_id` inputs at `feedback.py` tail, bounds at `feedback.py:18-22`, head race → `unknown`, outdated-thread → `historical`, `--runtime-json` untouched, `pr.checks/comments/verdicts/review_decision/gate_text` preserved per `tests/test_factory.py:2441-2445`, body rendered via `pre()` at `dashboard.html:1083`, `sources_for` appends feedback after human constraints with omission notices at `briefing.py:364-372`) is consistent with the issue text.

VERDICT: REVISE
