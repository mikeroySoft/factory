## Standards

Sources: no `AGENTS.md`/`CONTRIBUTING.md` in repo; `README.md`, `CHANGELOG.md`, `docs/*.md`, `pyproject.toml` (`ruff line-length = 100`). No documented standard is violated.

**Optional**
- `CHANGELOG.md` has no entry for this change. Every `## Unreleased` item carries an issue ref (`CHANGELOG.md:5-13`); `docs/autonomy-execution-plan.md:693` names `CHANGELOG.md` as a per-PR documentation surface. Observed convention, not a written rule — not blocking.
- Possible Duplicated Code: `factory/dashboard.py:843-851` re-derives the readiness state that dispatch already computed and recorded on the same `review-readiness` row (`factory/dispatch.py:300-312`, `state=` field). Judgement call; the recorded `state` is always written after the matching `review-result` (`dispatch.py:269-271`), so `readiness.get("state")` could replace the elif ladder, keeping only the `ci` bucket fold.
- Dead condition: `factory/dashboard.html:1162` `pr.reason ||` — for `state === 'review_pending'`, `current` is `{}` and `escalation` is empty (else state would be `escalated`), so `reason` is always null (`dashboard.py:856`). Reduces to `pr.verdict === 'APPROVE'`, which is the real (and correct) stalled case: dispatch never re-reviews an approved PR at a new head (`dispatch.py:234-236`).

## Spec

Issue #9 scope/brief: six states ✓ (`dashboard.py:843-852`, `dashboard.html:1128`), repo-local number/title/author/head/last-reviewed SHA/verdict/CI state ✓ (`dashboard.py:853-857`, `dashboard.html:1134-1142`), GitHub link per row ✓ (`dashboard.html:1136`), stalled items in Inbox without mutation controls ✓ (`dashboard.html:1160-1165`, `1550-1551`), snapshot test over all six states with link + SHA checks ✓ (`tests/test_factory.py:2827-2838`), README lane description ✓ (`README.md:353-365`). Out-of-scope items untouched.

**Required**
1. Opted-in PRs silently drop out of the queue once the repository has >100 PRs. `review_queue` is fed `repo["pullRequests"]["nodes"]` (`factory/dashboard.py:875`), which is the pre-existing `pullRequests(first:100, CREATED_AT DESC)` across all states (`factory/dashboard.py:150`). Dispatch's intake uses `--state open --limit 1000` (`factory/dispatch.py:203-206`), so dispatch keeps reviewing/escalating a PR the dashboard no longer lists. Trigger: 100 newer PRs (merged/closed included) created after an open opted-in PR — the target repo is already past #84. Impact: the queue and the Inbox stalled-case list omit it with no partial/unknown signal, contradicting the diff's own claim "lists open, non-draft opted-in PRs" (`README.md:353`) and the issue scope "Show each opted-in PR". Minimal fix: a second aliased field in `GRAPHQL`, e.g. `reviewPrs: pullRequests(first:100, states:OPEN, orderBy:…){pageInfo{hasNextPage} nodes{number title url headRefOid isDraft author{login} labels(first:10){nodes{name}} reviewRequests(first:100){…}}}`, and append to `errors` when `hasNextPage` (same pattern as `errors.append(f"github: …")` at `dashboard.py:888`). No new abstraction.

**Optional**
- `labels(first:10)` (`dashboard.py:156`) can miss `needs-review` on a heavily-labelled PR; dispatch reads all labels. Same silent-omission class as above but far less likely; fold into the aliased query if adopted.
- `escalated` ignores head (`dashboard.py:838`): any prior `escalate` keeps a PR escalated after new pushes. Consistent with dispatch (`dispatch.py:237-238`), so not a defect; worth a word in README if operators expect it to clear.

## Changed surfaces → external consumers
- `GRAPHQL` (`dashboard.py:124,154-155`): only consumer is `github()` default path (`dashboard.py:206-214`); `feedback.collect` passes its own `query=` (`dashboard.py:947-948`) — unaffected. Tests mocking `github` without `viewer`/`author`/`reviewRequests` still hold: `data.get("viewer") or {}` (`dashboard.py:876`), `pr.get("author") or {}` (`:854`), `pr.get("reviewRequests", {}).get("nodes") or []` (`:826`).
- New snapshot key `review_queue` (`dashboard.py:1016`): consumers `dashboard.html:1145,1160`, both default `|| []`. No other snapshot consumer references it (grep of `briefing.py`, `cli.py`, `stats.py`, `console/`: none). Additive.
- `WAITING` += `external-review` (`dashboard.html:1148`): `inboxCount` (`:1191`) and waiting count (`:1534-1536`) include it ✓; informational branch (`:1557-1559`) filters `!WAITING.has` so `i.t.number` at `:1559` never sees a review item ✓; `ensureBriefing` guarded by `item?.t` (`:1560`) ✓; sort uses `b.t?.number` (`:1189`) ✓; `selectedItem` (`:1193`) returns review item and `renderInbox` branches on `item.review` before `item.t` (`:1550-1551`) ✓; hash regex `[\w-]+` accepts `review-<n>` (`:1691`) ✓. `INBOX_KINDS` lacks `external-review` but is only read in `renderTaskBriefing` (`:1515`) and the informational branch — unreachable for review items ✓.
- `h()` supports `style` objects and drops null kids (`dashboard.html:455,464`) as used in `reviewRow` ✓.

VERDICT: REVISE
