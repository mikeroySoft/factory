## Review: #79 B2 source-versioned PR feedback

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; only documented standards are the CI gate (`.github/workflows/ci.yml:12`, `python -m unittest discover -s tests`) and README contracts. Spec source: issue #79 body + triage brief comment.

### Standards

No documented-rule violations. The diff reuses existing helpers (`briefing.bounded_file`, `h`/`pre`/`safeSourceURL`, `dashboard.sh`, `lifecycle._lifecycle`) rather than inventing parallel ones. `factory/feedback.py` is the single new module the issue asked for; no event bus/cache/second client introduced.

### Spec

Verified against issue text:

- Schema envelope, required keys, coverage with exactly 4 sources, errors bounded/sanitized, items sorted by `(evidence_id, source_revision)` — `review.diff:664-670, 984-988`.
- `source_revision` over exactly the 14 named fields — `review.diff:605-607, 811`. `observation_id` excludes timestamps/URLs/relevance — `review.diff:986-988`.
- Bounds `100/2/20000/32/30` as fixed constants — `review.diff:599-603`; initial+final head reads with race → `unknown` + both SHAs in reason — `review.diff:750, 920-931`.
- Factory review requires lifecycle `result` with `returncode==0`, `parsed is True`, `head==actual_head`, matching `exit` outcome — `review.diff:954-968`; matches real emitter fields in `dispatch.py:489-492`. Provenance-free `VERDICT` prose never becomes `factory_review` — tested `review.diff:1304-1309`.
- `--runtime-json` branch unchanged, returns before `snapshot()` (`dashboard.py:1224-1250`); collector only invoked from `snapshot()` — `review.diff:563-572`.
- Non-PR tickets get no feedback (`n in raw_prs` guard, `review.diff:563`); briefing appends feedback after existing candidates and reports omission/shortening counts — `review.diff:136-219`.
- Body rendered via `pre()` → text node (`dashboard.html:938`, `h()` line 464); URLs via `safeSourceURL` — `review.diff:286-289, 350-351`.

### Required fixes

1. **`producer.revision` can report an unrelated repository's HEAD.** `review.diff:555-558`: `git rev-parse HEAD` runs with `cwd=factory/` and the only guard is `git status --porcelain -- .` being empty. For the documented install paths (`uv tool install` / `pipx` / `pip install --user`, README:43-48) the package lives in a site-packages dir; if any ancestor is a git worktree (home-directory dotfiles repo, a project `.venv/` which is gitignored so `status` is empty) the command resolves that repo's HEAD and emits it as the Factory producer revision. Spec: `producer.revision` "is a verifiable source/build revision when available, otherwise null" and exit-gate item 7 keys #15 acceptance to "the actual merged producer revision". Impact: fabricated provenance in a field the spec designates as authority evidence. Fix (one line): additionally require `sh(["git","ls-files","--error-unmatch","feedback.py"], cwd=source_root)` non-empty, else `revision = None`.

### Optional suggestions

- `review.diff:496-499`: `github()` now raises bare `"GitHub read failed"` for the main snapshot query too, dropping `proc.stderr` that previously surfaced in `snapshot()["errors"]` (e.g. the `GH_TOKEN` hint). `feedback.request()` already replaces exception text with fixed messages (`review.diff:706-709`), so sanitizing inside `github()` is redundant for feedback and only degrades the existing error surface. Keep stderr on the default-query path.
- `review.diff:839-841, 906-912`: REST review objects carry `submitted_at`, not `updated_at`; REST check-runs carry `started_at`/`completed_at`, not `updated_at` or `run_attempt`. In production `source_updated_at` will be `null` for every review and check run, so any truncated body always triggers `change_detection_incomplete`. README documents the nulls, but `submitted_at`/`completed_at` (or GraphQL `PullRequestReview.updatedAt`) would give a real update signal the spec prefers "when supported".
- Cadence risk [INFERENCE, not measured]: `snapshot()` now issues ≥7 serial `gh` subprocesses per selected PR with a 30 s budget each (`review.diff:563-572`), under `_cache_lock` with a 15 s TTL (`dashboard.py:958-967`). Total snapshot time is unbounded in PR count. Spec says "Preserve full-snapshot cadence"; worth noting in handoff or batching reviews/threads into the existing GraphQL query.
- `review.diff:557`: dirty check is scoped to `factory/` only; a dirty `tests/` still yields a "clean" revision. Minor given fix #1.

### Plan drift

Plan status not recorded; nothing to diff against. Files changed (`CHANGELOG.md`, `README.md`, `factory/{briefing.py,dashboard.html,dashboard.py,feedback.py}`, `tests/{test_briefing.py,test_factory.py,test_feedback.py}`) all appear in the issue's **Touches** list; `tests/test_feedback.py` is the issue-permitted conditional file. Gate report covers conflict-markers/test/leak-scan only; exit-gate items 5 (disposable-repo CLI + live browser smoke, mutation-absence record) and 7 (handoff packet with sanitized example) are process evidence not present in the gate report. The second issue comment shows a sanitized snapshot example from a prior attempt, but no browser/no-mutation record for this revision is in evidence. Not a diff defect; flagging as missing proof.

VERDICT: REVISE
