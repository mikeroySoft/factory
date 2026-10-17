## Spec (issue #20)

- `when` on `[workers.<label>]` tables, legacy arrays preserved: `factory/config.py:288-298`; both forms exercised in `tests/test_factory.py:147-165`.
- Prompt lists labels with `when`; reserved labels excluded: `factory/manage.py:199-202`.
- ROUTE/FIX restricted to listed labels; FIX requires non-empty guidance: `factory/manage.py:54`, `factory/manage.py:63-69`. Unlisted/reserved/non-string worker → HUMAN with `manage` event recorded: `tests/test_factory.py:740-750`.
- Exit gate "ROUTE chore rejected/recorded without worker, label applied with one": `tests/test_factory.py:652-676`.
- Exit gate "FIX on red CI runs ci-fix argv": `factory/manage.py:118-121` dispatches `{data["worker"]}` only; `tests/test_factory.py:678-738` checks worker ran, guidance + packet passed, push only on gate PASS, `factory-approved` only on fresh APPROVE, no `ready-for-agent` requeue.
- Dashboard surfaces `worker_when`: `factory/dashboard.py:847`.
- README updated for the new decision and table form: `README.md:88-101`.

Observation (not a defect): the `ci-fix`/`conflict` profiles are shipped commented-out in `factory/templates/factory.toml:41-47`, so `factory init` yields no active profile. Consistent with the issue's "remain host-config, human-applied" and the triage brief; noting in case "ship" was intended as active defaults.

## Standards

No AGENTS.md/CONTRIBUTING.md at this head; README conventions (worker labels, `agent/<n>` branches, `manage` event before mutation at `factory/manage.py:219`) are followed.

VERDICT: APPROVE
