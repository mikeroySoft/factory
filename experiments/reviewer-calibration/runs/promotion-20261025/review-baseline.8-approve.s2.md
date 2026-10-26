## Scope checked

- Diff: `factory/dispatch.py`, `tests/test_factory.py` — both named in issue #8 *Touches*. Plan not recorded → no plan/file drift computable; no planned proof to compare. Gate `test: PASS` covers the exit-gate command (`python -m unittest`).
- Standards sources: no `AGENTS.md`/`CONTRIBUTING.md` in snapshot; README §Configuration (review lane, L329–349), `record()` docstring (`factory/dispatch.py:401-407`), issue template conventions.

## Spec (issue #8 exit gate)

| Criterion | Evidence | Status |
|---|---|---|
| Missing / unparsable / pending / failed / cancelled checks not ready | `review.diff:53-57` (`valid` requires non-empty list of known buckets), `:66-71` (`ready` needs `confirmed and valid and returncode==0 and all pass`); tests `:114-127` | met |
| Approval + passing required checks for same head → ready | `review.diff:58-60` review matched on `head`; `:68-71`; test `:127` | met |
| New head SHA clears readiness | `review.diff:48-50` fresh head replaces `head`, `confirmed=False`; review lookup then misses → `review_pending`; tests `:134-141` | met |
| Distinct states ci_pending / ci_failed / changes_requested / ready | `review.diff:61-71` | met |
| Events via existing `record()` | `review.diff:72-73` | met |
| No `gh pr merge` for external PRs | no merge command in diff; mock `run` fails on unexpected commands `:109`, assertion `:148` | met |
| Fail-closed on head race | checks→view ordering: a push between the two calls yields `confirmed=False`; a push before both yields a head mismatch with the passed `head` → never `ready` | met |

Scope creep: none. Line `review.diff:15` (keep tracking `APPROVE`d PRs) is required so readiness is derived for approved heads; test `:95-97` (no label, no request) exercises it.

## Standards

No documented-rule violations. Smell baseline: nothing beyond judgement calls below.

## Required fixes

None.

## Optional suggestions

1. **Per-pass event churn** — `review.diff:23-24` records a `review-readiness` row for every admitted open PR on every pass (default `OnUnitActiveSec=10min`, `factory/onboard.py:301`), even when state is unchanged, and `:15` keeps approved PRs in the lane until closed. `events.jsonl` is read in full several times per PR per pass (`factory/dispatch.py:221,231`) and bounded tail consumers (`runtime_events.EVENT_LIMIT=512`, `briefing.EVENT_READ_CAP`) see this churn. One-line guard: skip `record` when the last `review-readiness` for `pr` already has the same `head`/`state`. Not blocking: issue asks for events through `record()` and the gate does not bound journal growth.
2. **README now stale** — `README.md:349` states the lane "does not … monitor CI". Diff adds CI monitoring and a new event type; README L337–349 and `CHANGELOG.md` *Unreleased* not updated. No documented rule mandates it (issue *Touches* omits docs), so advisory.
3. **Coupled parse failures** — `review.diff:45-52`: an unparsable `gh pr checks` stdout (e.g. gh's "no required checks" case with empty stdout) aborts the `try` before the fresh-head parse, so the recorded `head` is the stale list-time head. Outcome is still non-ready (fail-closed), and the next pass corrects it; parsing `fresh` first, or in a separate `try`, keeps the recorded head accurate.
4. **`skipping` maps to `ci_pending`** — `review.diff:55,64-71`: a skipped required check is terminal but reported as pending forever. Fail-closed per spec ("all reported required checks pass"), but a distinct label would avoid an operator reading it as transient.

VERDICT: APPROVE
