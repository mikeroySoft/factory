**Setup note:** `./review.diff` and `./snapshot` were absent from the sandbox; reconstructed the diff as `git diff 07913b4..ce5568e` (origin/main → gated head, 3 commits, 6 files) and used the checked-out tree as the snapshot. Issue #20 fetched from github.com (no `gh` auth).

## Standards
No `AGENTS.md`/`CONTRIBUTING.md` in repo; standards sources are `README.md`, `docs/manager-plan.md` (T11), `skills/factory/SKILL.md`, and the template's own "defaults shown commented out" convention (`factory/templates/factory.toml:2-3`).
- `factory/config.py:288-298` — table-or-array loader matches plan T11 "Touches: config.py (`[workers]` becomes table-or-array)"; validation is stricter than the old `list(v)` (rejects string/empty/non-str argv). `KNOWN_KEYS["workers"] = None` so `factory doctor` won't flag `when`.
- `factory/manage.py:33,54,65,199` — reserved-label set hoisted; prompt and `parse` both filtered. No baseline smells beyond a harmless redundancy (`parse` re-subtracts `RESERVED_LABELS` after the caller already filtered).
- `factory/manage.py:104-131` — FIX reuses `worker_round`/`review`/`pr_comment`/`approve_pr`/`escalate` exactly as plan T6 line 283-284 prescribes; `--force-with-lease` push after gate PASS only; bounded by `manager_rounds` because each failure re-escalates with `round+1` (`dispatch.py:369-372`) and `manage_pass` skips rounds > `manager_rounds`.
- `README.md:88-102` and template comments updated; no doc drift found.

## Spec (issue #20 / brief)
- `when` on `[workers.<label>]` → `config.py:291-298`, `dashboard.py:847`. ✓
- Prompt lists labels with `when` → `manage.py:200-201`. ✓
- ROUTE/FIX only for listed labels → `manage.py:54-57,64-66`; tests `test_factory.py:652-676,737-748`. ✓
- `ci-fix` / `conflict` profiles in template → `factory.toml:41-47` (commented, per template convention). ✓ with caveat below.
- Array-form still loads → `test_factory.py:147-159`. ✓
- "FIX on red CI runs the ci-fix argv" → `test_factory.py:678-735` (gate fail / REVISE / APPROVE / rebase cases; asserts push only on gate PASS, label only on APPROVE, `ready-for-agent` never re-added). ✓
- Out of scope respected: no host-config writes.

## Plan drift
Plan status not recorded → no planned file list or proof to diff against. Relative to the issue's "Touches", extra files are `README.md` and `tests/test_factory.py`; both are required by the exit gate (`uv run pytest tests/test_factory.py`) and documented-behavior sync. No defect exposed.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":41,"summary":"ci-fix/conflict ship commented-out, so on a repo with no [workers] config a manager FIX {\"worker\":\"ci-fix\"} is rejected as unknown until a human copies the profile; this follows the template's commented-defaults convention and the issue's 'human-applied' clause, but 'Ship two default profiles' could also be read as code defaults alongside DEFAULT_CHORE_WORKER. Confirm intended reading."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":104,"summary":"FIX path never posts a 'Factory manager:' comment on the issue (every other decision does, manage.py:134-157); the ticket stays ready-for-human with the outcome visible only in events.jsonl and the PR comment. Not a documented rule; consider one issue comment for diagnosability."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":54,"summary":"`set(workers) - RESERVED_LABELS` is now redundant: manage_pass already filters reserved labels before calling parse (manage.py:199,213). Harmless; keeps parse safe for other callers."}]}
```
VERDICT: APPROVE
