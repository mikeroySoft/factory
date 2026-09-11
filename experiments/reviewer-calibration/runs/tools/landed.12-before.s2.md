## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the repo; conventions come from `README.md` (§Configuration: "The template written by `factory init` documents them all") and existing code shape.

- Loader/field/doctor/dashboard additions mirror the `review` sibling exactly (`config.py:103` vs `:104`, `:283-284` vs `:285-286`; `onboard.py:225` vs `:226-227`; `dashboard.py:839` vs `:840-844`). Template comment style matches the `[review]` block (`factory.toml:32-36` vs `:43-48`). README still accurate (`README.md:170-172`).
- Smell, judgement call only: `tests/test_factory.py:281` is a verbatim copy of the doctor-rows lambda at `:234`. Existing style; not a rule.

## Spec (issue #12 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `config.py:104`, `:285-286`; placeholders documented `factory.toml:45` | met |
| `rounds = 1` | `config.py:105`, `:287` | met |
| `review = "escalated"`, `"all"` allowed | `config.py:106`, `:288` | met (any value accepted; see Optional) |
| Host-layer table (`HOST_TABLES`) | `config.py:50` already on main | met |
| `KNOWN_KEYS` | `config.py:60` | met |
| doctor prints `manager:` row when configured, nothing when not | `onboard.py:226-227`; test `tests/test_factory.py:276-284` | met |
| unknown `[manager]` keys = drift | test `tests/test_factory.py:252`, `:263` | met |
| `dashboard --json` `config` includes it | `dashboard.py:840-844` | met |
| template documents it | `factory.toml:43-48` | met |
| loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | met |
| Out of scope: running the command | nothing executes it | met |

No scope creep.

## Required fixes

1. **`config.py:286` — `list(manager["command"])` disagrees with the module's own reader of the same key.** `manager_model` (`config.py:231-237`, unchanged) accepts `manager.command` as either an argv array or a command string (`shlex.split`; error text "must be an argv array or command string"), and `README.md:171-172` tells users "an existing `manager.command` supplies only its `--model` value", i.e. pre-#12 host configs may hold that string form.
   - Trigger: `[defaults.manager] command = "omp -p --model openai-codex/gpt-6-astra"`.
   - Impact: `cfg.manager` becomes a list of characters → `factory doctor` prints `manager: o` FAIL (`onboard.py:227`), flipping `ok` to false for a config the loader accepts; `dashboard --json` `config.manager.command` is `"o m p   - p ..."` (`dashboard.py:841`). Violates the exit-gate criterion "`factory doctor` reports the binary".
   - Secondary: line 286 runs before `manager_model` at `:302`, so a non-list, non-str `command` (with `model` unset) now dies with a `TypeError` traceback instead of the existing `ConfigError`.
   - Fix (one line, either direction, but both readers must agree): normalize at `:286` the same way `:231-233` does (`shlex.split` when `str`), or make `manager_model` reject strings too. No new abstraction needed.

## Optional suggestions

- `config.py:288`: `review` is stored unvalidated. The issue defines a closed set (`"escalated"` default, `"all"` allowed); a `ConfigError` for anything else would match how `[workers]`/gate names are rejected (`config.py:280-281`, `:295-296`) and catch typos before T6 consumes it. Not required: no acceptance criterion demands rejection and the consumer doesn't exist yet.

VERDICT: REVISE
