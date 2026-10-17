## Findings

### Spec (issue #20)

- `when` rules on `[workers.<label>]`, table-or-array loading, validation — `factory/config.py:288-298`; legacy array form covered by `tests/test_factory.py:147-165`. Satisfied.
- Manager prompt lists routable labels with `when` (or `(no when rule)`) and excludes reserved labels — `factory/manage.py:199-201`; `parse` receives the filtered dict — `factory/manage.py:213`. Satisfied.
- ROUTE/FIX restricted to listed labels — `factory/manage.py:54`, `factory/manage.py:63-69`; rejection recorded as `HUMAN` via existing `dispatch.record("manage", …)` before `apply` — `factory/manage.py:219-221`. Exit-gate scenarios covered: `tests/test_factory.py:652-675` (ROUTE chore without/with worker), `tests/test_factory.py:677-717` (FIX on red CI runs the `ci-fix` argv, gate+review gating), `tests/test_factory.py:719-729` (unlisted/reserved workers).
- `dashboard.py` exposes `worker_when` — `factory/dashboard.py:847`. Satisfied.
- Profiles: `factory/templates/factory.toml:41-47` ship `ci-fix` and `conflict` commented out, with instructions to copy into host config (`factory/templates/factory.toml:36-38`). Issue text says "Ship two default profiles … New profiles remain host-config, human-applied", which can be read as the two defaults being active in the template; the triage brief (`these remain host-config that humans apply`) reads them as opt-in. The diff follows the brief. [INFERENCE] A freshly `factory init`'d repo therefore has no `ci-fix` worker and a `FIX ci-fix` decision would be rejected until a human applies the profile. Not failing the stated exit gate (which is exercised with the worker configured), so not a REVISE item; flagging the ambiguity.

### Standards (README.md; no AGENTS.md/CONTRIBUTING.md at this head)

- README convention "records a `manage` event before GitHub mutations" holds for FIX: record at `factory/manage.py:219`, all GitHub/worker mutations in `apply` at `factory/manage.py:104-131`.
- FIX preconditions (kept worktree, open `agent/<n>` PR, no CHANGES_REQUESTED, branch check) fail with `ValueError` → existing `mechanism_failure` path — `factory/manage.py:107-112`, `factory/manage.py:222-225`. Consistent with README "failure stays with the human".
- Push only on gate PASS, approve only on fresh APPROVE, no `ready-for-agent` re-add — `factory/manage.py:121-131`; README text updated accordingly — `README.md:93-102`.
- Minor, non-blocking: `RESERVED_LABELS` filtering is applied both at the call site (`factory/manage.py:199`) and inside `parse` (`factory/manage.py:54`, `factory/manage.py:65`); redundant but harmless.

No findings fail the acceptance criteria or documented conventions.

VERDICT: APPROVE
