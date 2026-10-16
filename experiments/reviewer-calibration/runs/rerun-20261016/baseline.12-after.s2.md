## Spec (issue #12)

- `command`/`rounds`/`review` keys registered: `factory/config.py:60`; `Config` fields with defaults `1` / `"escalated"`: `factory/config.py:104-106`; loader populates and rejects anything other than `escalated`/`all`: `factory/config.py:287-291`.
- Legacy `manager_model` folded into `manager_settings` returning `(argv, model)`, still never executing: `factory/config.py:224-252`; sole caller in `tests/test_briefing.py:116-124` migrated; old loader call removed `factory/config.py:305`.
- `factory doctor` prints `manager:` only when `cfg.manager` set: `factory/onboard.py:226-227`; covered by `tests/test_factory.py:293-301` (absent → present).
- Unknown `[manager]` keys surface as drift: `tests/test_factory.py:252,263`.
- `dashboard --json` `config.manager` with all three keys: `factory/dashboard.py:840-844`.
- Template documents all three keys with `{prompt}`/`{cwd}` semantics: `factory/templates/factory.toml:43-48`.
- Loader test for all three keys: `tests/test_factory.py:112-115,137-138`; host-layer `[defaults.manager]` load + invalid-shape cases: `tests/test_factory.py:276-291`.
- Out of scope respected: nothing runs the command.

`HOST_TABLES` is not touched by the diff; `[defaults.manager]` already loads from the host layer (`tests/test_factory.py:279-281`) and the base README already documents `[defaults.manager]`, so `manager` is `[INFERENCE]` already registered there. No citable defect.

## Standards

README is the only convention source at this head; nothing in the diff contradicts it (host-layer `[defaults.manager]` semantics retained, dashboard never executes the command).

No findings.

VERDICT: APPROVE
