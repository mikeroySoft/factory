## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo; standards sources are `pyproject.toml`, `README.md`, `docs/*.md` (stdlib-only package, tests independent of GitHub credentials, `python -m unittest discover -s tests` gate). Diff complies: `factory/dashboard.py:811-855` is stdlib-only; `tests/test_factory.py:2812-2822` mocks `github`/`dispatcher`/`upstream_state` and uses the existing `mock.patch.dict(dashboard.__dict__)` pattern (`tests/test_factory.py:596`).

**Required fixes:** none.

**Optional suggestions**

- `factory/dashboard.py:826,829`, `tests/test_factory.py:2815` — added lines are 103–107 chars vs `[tool.ruff] line-length = 100` (`pyproject.toml:37`). Not enforced (E501 not in ruff defaults; `dashboard.py` on `origin/main` already has 27 such lines), so judgement call only.
- Possible Duplicated Code (judgement call): `factory/dashboard.py:823-833` re-implements dispatch's opt-in predicate (`factory/dispatch.py:209-223`) and `:843-850` re-implements the readiness state machine (`factory/dispatch.py:300-310`). The newest `review-readiness` row already carries `state` and `review_head`; the dashboard still needs the escalation override and the no-row case, so the gain is small. Leave unless the two drift.
- `factory/dashboard.html:1162` — `pr.reason` in the `review_pending` branch is dead: `reason` comes only from `escalation.get("reason")` or `current.get("reason")` (`factory/dashboard.py:853`), and `review-result` rows record `findings`, not `reason` (`factory/dispatch.py:363`); any escalation forces `state = "escalated"` (`:851-852`). `pr.verdict === 'APPROVE'` alone expresses the intended "approved on an old head, automation will not re-review" case (`factory/dispatch.py:234-236`).
- `CHANGELOG.md` `## Unreleased` lists dashboard features by issue (`#79`, `#67`); no `#9` entry. Not in the issue's Touches and no documented rule, so non-blocking.

## Spec

Issue #9 scope vs diff:

| Criterion | Evidence |
|---|---|
| PR number, title, author, current head, last reviewed SHA, verdict, CI state | `factory/dashboard.py:853-858` → rendered `factory/dashboard.html:1134-1142` |
| Six states | `factory/dashboard.py:843-852`; labels `factory/dashboard.html:1130` |
| Link each row to GitHub | `factory/dashboard.html:1136` (`href: pr.url`) |
| Stalled items in existing Inbox, no mutation controls | `factory/dashboard.html:1160-1165`, `:1550-1551` (text + link only) |
| Snapshot test: six states, PR links, SHA-specific data | `tests/test_factory.py:2826-2839` (states, url, head, old-head/unknown-CI on PR 1) |
| No label/review/branch/merge mutation | read-only projection; test asserts events file unchanged `tests/test_factory.py:2824` |
| README operating description | `README.md:349-365` |
| Touches limited to the four listed files | `--stat`: exactly those four |

Opt-in semantics match dispatch intake exactly (label, viewer review request, or prior admitted verdict; `factory/dashboard.py:823-833` vs `factory/dispatch.py:209-223`), and "old approval never makes a new head ready" holds: `current`/`readiness` are head-bound (`:835-838`), `review` is not (`:822`), matching the README text at `README.md:360`.

**Required fixes:** none.

**Optional suggestions**

- Coverage window: queue is built from the existing `pullRequests(first:100, CREATED_AT DESC)` (`factory/dashboard.py:150`), which includes closed/merged PRs. Trigger: repo with >100 PRs where an open opted-in PR is older than the newest 100. Impact: silently absent from queue and Inbox; dispatch uses `--state open --limit 1000` (`factory/dispatch.py:203-206`), so the two disagree. Pre-existing query shape shared with tickets; lazy fix is a one-line README bound note rather than a second query.
- Escalated external PRs count twice in "cases waiting on you": dispatch creates a `ready-for-human` issue (`factory/dispatch.py:255-262`) that becomes an `escalated` ticket item (`factory/dashboard.html:1170-1172`), plus the new `review-N` item (`:1160-1165`). Both are WAITING kinds. Not a spec breach (issue asks that escalations appear in Inbox).

**Summary:** Standards 0 required / 4 optional (worst: dead `pr.reason` branch). Spec 0 required / 2 optional (worst: 100-PR window undisclosed in README).

VERDICT: APPROVE
