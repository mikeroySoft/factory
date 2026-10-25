## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in snapshot; conventions inferred from `factory/config.py` loader and `factory/templates/factory.toml`.

- Loader mirrors the `review.command` pattern (`factory/config.py:283-288`, review.diff:34-39); `KNOWN_KEYS`/`unknown_keys` reused for drift, no new abstraction. OK.
- Template comment block matches existing style (`factory/templates/factory.toml:78-90`, review.diff:78-90). OK.
- Smell baseline: nothing beyond judgement-level. Minor duplicate lookup: `manager = raw.get("manager", {})` at `factory/config.py:269` (review.diff:28) while `manager_model(raw.get("manager", {}))` at `:302` still re-reads it. Optional.

## Spec (issue #12 + agent brief)

| Criterion | Status | Evidence |
|---|---|---|
| `command` argv template, unset = disabled | ✓ | `factory/config.py:104,285-286` (review.diff:18,36-37) |
| `rounds = 1`, `review = "escalated"` (`"all"` allowed) | ✓ defaults; `review` unvalidated (see Optional) | `factory/config.py:105-106,287-288` (review.diff:19-20,38-39) |
| `HOST_TABLES` / `KNOWN_KEYS` | ✓ — `"manager"` already in `HOST_TABLES` at base (`snapshot/factory/config.py:50`, untouched); `KNOWN_KEYS` extended | review.diff:10 |
| doctor `manager:` row only when configured | ✓ | `factory/onboard.py:226-227` (review.diff:67-68); test `tests/test_factory.py:276-284` (review.diff:139-147) |
| unknown `[manager]` keys reported as drift | ✓ | test `tests/test_factory.py:252,263` (review.diff:123,131) |
| `dashboard --json` `config` includes it | ✓ | `factory/dashboard.py:840-844` (review.diff:51-55) |
| template documents it | ✓ | review.diff:85-90 |
| loader test for all three keys | ✓ | review.diff:102-105,113-114 |
| Out of scope: running the command | ✓ not executed anywhere |

## Required fixes

1. **String-form `manager.command` is silently exploded into characters.** `factory/config.py:286` (review.diff:37) does `cfg.manager = list(manager["command"])`. The same table's loader explicitly accepts a string command (`snapshot/factory/config.py:231-233` `shlex.split`), and README:171-172 documents an "existing `manager.command`". Trigger: `[manager] command = "omp -p --model x {prompt}"` → no `ConfigError` (`manager_model` runs after, `:302`), `cfg.manager == ['o','m','p',...]`. Impact: doctor prints `manager: o` (acceptance "`factory doctor` reports the binary" is wrong), `dashboard --json` shows `"command": "o m p ..."`. Fix: either normalize like `manager_model` (`shlex.split` when `str`) or reject non-list with `ConfigError` before `:286`. One line either way; do not add a helper.

## Optional suggestions

- `review` accepts any string (`factory/config.py:288`, review.diff:39); issue names a closed set `"escalated"|"all"`. A `ConfigError` on other values would catch typos before T4/T6 consume it. Not required: no consumer yet, issue doesn't state rejection.
- Non-table `[manager]` (e.g. `manager = 5`) now raises `TypeError` at `factory/config.py:285` (`"command" in 5`) before `manager_model`'s `ConfigError("[manager] must be a table")` at `:302`. Pre-diff this produced a clean `ConfigError`. Moving the `manager_model` call above `:285`, or guarding `isinstance(manager, dict)`, restores it.
- Drop the duplicate `raw.get("manager", {})` at `:302` in favour of `manager`.

## Plan drift

Plan status not recorded; judged against issue "Touches". Files changed: `factory/config.py`, `factory/dashboard.py`, `factory/onboard.py`, `factory/templates/factory.toml`, `tests/test_factory.py` — all named or implied (exit gate requires a loader test). No unplanned files. Gate report shows `test: PASS` without naming the command; cannot confirm it was `uv run pytest tests/test_factory.py` specifically, but the new tests are in that file.

VERDICT: REVISE
