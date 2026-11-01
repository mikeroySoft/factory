I'm approving this. I found no important defects; the four nits are below.

**Spec (issue #20, plan T11):**
- **`when` rules:** `[workers.<label>]` tables with `command` and an optional `when` are loaded and checked in `factory/config.py:288-298`. Array-form workers still load, and `tests/test_factory.py:146-165` covers both forms plus invalid entries.
- **Manager prompt and label checks:** the prompt lists each worker label with its `when` rule (`factory/manage.py:199-201`). ROUTE and FIX are checked against that same list, which excludes `default` and the pipeline labels (`factory/manage.py:54`, `factory/manage.py:63-69`, `factory/manage.py:213`).
- **ROUTE without a `chore` worker:** with only a `default` worker, ROUTE to `chore` falls back to HUMAN and a `manage` event is recorded. With a `chore` worker, the label is applied. Test: `tests/test_factory.py:263-286`.
- **FIX on red CI:** FIX runs the selected `ci-fix`/`conflict` command in the kept worktree. It pushes only after a gate PASS and restores `factory-approved` only after a fresh APPROVE. Test: `tests/test_factory.py:288-347`.
- **Template profiles:** `ci-fix` and `conflict` are in the template but commented out, for a human to copy into host config. That fits the brief's "remain host-config, human-applied" scope. The manager cannot write config.
- **Safety:** FIX runs under the ticket lock already held by `manage_pass`. It needs an open `agent/<n>` PR without requested changes. Further FIX rounds are capped by `manager.rounds`, because each failed FIX escalation becomes a new round.

**Standards:** the README is updated (`README.md:93-102`). There is no AGENTS.md or CONTRIBUTING.md; nothing in `docs/` is violated. The one new constant, `RESERVED_LABELS`, removes a duplicated set and is shared by `parse` and `manage_pass`, so it is justified.

**Plan drift:** no plan was recorded. Compared with the issue's Touches list, two extra files changed: `README.md` (docs) and `tests/test_factory.py` (the exit-gate tests). Neither exposes a defect. The issue's planned proof (`uv run pytest tests/test_factory.py`) is covered by the gate's `test: PASS`.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/manage.py","line":124,"summary":"When ci-fix declares a flake and makes no commits, the gate passes and the push changes nothing, so GitHub CI never reruns. approve_pr then restores factory-approved, the next merge pass sees the same failed checks, strips the label and escalates again. The 'declare a flake' route (factory/templates/factory.toml:43) therefore always ends with the human."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":122,"summary":"worker_round returns ok=False with the report 'budget exceeded before gate' when the deadline passes, but FIX escalates it as 'gate failed after manager FIX', which misstates the cause in the packet and issue comment."},{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":39,"summary":"'Keep a default worker in that layer' overstates the requirement. config.load checks for `default` after merging host and repo layers (factory/config.py:285-287), so a default in any layer is enough."},{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":847,"summary":"worker_when is added to the dashboard snapshot, but dashboard.html never reads it (the workers card renders only cfg.workers), so the dashboard does not show the rules."}]}
```
VERDICT: APPROVE
