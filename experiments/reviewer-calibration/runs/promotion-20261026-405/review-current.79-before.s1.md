## Standards

No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` coding rules exist in the snapshot; CI (`.github/workflows/ci.yml`) runs only `python -m unittest discover -s tests`. Baseline-smell judgement calls only:

- **Duplicated Code** — `factory/dashboard.py:853-869` re-reads and hand-parses `events.jsonl` (split/pop/json.loads loop) immediately after `snapshot()` already holds the coherent committed `rows` from `lifecycle.journal_snapshot` (dashboard.py:837). Second read is outside the journal lock, so provenance and `executions` can disagree. Nit.
- `tests/test_factory.py:2401` imports `from tests.test_feedback import …`; `tests/` has no `__init__.py` and no other test uses that form — works only because discovery runs from repo root (namespace package via cwd). Nit.
- Cross-module use of private `lifecycle._lifecycle` (`feedback.py:373`) has precedent in `runtime_events.py:171`; not flagged.

## Spec

Schema-1 envelope, item fields, enums, explicit nulls, `source_revision` field set (`feedback.py:605-607`), `observation_id` composition (excludes `observed_at`, includes coverage status/truncated/reason), sorted items, bounds (100/2/20 000/32/30 s), initial+final head reads with race → `unknown`, independent-source survival, sanitized fixed error messages, Factory-review provenance keyed on lifecycle `result` rows (`returncode==0`, `parsed`, `head==actual_head`, enter/exit framing — matches `dispatch.py:450-492`), ownership = same-repo `closingIssuesReferences` + retained `claimed` event (matches `dispatch.py:1297,1319`), runtime-json path untouched (`dashboard.py:1224-1250` never calls `snapshot()`), briefing appends feedback after existing candidates with omission/shortening notices, HTML renders via `h()` text nodes + `safeSourceURL`. README/CHANGELOG document bounds and read-only authority.

Defect:

- **`producer.revision` can report a foreign repository's commit** — `dashboard.py:875-878`: `git rev-parse HEAD` runs in the installed package dir; `git status --porcelain -- .` is empty for a git-ignored path. Trigger: non-editable install into a venv under a project repo (e.g. `<repo>/.venv/`, the CI install form `pip install '.[atlas]'`). Result: a clean, unrelated project SHA is emitted as the producer revision. Spec: "`revision` is a verifiable source/build revision when available, otherwise null"; README (diff line 85) promises "a clean source-checkout Git revision, otherwise null". Fix is one guard: require `git ls-files --error-unmatch __init__.py` (cwd `source_root`) to succeed before trusting `rev-parse`.

Minor:

- `dashboard.py:499` replaces `proc.stderr.strip()` with a fixed `"GitHub read failed"` for the pre-existing full-snapshot GraphQL path. Feedback already discards exception text in `feedback.request` (`feedback.py:~108`), so this only degrades the existing `snapshot()["errors"]` diagnostics (auth/not-found detail lost). Nit.

## Plan drift

Plan not recorded. All changed files (`CHANGELOG.md`, `README.md`, `factory/{briefing.py,dashboard.html,dashboard.py,feedback.py}`, `tests/{test_briefing.py,test_factory.py,test_feedback.py}`) are named in the issue's Touches. Gate report lacks exit-gate 5 proof (disposable-repo `python -m factory dashboard --json` inspection, live server + browser Review drawer/Inbox observations, no-mutation record); the issue handoff comment shows a snapshot JSON excerpt only. Missing proof, not a code defect.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/dashboard.py","line":876,"summary":"producer.revision resolves `git rev-parse HEAD` from the installed package dir; when factory is pip-installed into a git-ignored venv inside another repo, `git status --porcelain -- .` is empty and the enclosing repo's HEAD is emitted as the producer revision. Spec requires a verifiable source revision or null; README promises a clean source-checkout revision. Guard with `git ls-files --error-unmatch __init__.py` before trusting rev-parse."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":499,"summary":"Pre-existing full-snapshot GraphQL failure now raises fixed 'GitHub read failed' instead of gh stderr; feedback.request already discards messages, so this only loses operator diagnostics in snapshot()['errors']."},
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":853,"summary":"Duplicated Code: events.jsonl is re-read and hand-parsed outside the journal lock although snapshot() already holds committed `rows` from lifecycle.journal_snapshot at line 837; provenance and executions can diverge."},
{"severity":"nit","pass":"bug","path":"tests/test_factory.py","line":2401,"summary":"`from tests.test_feedback import …` relies on an implicit namespace package from cwd (no tests/__init__.py, no precedent); breaks if discovery runs from another directory."}
]}
```

VERDICT: REVISE
