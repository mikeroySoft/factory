# Review: PR for #79 (B2 PR feedback) — `8fba9c5..7bd5805`

## Required fixes

### R1. `producer.revision` can report an unrelated repository's HEAD
`factory/dashboard.py:875-878`

```python
source_root = Path(__file__).resolve().parent
revision = sh(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
if sh(["git", "status", "--porcelain", "--", "."], cwd=source_root).strip():
    revision = None
```

`git rev-parse HEAD` walks up from the package directory to the nearest enclosing repository. Trigger: Factory installed into a venv that lives inside another git checkout (`uv sync` / `.venv/` in the operator's project; the issue's own exit gate 5 uses a disposable repo with "real Factory configuration"). `.venv` is gitignored, so `git status --porcelain -- .` is empty and `revision` becomes the *consuming project's* HEAD, not Factory's. Impact: a false provenance claim is emitted and hashed into every feedback object.

Issue #79, Normative schema 1, `producer`: "`revision` is a verifiable source/build revision when available, otherwise null." The current derivation does not verify that the located repository contains the Factory source (e.g. compare `git rev-parse --show-toplevel` against the directory holding `factory/__init__.py`, or null out when the toplevel does not contain the package).

`[INFERENCE]` Additionally, `sh()` is not in the diff; if it raises on nonzero exit, a non-git install (`uv tool install`, site-packages, no enclosing repo) makes `git rev-parse` exit 128 and `snapshot()` raises for every snapshot with a PR present — a full-dashboard regression for the install path README documents. Guard the same way. No test exercises a non-checkout `source_root` (`tests/test_factory.py:2397+` runs inside the source checkout).

### R2. `source_updated_at` is never populated for `review` items; provider update time is read from a key the REST review object does not have
`factory/feedback.py:258` — `updated=row.get('updated_at')` on rows from `GET /repos/{slug}/pulls/{n}/reviews`. The REST review object carries `submitted_at`, not `updated_at`; the field is always `None` in production. Consequences in the diff itself:

- `factory/feedback.py:191-192`: every truncated review body fires `change_detection_incomplete`, unconditionally, because `updated is None` is guaranteed for reviews.
- `source_revision` (`feedback.py:230`) excludes the real provider update time for reviews even though the provider supplies one (GraphQL `PullRequestReview.updatedAt`; REST `submitted_at` at minimum).

Issue #79, items contract: "`source_updated_at` … Use real provider IDs/links and actual provider UTC update time when supported." The fixture masks this: `tests/test_feedback.py:21-22` review rows carry no `updated_at`/`submitted_at`, so `test_body_count_pagination_timeout_and_malformed_bounds` (`tests/test_feedback.py:181-186`) asserts `change_detection_incomplete` on a scenario that is actually the always-on default. Either read `submitted_at` (and document that edits are not surfaced), or source reviews via GraphQL `updatedAt`. Same dead read at `feedback.py:327` for check runs (`updated_at` absent from check-run objects; `completed_at` exists) — README already documents null for check runs, so that half is optional, but the review half is a spec defect.

## Optional suggestions

- `factory/dashboard.py:812` `repo: dict = {}` is never assigned; `repo.get("id")` at `:886` is always `None`, so the new `repository{ id }` field in `GRAPHQL` (`dashboard.py:124`) is dead. The collector recovers the ID from `HEAD_QUERY`, so no behavioral bug, but the identity-conflict check at `feedback.py:140-143` is a no-op on first read. Drop `repo`/the query field, or wire `repo = data["repository"]`.
- `factory/dashboard.py:216` replaces `proc.stderr.strip()` with a fixed `"GitHub read failed"` for the pre-existing full-snapshot GraphQL path too; operators lose `gh` auth/rate-limit diagnostics in `errors[]`. Sanitization is only required for feedback `errors[]`; consider sanitizing in `feedback.request()` (already done at `feedback.py:125-128`) and leaving the legacy message intact.
- `factory/dashboard.py:220-221`: any `errors` key now raises for the main snapshot query, whereas before partial `data` was accepted. Behavior change on an existing path; likely harmless since `gh` already exits nonzero on GraphQL errors, but note it.
- `factory/feedback.py:373` imports private `lifecycle._lifecycle`. Issue asks to "reuse … accepted bounded lifecycle/event readers"; a public reader would avoid coupling to a private symbol.
- `factory/feedback.py:120-121`: a read that returned successfully but finished after `deadline` is discarded as a timeout. Conservative but lossy; the spec only requires stopping cleanly.
- `factory/feedback.py:237-243`: on `source_identity_conflict` the previous item is removed but `counts[source]` is not decremented. Off-by-one against `ITEM_LIMIT`; negligible.
- `factory/dashboard.py:883-892`: one sequential `collect()` per selected PR, each with up to ~8 `gh` subprocesses and a 30 s budget. With N open agent PRs the full snapshot can take N×30 s worst case; issue says "Preserve full-snapshot cadence." Not blocking (the design implies it), but worth a note in README.
- `factory/dashboard.html:1050` passes `open: expanded` (boolean false) and `dataset: {...}` to `h()`. `[INFERENCE]` whether the existing helper handles a false boolean attribute and a `dataset` object is not visible in the diff; a `setAttribute('open','false')` implementation would force-open every item in the Inbox. Browser smoke in exit gate 5 should have covered this; confirm.
- `factory/dashboard.html:1090` drops the `PASS`/`FAIL` chip classes for legacy verdicts (`class: 'chk'`). The relabel to "legacy" is in scope; removing the color distinction is an unrequested presentation change.
- `tests/test_briefing.py:38` fixture uses `"changes_requested"` (lowercase) and `:73` `"branch agent/7"` as ownership evidence; the producer emits uppercase enums and the spec says branch text never proves ownership. Fixture-only, but it misrepresents the contract to future readers.
- Exit gate 4 ("`--runtime-json` performs no feedback/GitHub read") has no new assertion. Structurally satisfied since the runtime branch is untouched, but a one-line regression test would pin it.

## Standards
No AGENTS.md/CONTRIBUTING.md at this head. README conventions (shared `github()` transport, bounded 2 MB tail reader, safe DOM helpers, read-only briefing) are followed. Only new source file is `factory/feedback.py` as the brief requires; no event bus/provider framework/cache introduced.

VERDICT: REVISE
