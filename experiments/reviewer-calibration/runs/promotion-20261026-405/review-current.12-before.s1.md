The diff covers the issue #12 exit gate. Verdict is REVISE because of one correctness defect: the new loader mishandles a string-form `manager.command`, which the existing `manager_model()` already accepts.

**Important (bug), `factory/config.py:286`.** Today `manager_model()` accepts `manager.command` as either an argv array or a string (`config.py:231-237`). Its error message even says "argv array or command string". The new line `cfg.manager = list(manager["command"])` does not handle the string form, so `list()` breaks a string into single characters.
- **Trigger:** `.factory.toml` contains `[manager]` with `command = "omp -p --model x/y"`. That passes the existing check.
- **Doctor:** `cfg.manager == ['o','m','p',' ',...]`, so `factory doctor` prints a false `manager: o` FAIL row (`onboard.py:227`). A doctor that used to pass now exits 1.
- **Dashboard:** `dashboard --json` shows `"o m p   - p ..."` (`dashboard.py:841`).
- **Fix:** handle `command` the same way in both places: either run strings through `shlex.split` here too, or reject anything that isn't a list. No new abstraction is needed.

**Spec check (issue #12 exit gate):**
- **Loader:** all three keys (`command`, `rounds`, `review`) are loaded, and a loader test covers them (`tests/test_factory.py:112-115,137-138`).
- **Doctor row:** appears when the manager is configured and is absent when it isn't (`onboard.py:226-227`, test at `tests/test_factory.py:276-284`).
- **Drift:** an unknown key under `[manager]` is reported (`tests/test_factory.py:252,263`).
- **Dashboard:** `dashboard --json` `config` includes the table (`dashboard.py:840-844`).
- **Template:** `templates/factory.toml` documents the table (`templates/factory.toml:43-48`).
- **Host table:** `HOST_TABLES` already contained `manager`, so leaving it unchanged is correct.
- **Out of scope respected:** the command is never run.

**Plan drift:** the plan wasn't recorded. All changed files are named in the issue's Touches list or Exit gate (`tests/test_factory.py`). No planned proof is missing from the gate report.

**Standards:** the snapshot has no `AGENTS.md` or `CONTRIBUTING.md`, and `docs/` contains only `manager-plan.md` plus site assets. The diff follows the loader's existing patterns.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/config.py","line":286,"summary":"`list(manager[\"command\"])` breaks a string-form manager.command into characters, even though manager_model() (config.py:231-237) accepts that form ('argv array or command string'). Trigger: `[manager] command = \"omp -p --model x/y\"`. Impact: doctor prints a false `manager: o` FAIL row and exits 1 (onboard.py:227); dashboard --json shows `\"o m p ...\"` (dashboard.py:841). Fix: handle command the same way in both places (shlex.split strings here too, or reject non-list)."},{"severity":"nit","pass":"spec","path":"factory/config.py","line":288,"summary":"manager.review is not checked against the issue's allowed values (\"escalated\"/\"all\"), so a typo like \"escalted\" is silently accepted and shown in dashboard --json."},{"severity":"nit","pass":"spec","path":"tests/test_factory.py","line":282,"summary":"The 'nothing when not configured' check only asserts that the exact label 'manager: manage' is absent, not that no 'manager:' row is printed, which is the issue's exit-gate wording."}]}
```
VERDICT: REVISE
