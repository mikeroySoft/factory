Note: `./review.diff` and `./snapshot` were absent; reviewed `git diff origin/main...HEAD` (merge-base `07913b4`, head `ce5568e`) in the checkout, and issue #20 via the GitHub web page (no `GH_TOKEN`).

## Standards
- Repo has no AGENTS.md/CONTRIBUTING.md; `pyproject.toml:33-34` sets ruff `line-length = 100` but neither CI (`.github/workflows/ci.yml:11`) nor the gate runs ruff, and 38 pre-existing lines already exceed it. New lines `factory/config.py:292` (130 cols), `factory/manage.py:109-110` exceed. Nit only.
- `factory doctor`'s `unknown_keys` (`factory/config.py:58`, `workers: None`) skips worker tables, so a misspelled key inside `[workers.<label>]` (e.g. `whne`) is silently dropped. The loader does raise on a missing/invalid `command` (`config.py:292-295`). Nit.

## Spec (issue #20 / plan T11)
- `when` on `[workers.<label>]`, table-or-array coexistence, legacy arrays still load: `config.py:288-298`, tested `tests/test_factory.py:147-165`.
- Manager prompt lists labels with `when`: `manage.py:199-201`. ROUTE/FIX restricted to listed, non-reserved labels: `manage.py:54-67`; `parse` receives the filtered map (`manage.py:213`).
- Exit gate: ROUTE chore without worker → HUMAN recorded, with worker → label applied (`tests/test_factory.py:652-676`); FIX on red CI runs the `ci-fix` argv, pushes only on gate PASS, approves only on fresh APPROVE (`manage.py:104-131`, `tests/test_factory.py:678-731`).
- Profiles: `templates/factory.toml:41-47` ship `ci-fix`/`conflict` as commented host-config examples; unlike `default`/`chore` they have no code default in `Config.workers`. The issue's "ship two default profiles" vs "remain host-config, human-applied" is ambiguous; the worker chose the inert reading. Acceptance criteria still hold when configured. Nit.
- Out of scope respected: manager never writes config.

## Plan drift
- Plan status not recorded; against the issue's "Touches" list the diff additionally changes `README.md` and `tests/test_factory.py` (docs and tests for the feature; not a defect).
- Issue verification `uv run pytest tests/test_factory.py`: gate report shows `test: PASS` without naming the command; no planned proof otherwise identifiable.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":41,"summary":"ci-fix/conflict are commented examples with no Config default (unlike default/chore); if 'ship two default profiles' meant active defaults, FIX ci-fix is rejected as unlisted until a human copies them into host config."},
{"severity":"nit","pass":"bug","path":"factory/config.py","line":58,"summary":"unknown_keys skips [workers.<label>] tables (KNOWN_KEYS['workers']=None), so a misspelled `when` key is silently ignored and factory doctor does not report it."},
{"severity":"nit","pass":"spec","path":"factory/config.py","line":292,"summary":"130-col line exceeds pyproject [tool.ruff] line-length=100 (also manage.py:109-110); not tool-enforced in CI/gate and widely pre-existing."}
]}
```
VERDICT: APPROVE
