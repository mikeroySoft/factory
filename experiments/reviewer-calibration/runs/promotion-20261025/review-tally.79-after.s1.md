**Scope reviewed**: `review.diff`/`snapshot` were absent; reviewed `git diff origin/main(8fba9c5)...HEAD(e20ebcd)` (9 files, +1476/−37) against the live checkout.

**Standards**: no repo AGENTS.md/CONTRIBUTING.md; gate is `python -m unittest discover -s tests` (ci.yml:12) and tests are unittest — followed. `dependencies = []` preserved; stdlib only. `pyproject [tool.ruff] line-length = 100` is not enforced in CI and pre-existing files already exceed it, so style only.

**Spec**: schema-1 keys/enums/nullability, `source_revision` field set (`feedback.py:27-29`), `observation_id` inputs (`feedback.py:438-440`), fixed bounds (`feedback.py:18-22`), initial/final head reads with race → `unknown` (`feedback.py:369-380`), evidence-backed `factory_review` from lifecycle `result` events matching `dispatch.py:489-492` (returncode/parsed/head/actual_head/outcome), no-SHA-backfill, `not_collected` for non-open PRs, runtime-json untouched (`dashboard.py:1228-1254` never calls `snapshot()`), full-snapshot integration at `tickets[].pr.feedback`, briefing append-after-constraints with omission notices, DOM text-only rendering via existing `h()`/`pre()`/`safeSourceURL` (`dashboard.html:453-466, 938, 1228`). Exit-gate 1–4 behaviors have tests in `tests/test_feedback.py`, `tests/test_factory.py`, `tests/test_briefing.py`; runtime-json network-free regression already exists in `tests/test_runtime.py`.

**Plan drift**: no plan recorded. All changed files are named in the issue's *Touches* list. Gate report covers conflict-markers/test/leak-scan only; exit-gate 5 (disposable-repo CLI run + real-browser Review/Inbox observation, no-mutation record) is not evidenced in the gate report — the issue-comment handoff shows only a snapshot JSON excerpt. Not a code defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":403,"summary":"`ownership_unverified` marks coverage.pr `partial` even when the PR read itself was complete; ownership state already lives in owner.relation/evidence. Conflates read coverage with ownership verification (deterministic, conservative, but every non-Factory PR reports partial pr coverage)."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":419,"summary":"factory_review items share the `reviews` ITEM_LIMIT budget after provider reviews are counted, so a PR with 100 provider reviews silently drops Factory review provenance (only an item_limit gap remains)."},
{"severity":"nit","pass":"spec","path":"factory/briefing.py","line":407,"summary":"Unrequested semantic change: the pre-existing 'Evidence coverage' source was always `truncated: true`/'· truncated'; now conditional on `incomplete`. Existing consumers reading that flag see different output; issue scope for sources_for was append feedback + coverage notice."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":220,"summary":"New `if value.get(\"errors\"): raise` also applies to the main snapshot GraphQL query, so any partial GraphQL error now fails the whole snapshot instead of returning partial `data`. Mostly masked because `gh api graphql` already exits nonzero on errors."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":23,"summary":"Module uses single-quoted strings throughout; the rest of factory/*.py uses double quotes (231 vs ≤12 single quotes per module). Style only."},
{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":888,"summary":"Exit gate 5 proof (disposable repo `python -m factory dashboard --json`, live browser Review/Inbox observation, mutation-absence record) is not in the gate report; only a snapshot JSON excerpt appears in the issue-comment handoff."}
]}
```
VERDICT: APPROVE
