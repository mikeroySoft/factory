## Spec (issue #20)

- `when` on `[workers.<label>]` tables, legacy arrays preserved — `factory/config.py:288-298` parses both forms, validates argv/`when`, populates `worker_when`. Covered by `tests/test_factory.py:147-165`.
- Manager prompt lists labels with `when`; reserved labels excluded — `factory/manage.py:199-201`. ROUTE/FIX accept only listed labels — `factory/manage.py:54`, `factory/manage.py:63-66`; `parse` now receives the filtered map (`factory/manage.py:213`).
- `ROUTE chore` without a `chore` worker → falls through to HUMAN (`factory/manage.py:72`), recorded via the existing `manage` event before mutation (`factory/manage.py:219`). With one, `--add-label chore` applied. Exercised at `tests/test_factory.py:652-675`.
- `FIX` on red CI runs the selected worker argv in the kept worktree with guidance + packet, re-gates, pushes only on PASS, re-reviews, approves only on fresh APPROVE — `factory/manage.py:104-131`. Exercised at `tests/test_factory.py:677-737` including gate-fail, REVISE, and rebase (`conflict`) cases.
- `ci-fix` / `conflict` profiles in `factory/templates/factory.toml:31-47`. They are commented-out, opt-in; consistent with the issue's "remain host-config, human-applied" and the out-of-scope note. Template comment tells the operator to keep a `default` in the host layer (`factory/templates/factory.toml:39`), which matches the `default` requirement at `factory/config.py:286`.
- All four named files touched; `dashboard.py:847` exposes `worker_when`.

## Standards

- README manager section updated for `FIX`, table-form workers, and the opt-in profiles (`README.md:88-102`). No AGENTS.md/CONTRIBUTING.md at this head; nothing in README's conventions is contradicted.
- Existing patterns reused: `dispatch.worker_round`, `dispatch.escalate`, `dispatch.review`, `dispatch.approve_pr`, `lifecycle.read_events` (`factory/manage.py:112-131`); no parallel mechanism introduced.

## Non-blocking observations

- `factory/manage.py:54` still subtracts `RESERVED_LABELS` from an already-filtered `workers` (`factory/manage.py:199`). Redundant, but keeps `parse` safe for callers passing `cfg.workers` directly.
- `factory/manage.py:109` `pr["reviewDecision"] == "CHANGES_REQUESTED"` handles the GitHub `null`/`""` case correctly (test stub uses `""`, `tests/test_factory.py:706`).
- README "Configuration" example (`[workers]` block) still shows only array form; the manager paragraph documents the table form, so the contract is documented.

No finding fails the issue's acceptance criteria or a documented convention.

VERDICT: APPROVE
