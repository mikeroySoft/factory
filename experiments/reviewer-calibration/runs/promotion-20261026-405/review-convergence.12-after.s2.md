No blocking defects found. The diff meets issue #12's scope and exit gate.

**Standards.** The repo has no `AGENTS.md` or `CONTRIBUTING.md`. The only docs are `README.md`, `docs/manager-plan.md` and `skills/factory/SKILL.md`. The diff follows the existing patterns in each area:
- **Loader:** the new keys are read the same way as the other `int`/`.get` keys and use `ConfigError`.
- **Doctor:** the new row uses the same `report(shutil.which(...))` call as the reviewer row.
- **Dashboard:** the command is shown as a joined string, like `workers` and `reviewer`.
- **Drift:** `manager` was already in `HOST_TABLES` (`config.py:50`), so host filtering and drift reporting cover it without new code.
- **Abstraction:** the diff adds none. `manager_settings` (`config.py:224`) replaces `manager_model` and reuses its parsing rather than adding a second parser.

**Spec (issue #12 and the triage agent brief).**
- **`command`:** an argv template. When it is unset, `cfg.manager` stays `None` (`config.py:104,229`).
- **`rounds`:** defaults to 1 (`config.py:105,288`).
- **`review`:** defaults to `"escalated"`, accepts `"all"`, and rejects any other value (`config.py:106,289-291`).
- **Doctor:** shows a `manager:` row only when a command is configured (`onboard.py:226-227`).
- **Drift:** unknown keys under `[manager]` are reported (test at `tests/test_factory.py:252,260`).
- **Dashboard:** `dashboard --json` `config` includes the table (`dashboard.py:840-844`).
- **Template:** `templates/factory.toml` documents it (`factory/templates/factory.toml:43-48`).
- **Loader test:** one test covers all three keys (`tests/test_factory.py:112-115,137-138`).
- **Out of scope:** the command is never run.

**Plan drift.** No plan was recorded, so I checked against the issue's "Touches" list.
- **Unnamed file changed:** `tests/test_briefing.py`. This follows from the `manager_model` → `manager_settings` rename and exposes no defect.
- **Named file left unchanged:** `HOST_TABLES`. It already contained `manager`.
- **Proof missing from the gate report:** it shows only a generic `test: PASS`. It does not name the planned `uv run pytest tests/test_factory.py` run. The gate is authoritative, so this is not a defect.

```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"spec","class":"legacy-command-semantics-undocumented","path":"factory/config.py","line":287,"summary":"A manager.command that was previously only a source for the briefing model (README.md:170-174 says it supplies 'only its --model value') now also fills cfg.manager. That turns on the frontier-manager config and the doctor row for existing configs that set it. README.md is not updated; only the template comment explains the change.","instances":[{"path":"factory/config.py","line":287,"summary":"The legacy command now fills cfg.manager as well as cfg.manager_model."},{"path":"factory/templates/factory.toml","line":43,"summary":"The template comment is the only place that documents the new meaning; README.md:170-174 still describes the model-only meaning."}]},{"id":"F2","severity":"nit","pass":"bug","class":"unvalidated-rounds","path":"factory/config.py","line":288,"summary":"manager.rounds is passed through int() with no range check, so 0, negative values and booleans are accepted silently. manager.review, by contrast, is checked against an allowed set. This has no effect until a stage reads rounds.","instances":[{"path":"factory/config.py","line":288,"summary":"int(manager.get('rounds', ...)) with no bound check."}]}]}
```
VERDICT: APPROVE
