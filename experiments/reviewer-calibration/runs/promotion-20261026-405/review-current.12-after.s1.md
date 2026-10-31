I found no blocking defects. I reviewed the diff and the snapshot only, without running builds or tests.

**Spec (issue #12 and its agent brief):** the diff covers everything in scope.
- **Config table:** `command` accepts an argv list or string, and unset means disabled. `rounds` defaults to 1. `review` defaults to `"escalated"`, accepts `"all"`, and any other value raises `ConfigError`. The new keys are listed in `KNOWN_KEYS`, and `manager` was already in `HOST_TABLES` (`snapshot/factory/config.py:50`).
- **Doctor and dashboard:** `factory doctor` shows a `manager:` row only when a command is set. `dashboard --json` now includes the manager settings, and the template documents them.
- **Exit gate:** the tests cover the doctor row being present or absent, unknown `[manager]` keys showing up as drift, and a loader test with all three keys.
- **Out of scope:** nothing runs the command.
- **Rename:** `manager_model` became `manager_settings`, and every caller now uses the new name. `briefing.py` still only reads `cfg.manager_model`.

**Standards:** the snapshot has no AGENTS.md or CONTRIBUTING.md, and the docs (`docs/manager-plan.md` T3) match the issue. The new code uses the same `int(...)` reads and `" ".join(argv)` display as the existing `[review]` and `[workers]` handling.

**Plan drift:** no plan was recorded.
- `tests/test_briefing.py` isn't in the issue's Touches list, but changing it was required by the function rename.
- No test checks that `dashboard --json` includes the manager settings. The issue requires the feature but its exit gate doesn't ask for a test, so this isn't blocking.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"manager.rounds is read with a bare int(): rounds = 0, negative values and booleans are accepted, and a non-numeric string raises ValueError instead of ConfigError. Other integer keys like dispatch.review_rounds work the same way, so this is a nit; a later ticket (T4) bounds the manager by this value, so a check that it is >= 1 would be cheap."},{"severity":"nit","pass":"bug","path":"factory/config.py","line":37,"summary":"manager.command is now always parsed and type-checked, even when manager.model is set. Before, it was only read when model was unset. An older config with a model plus a malformed command, for example unbalanced quotes, used to load and now fails config.load() for every subcommand. This is probably intended because the command is now real configuration, but it is a behavior change."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":94,"summary":"No test checks that the dashboard --json config block includes the manager table. The issue requires the feature but its exit gate does not require a test for it."}]}
```
VERDICT: APPROVE
