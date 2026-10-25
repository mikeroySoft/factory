## Standards

Sources checked: `README.md` (Configuration: "The template written by `factory init` documents them all"), `pyproject.toml` (ruff defaults, line-length 100 — E501 not enabled), `docs/manager-plan.md` §4.3/§6 T3, `templates/factory.toml`. No `CONTRIBUTING.md`/`AGENTS.md` in the snapshot.

- Host-layer registration: `"manager"` already in `HOST_TABLES` (snapshot `config.py:50`); diff adds the keys to `KNOWN_KEYS` (`review.diff:10`) so `unknown_keys` → doctor drift works without further wiring. Matches the `review` precedent.
- Loader follows the existing per-key pattern (`int(table.get(...))`, `ConfigError` for shape errors) — `review.diff:70-74`. Validation of `review` ∈ {escalated, all} is the same style as the gate-check-name check.
- Template documents all three keys and the placeholders (`review.diff:128-133`), satisfying the README rule that the template documents every key.
- Tests extend existing `HostConfigTest`/stub-bin patterns (`review.diff:206-232`).
- Smell check: no new abstraction; `manager_model` → `manager_settings` returns the already-parsed argv instead of re-parsing it twice (`review.diff:30-54`). Minor judgement call only: a tuple return is a small Data Clump, but with two fields and one caller it's not worth a type.

No standards violations.

## Spec (issue #12, brief in the triage comment)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `review.diff:18,36-44,70`; string form via `shlex` kept | met |
| `rounds` default 1 | `review.diff:19,71` | met |
| `review` default `"escalated"`, `"all"` allowed, else error | `review.diff:20,72-74`; test `review.diff:217` | met |
| Host-layer like `review` (`HOST_TABLES`, `KNOWN_KEYS`) | `KNOWN_KEYS` `review.diff:10`; `HOST_TABLES` pre-existing; host-file test `review.diff:209-212` | met |
| `factory doctor` prints `manager:` row only when configured | `review.diff:110-111`; test `review.diff:224-232` | met |
| Unknown keys under `[manager]` reported as drift | test `review.diff:190,198` | met |
| `dashboard --json` `config` includes the table | `review.diff:94-98` | met |
| Loader test covering all three keys | `review.diff:169-172,180-181` | met |
| Out of scope: running the command | nothing executes `cfg.manager`; `test_manager_settings_never_execute_the_command` retained (`review.diff:146-152`) | met |

Behaviour change worth noting, not a defect: `command` is now validated even when `model` is set (`review.diff:37-44`), whereas `manager_model` previously skipped it. Correct now that `command` is a first-class key.

## Plan drift

Plan status not recorded; compared against the issue's **Touches** list.

- Files changed beyond the named set: `tests/test_briefing.py` (rename for `manager_settings`, forced by the refactor), `tests/test_factory.py` (required by the exit gate's loader test). Neither exposes a defect.
- Planned proof: exit gate names `uv run pytest tests/test_factory.py` → gate report `test: PASS`. Doctor-row and drift criteria are covered by the added tests rather than a manual `factory doctor` transcript; acceptable since the tests exercise the real CLI via `factory(repo, "doctor", "--json")` (`review.diff:229`).

## Required fixes

None.

## Optional suggestions

- `review.diff:71`: a non-numeric `rounds` surfaces as `ValueError`, not `ConfigError`. Same as every other `int(...)` key in the loader, so leave it unless the repo decides to fix them together.
- `review.diff:229`: 150-char line; ruff's default rule set doesn't enforce E501 and the surrounding code already exceeds 100, so purely cosmetic.

VERDICT: APPROVE
