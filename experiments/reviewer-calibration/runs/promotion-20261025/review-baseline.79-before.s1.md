## Review scope note

`./review.diff` and `./snapshot` are absent from the sandbox. Reconstructed from the checked-out gated revision: `git diff origin/main...HEAD` = `8fba9c5..7bd5805` (base matches the revision the issue names), working tree = snapshot. Line citations below are gated-revision file lines.

## 1. Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; documented gate is `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`, `docs/*`). The diff adds unittest-only tests, keeps the package dependency-free, updates `CHANGELOG.md:9` under Unreleased with `(#79)` and documents schema/bounds in `README.md:21-93` — consistent with existing entries.

Judgement calls (non-blocking):
- `factory/feedback.py` uses single-quoted strings throughout; every other module in `factory/` uses double quotes. Not a documented rule.
- `pyproject.toml:36-38` sets ruff `line-length = 100`; many lines in `factory/feedback.py` exceed it (e.g. `:198`, `:325-331`). Ruff is not in CI and `docs/codebase-history-plan.md:58` records pre-existing dashboard findings, so this is a preference only.
- Possible Duplicated Code: `snapshot()` already holds the full journal via `lifecycle.journal_snapshot` (`factory/dashboard.py:837`), then re-reads the same file via `briefing.bounded_file` for provenance (`:853-873`). Two reads of one journal in one snapshot can disagree. The spec asked to reuse "accepted bounded lifecycle/event readers", so the bounded reader is defensible; noting only.

## 2. Spec (issue #79)

Satisfied, verified against the diff:
- Schema-1 envelope and item fields with explicit nulls (`factory/feedback.py:83-89`, `:198-207`); `REVISION_FIELDS` (`:24-26`) equals the normative list exactly; `observation_id` hashes repo/PR native IDs, final head, sorted `(evidence_id, source_revision)` pairs, and coverage status/truncated/reason only (`:405-407`).
- Fixed bounds `ITEM_LIMIT/PAGE_LIMIT/BODY_LIMIT/ERROR_LIMIT/DETAIL_TIMEOUT` (`:18-22`); initial/final head reads with race → partial + both SHAs + `unknown` relevance (`:339-350`, `:389-400`).
- Factory review requires accepted lifecycle `result` (returncode 0, parsed, head==actual_head, enclosing enter/exit with matching outcome) — field names match the real emitter at `factory/dispatch.py:489-492` and `lifecycle._lifecycle` validation (`factory/feedback.py:373-387`). `VERDICT` prose never becomes an item.
- No `delivered/ready/approved_for_merge` emitted; no cache/event append/model call.
- Full-snapshot-only integration at `tickets[].pr.feedback` (`factory/dashboard.py:883-892`); `--runtime-json` branch untouched (`:1224-1250`) and already network-guarded by `tests/test_runtime.py:34`.
- Briefing appends feedback after existing/human sources and reports omissions/shortening (`factory/briefing.py:288-345`, `:364-373`); unsupported/missing schema is an explicit notice, not empty success.
- HTML renders body via `pre()`/text nodes only (`factory/dashboard.html:938`, `:1071`); links go through `safeSourceURL` (`:1225`).
- Legacy `checks/comments/verdicts/review_decision/gate_text` preserved and asserted (`tests/test_factory.py:2419-2425`).

### Required fixes

1. **`producer.revision` can report an unrelated repository's commit** — `factory/dashboard.py:875-878`. `git rev-parse HEAD` runs with `cwd=<package dir>` and is accepted whenever `git status --porcelain -- .` is empty. For a non-editable install whose site-packages sits in a gitignored directory inside any git work tree (e.g. the README-documented `pip install --user` on a home-directory dotfiles repo, or a project `.venv`), `rev-parse` returns that work tree's HEAD and `status --porcelain` is empty for ignored paths (reproduced locally with a throwaway repo: rev-parse succeeds, status empty). Impact: a fabricated "verifiable source/build revision" is published in every feedback object, which #15's handoff is meant to rely on. Spec: "`revision` is a verifiable source/build revision when available, otherwise null." Minimal fix: additionally require the package to be tracked (`git ls-files --error-unmatch __init__.py` succeeds) or `git rev-parse --show-toplevel` to be the package's parent; otherwise `None`.

2. **Full-snapshot GitHub error diagnostics regressed** — `factory/dashboard.py:215-216`. The shared transport now raises the fixed string `"GitHub read failed"` for every failure (previously `proc.stderr.strip() or "gh api graphql failed"`). The default-query path surfaces this into `snapshot()["errors"]` (`:828-829`), which the dashboard and `briefing.sources_for` show as "Snapshot collection errors". Trigger: any `gh` failure (auth missing, rate limit, repo not found). Impact: operators lose the reason. No spec justification: the sanitization requirement is for the feedback object's `errors[]`, and `feedback.request()` already replaces every exception message with a constant (`factory/feedback.py:125-128`), so masking in `github()` buys nothing for feedback and only degrades the existing consumer. Fix: restore the stderr message for the default full query (one line).

### Optional suggestions

- REST review rows have `submitted_at`, not `updated_at` (`factory/feedback.py:258`), so `source_updated_at` is always null for `review` items and any >20 KB review body is flagged `change_detection_incomplete`. Check-run rows carry `app`, not `creator` (`:328`), so check-run author is always `unknown`. Either use the available provider fields or document both nulls alongside the check-run notes in `README.md:57-58`.
- Latency: each selected PR issues up to 7 `gh` subprocesses with a 30 s budget, serially, inside `snapshot()` while `_cache_lock` is held (`factory/dashboard.py:962-967`), blocking `/api/snapshot` and `/api/ask`. The spec asks to "preserve full-snapshot cadence"; worth measuring on a real repo with several open agent PRs.
- `collect(..., events: list[dict] = ())` (`factory/feedback.py:72`) — tuple default on a `list` annotation; cosmetic.

## 3. Plan drift

Plan status "not recorded", so no file list to diff against. Files changed match the issue's Touches list exactly (`feedback.py`, `dashboard.py`, `briefing.py`, `dashboard.html`, `test_factory.py`, `test_briefing.py`, `test_feedback.py`, `README.md`, `CHANGELOG.md`); no out-of-scope files.

Planned proof missing from the gate report: exit-gate 5 (disposable repo `python -m factory dashboard --json`, live server + real-browser Review drawer/Inbox check, `sources_for` bundle inspection, no-mutation record) and exit-gate 7 handoff artefacts are not in the gate report (only conflict-markers/test/leak-scan). The issue comment shows a sanitized `--json` excerpt from attempt 4, but it is untrusted and not tied to this head. Not blocking on its own; the Required items above are.

VERDICT: REVISE
