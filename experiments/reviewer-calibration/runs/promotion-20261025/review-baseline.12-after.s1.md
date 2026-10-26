## Standards

Sources: `snapshot/README.md`, `snapshot/docs/manager-plan.md`, `pyproject.toml` (`ruff line-length = 100`), `.github/workflows/ci.yml` (unittest only). No AGENTS.md/CONTRIBUTING.md in the repo.

- `factory/config.py:70-74` — loader pattern matches existing tables (`int(gate.get("timeout", …))`, `raw.get("review", {})`); `manager_review` validation raises `ConfigError` like sibling validators. Conforms.
- `factory/config.py:10` — `KNOWN_KEYS["manager"]` extended; `HOST_TABLES` already contained `"manager"` in base (`snapshot/factory/config.py:50`), so the "host-layer like `review`" requirement is met without a diff change. Proven by `tests/test_factory.py:209-212` loading from `[defaults.manager]`.
- `factory/config.py:28-54` — `manager_model` → `manager_settings` rename: no stale callers in `snapshot/` (only `tests/test_briefing.py`, updated in the diff). Clean cutover.
- `factory/templates/factory.toml:128-133` — documents all three keys plus `{prompt}`/`{cwd}` placeholders, consistent with `docs/manager-plan.md:136,171` (`review = "escalated"`, `rounds` default 1).
- Smell baseline: nothing notable. No new abstraction beyond extending the existing normalizer to return argv alongside model.

Judgement call (non-blocking): `tests/test_briefing.py:150,154` exceed 100 columns, but the replaced lines (`:149,153`) already did and ruff is not in CI.

## Spec (issue #12 + triage brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:18,70`; doctor/dashboard guard on falsy `cfg.manager` (`onboard.py:110`, `dashboard.py:95`) | met |
| `rounds = 1` default | `config.py:19,71` | met |
| `review = "escalated"`, `"all"` allowed, else error | `config.py:20,72-74`; `test_factory.py:217` | met |
| `factory doctor` prints `manager:` row only when configured | `onboard.py:110-111`; `test_factory.py:224-232` | met |
| Unknown `[manager]` keys reported as drift | `test_factory.py:190,198` | met |
| `dashboard --json` `config` includes table | `dashboard.py:94-98` | met (no test; exit gate doesn't require one) |
| `templates/factory.toml` documents it | `factory.toml:128-133` | met |
| Loader test for all three keys | `test_factory.py:169-172,180-181` | met |
| Out of scope: running the command | nothing in diff executes `cfg.manager`; `test_briefing.py:146-152` still asserts no execution | met |

Behavior change worth noting (not a defect): `config.py:36-45` now validates `manager.command` even when `manager.model` is set (previously skipped). Intended, since `command` is now a stored setting; covered by `test_factory.py:214-215`.

## Plan drift

Plan status: not recorded, so drift is judged against the issue's **Touches** list. Files changed outside that list: `tests/test_briefing.py` (forced by the rename; required to keep the suite green) and `tests/test_factory.py` (explicitly required by the exit gate). No planned proof is missing from the gate report; the gate's `test: PASS` covers the stated `tests/test_factory.py` verification. No drift exposes a defect.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:71` — `rounds` accepts `0`, negatives, and `true` (`int(True) == 1`). No spec rule constrains it; leave unless the consumer feature (out of scope here) needs a lower bound.
- `factory/dashboard.py:94-98` — no test asserts the `config.manager` block in `dashboard --json`. The exit gate doesn't require one and no sibling key (`reviewer`, `workers`) is tested either, so this is consistent with the existing suite.

VERDICT: APPROVE
