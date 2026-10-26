## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; the operative convention is in `factory/config.py` itself ("Every key the loader reads, by table; `factory doctor` reports anything else") plus the template-documents-all-keys rule (README.md:118-119).

- `review.diff:10` — `KNOWN_KEYS["manager"]` extended with exactly the keys the loader reads (`rounds`, `review`); `HOST_TABLES` already contained `manager` at base (snapshot `factory/config.py:50`). Convention met.
- `review.diff:71,75` — `rounds` coerced via `int(...)` identically to `gate.timeout`/`dashboard.port`; matches existing pattern (a non-numeric value raises `ValueError`, not `ConfigError`, same as those siblings — pre-existing convention, not flagged).
- `review.diff:95` — `" ".join(cfg.manager)` mirrors `workers`/`reviewer` rendering at `dashboard.py:92-93`.
- `review.diff:28-54` — `manager_model` → `manager_settings` returning `(command, model)`: no new abstraction, single function still, all callers migrated (`tests/test_briefing.py:119-124`, `config.py:287`). Not speculative.

No violations.

## Spec (issue #12 / brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `review.diff:18` (`None` default), `:36-44` validation, `:70` | met |
| `rounds = 1` default | `review.diff:19,71` | met |
| `review = "escalated"`, `"all"` allowed, others rejected | `review.diff:20,72-74`; test `:217` | met |
| Host-layer like `review` | `HOST_TABLES` already includes `manager` (base) | met |
| `doctor` prints `manager:` row only when configured | `review.diff:110-111`; test `:224-232` | met |
| Unknown `[manager]` keys reported as drift | `review.diff:190,198` | met |
| `dashboard --json` `config` includes table | `review.diff:94-98` | met |
| `templates/factory.toml` documents it | `review.diff:125-133` | met |
| Loader test for all three keys | `review.diff:169-172,180-181` | met |
| Out of scope: running the command | nothing executes `cfg.manager`; `:146-152` asserts no execution | met |

## Plan drift

Worker plan not recorded; drift judged against the issue's "Touches" list.

- Files changed not named: `tests/test_factory.py` (required by exit gate — not drift in substance), `tests/test_briefing.py` (`review.diff:137-157`, forced by the `manager_model` → `manager_settings` rename). Neither exposes a defect.
- Planned proof: exit-gate items all have assertions in `tests/test_factory.py`; gate `test: PASS`. No missing proof.

## Required fixes

None.

## Optional suggestions

- `review.diff:36-45` — behaviour change: a `[manager]` with `model` set and a malformed `command` (bad shell quoting / non-string argv) was previously ignored, now raises `ConfigError`. Correct for a real `command` key, but worth a line in the template comment (`:131`) so existing hosts hitting it know why `load()` started failing.
- `review.diff:71` — `rounds = 0` / negative is accepted. Spec doesn't require bounds; T4 will need `round <= manager.rounds`, so a `>= 0` check could land then.

VERDICT: APPROVE
