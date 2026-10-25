## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `pyproject.toml:36-37` sets ruff `line-length = 100` but CI (`.github/workflows/ci.yml:12`) runs only `unittest`, and `tests/test_factory.py` already has 136 lines >100, so the one new overlong line (`review.diff:117`) is not a deviation from enforced practice. Event emission goes through the existing `record()` (`review.diff:72-73`), matching the documented journal convention (`factory/dispatch.py:401-417`). No hard violations.

Judgement calls (smell baseline):
- **Mysterious Name / parameter rebinding** — `review.diff:49-50` overwrites the `head` parameter with the freshly fetched SHA; the name then means "whichever head we ended up with". Readable enough given the docstring; non-blocking.

## Spec (issue #8)

All exit-gate criteria are covered by the diff:
- Missing/unparsable/pending/failed/cancelled → not ready: `review.diff:53-57` (`valid` requires non-empty list with known buckets), `review.diff:66-71` (`ready` requires `confirmed and valid and returncode == 0 and all pass`). Tests: `review.diff:113-128` (`[]`, `null`, `not json`, `[{}]`, bad bucket type/value, pending, fail, cancel, skipping, pass with rc≠0).
- Distinct states `review_pending` / `changes_requested` / `ci_pending` / `ci_failed` / `ready`: `review.diff:61-71`.
- Approval + passing required checks for the same head → ready: `review.diff:58-60` binds the review to `head`; test `review.diff:127`.
- New head SHA clears readiness: `review.diff:47-50` refreshes head via `gh pr view`; a mismatch sets `confirmed=False` and re-binds `head`, so the prior approval no longer matches (`review.diff:58-60`). Tests `review.diff:134-141`.
- No `gh pr merge`: no merge invocation added; asserted at `review.diff:148`.
- Dry run writes nothing: `review.diff:23-24`, test `review.diff:145-147`.
- `--required` flag used: `review.diff:37-40`, asserted `review.diff:104`.

Scope widening in `review.diff:15` (tracking `APPROVE`d PRs after GitHub consumes the review request) is necessary for the brief — otherwise an approved PR without the label would never get CI tracked — not creep.

## Plan drift

Plan status: not recorded, so no plan-named file list to diff against. Files changed: `factory/dispatch.py`, `tests/test_factory.py` — both named in the issue's **Touches**; `.factory/events.jsonl` schema is reached through `record()` as specified. The issue's proof (`python -m unittest`) is covered by gate `test: PASS`. No drift exposing a defect.

## Required fixes

None.

## Optional suggestions

1. **README now contradicts behaviour.** `snapshot/README.md:349` states the review lane "does not … monitor CI". After `review.diff:35-73` it does. One-line README amendment (and a `CHANGELOG.md` Unreleased bullet, per existing practice at `CHANGELOG.md:3-6`) would keep the documented contract accurate. Not a documented rule, so non-blocking.
2. **Unbounded journal growth / API load.** `review.diff:23-24` + `review.diff:72-73` append a `review-readiness` row (including the full `checks` list) and issue two `gh` calls for every tracked PR on every pass, unchanged or not. With the 10-minute timer (`README.md:61`) and the widened tracking set (`review.diff:15`, every lane-approved open PR), that is ~144 rows/PR/day into a file that `lifecycle.read_events(EVENTS)` re-reads per PR per pass (`factory/dispatch.py:221`). Cheapest fix: skip `record` when the last `review-readiness` event for `pr` has identical `head`/`state`/`checks`.
3. **Combined `try` masks the head refresh.** `review.diff:45-52` parses `checks.stdout` before `fresh.stdout` in one block; unparsable check output skips the head comparison, so a moved head is recorded as the stale `head` with `ci_pending` instead of the new head with `review_pending`. Still not-ready (fail-closed), so non-blocking; two separate `try` blocks would make the recorded head accurate.

VERDICT: APPROVE
