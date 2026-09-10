## Standards

Sources: README.md `## Configuration` (template documents every key), `factory/config.py` header comments (`HOST_TABLES`/`KNOWN_KEYS`), existing loader/doctor patterns. No AGENTS.md/CONTRIBUTING.md in repo.

1. **hard — `factory/config.py:286` char-splits a string-form `manager.command`.** `cfg.manager = list(manager["command"])` runs unconditionally, but the pre-existing reader of the same table, `manager_model()` (config.py:224-250, called at :302), accepts and `shlex.split`s a string (`"manager.command must be an argv array or command string"`, docstring "legacy manager argv"; README:171-172 says existing `manager.command` entries supply `--model`). With `command = "omp -p {prompt}"` the loader raises nothing, `manager_model` parses fine, yet `cfg.manager == ['o','m','p',' ',...]`: `factory doctor` emits `manager: o` FAIL (`factory/onboard.py:227`) and `dashboard --json` `config.manager.command` renders `"o m p   - p ..."` (`factory/dashboard.py:841`). Two readers of one value disagree. Fix: normalize once (reuse `manager_model`'s split/validation, or have it return the argv) before assigning `cfg.manager`.
2. **judgement — `factory/config.py:288` assigns `manager_review` unvalidated.** Template (`factory/templates/factory.toml:48`) documents only `"escalated"`/`"all"`; `unknown_keys()` checks key names, not values, so `review = "escalted"` passes doctor and shows in the dashboard as valid. Loader raises `ConfigError` for comparable constraints (`workers.default` :280-282, gate check names :295-296). Same for `rounds <= 0` at :287.
3. **judgement (Duplicated Code) — `factory/config.py:269`** introduces `manager = raw.get("manager", {})` but the existing `manager_model(raw.get("manager", {}))` at :302 still re-reads the table; pass the local.
4. Data Clumps on `manager*` fields (`config.py:104-106`) suppressed: repo convention is flat `Config` fields for every table (`llm_url`/`llm_model`, `dashboard_port`/`dashboard_theme`).

## Spec

Issue #12 / `docs/manager-plan.md` T3. Implemented: `KNOWN_KEYS` (`config.py:60`), `Config` fields (:104-106), loader (:285-288), conditional doctor row (`onboard.py:226-227`), dashboard `config.manager` (`dashboard.py:840-844`), template documents all three keys (`factory.toml:43-48`), loader test for all three keys (`tests/test_factory.py:112-115, 137-138`), drift test (:252, :263), doctor present/absent test (:276-284). `HOST_TABLES` already contained `manager` on main. Out-of-scope respected: nothing executes the command.

1. **hard — "`factory doctor` reports the binary" fails for a string `command` (`factory/config.py:286`).** Same defect as Standards #1: the binary reported is `o`, not the executable. Spec shape is `command = [...]`, but the repo's own `[manager]` reader treats the string form as valid input, so a host config that satisfies `manager_model` now yields a wrong doctor row and dashboard entry.
2. **judgement — `review = "escalated"` (`"all"` allowed)` is a closed set; `factory/config.py:288` accepts any string.** Nothing consumes the value yet (running is T4), so not an exit-gate failure today; a misspelling will only surface when T4 lands.

**Summary.** Standards: 3 findings, worst = char-split string `manager.command` (`config.py:286`). Spec: 2 findings, worst = same line, doctor reports `manager: o` instead of the binary.

VERDICT: REVISE
