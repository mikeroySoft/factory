**Standards.** No AGENTS.md/CONTRIBUTING.md/docs conventions in the snapshot; README is the only documented contract. Diff reuses existing `github()` transport, `briefing.bounded_file`, `h`/`pre`/`safeSourceURL` DOM helpers (body rendered as text nodes only, `dashboard.html:938,453-464`). Runtime-JSON path (`dashboard.py:1224-1250`) untouched; `snapshot()` is only invoked by `--json`/server.

**Spec (#79).** Schema-1 envelope, fixed bounds, canonicalization/`source_revision`/`observation_id` field sets, head-race handling, per-source coverage, sanitized errors, ownership requiring closing-issue link + `claimed` provenance, Factory review requiring valid lifecycle `result` row with matching heads — all present in `feedback.py` and exercised in `tests/test_feedback.py`. Briefing appends feedback after existing sources and reports omissions; HTML renders unsupported/unknown states. Exit gates 1–4 covered by tests; gate 6 passed.

**Plan drift.** No plan recorded. All changed files are named in the issue's Touches. Gate report carries no exit-gate-5 smoke evidence (live `--json`, browser Review drawer/Inbox, mutation-absence audit); the issue handoff comment contains a `--json` sample only. Not a code defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/dashboard.py","line":499,"summary":"Full-snapshot `errors` previously surfaced gh stderr (e.g. GH_TOKEN guidance); now a fixed 'GitHub read failed'. Sanitization is unnecessary here because feedback.gap() never includes exception text (feedback.py:706-709); this degrades a pre-existing operator diagnostic."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":828,"summary":"Reviews are read via REST, which has no `updated_at`, so `source_updated_at` is always null for reviews and every truncated review body raises `change_detection_incomplete`. GraphQL PullRequestReview.updatedAt is supported by the provider ('actual provider UTC update time when supported')."},
{"severity":"nit","pass":"spec","path":"factory/feedback.py","line":909,"summary":"REST check-run payloads have `app`, not `creator`; `author` for check_run items is always {login:null, kind:'unknown'} even when the provider identifies the app."},
{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":818,"summary":"On source_identity_conflict the previous item is removed but `counts[source]` is not decremented, so the 100-item budget is consumed by a discarded item."},
{"severity":"nit","pass":"bug","path":"factory/feedback.py","line":954,"summary":"Imports private `lifecycle._lifecycle`; no public validator exposed, so the cross-module coupling is on an underscore API."},
{"severity":"nit","pass":"spec","path":"tests/test_factory.py","line":1160,"summary":"Issue preferred adding cases to existing test files, with tests/test_feedback.py only if smaller/clearer; test_factory now imports fixtures (Provider, REPO, PR, ISSUE, H, AT, factory_events) from test_feedback, coupling the two modules."}
]}
```
VERDICT: APPROVE
