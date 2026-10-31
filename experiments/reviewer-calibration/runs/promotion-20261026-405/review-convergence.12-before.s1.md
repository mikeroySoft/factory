Issue #12 isn't met yet. One important defect: a `[manager] command` written as a string gets split into single characters. The issue's other requirements are covered: the three keys load with defaults, doctor shows the row only when configured, unknown keys are reported as drift, the dashboard JSON includes the table, the template documents it, and the loader test covers all three keys.

**Spec (issue #12 and its agent brief)**
- `manager` was already in `HOST_TABLES` in the snapshot (`factory/config.py:50`), so the diff didn't need to touch it.
- The diff doesn't run the command, which keeps it inside the "Out of scope: running the command" line.

**The defect.** The new loader code at `factory/config.py:286` does `cfg.manager = list(manager["command"])`. The existing `manager_model()` in the same file (snapshot lines 231–237) accepts `command` as either an argv array or a single string (its error message says "argv array or command string"). Given a string such as `command = "omp -p --model x"`, `list(...)` returns `['o','m','p',' ',...]` and doesn't raise.
- **Doctor:** `factory/onboard.py:227` then checks a binary named `o`. That row comes out `manager: o` with status FAIL, and doctor exits 1 on a config the loader still accepts.
- **Dashboard:** `factory/dashboard.py:52` shows the command as `"o m p   - p …"`.
- **Next stage:** the planned step that runs the command (T4 in `docs/manager-plan.md`) would inherit the broken argv.
- **Fix:** either split string commands with `shlex`, as `manager_model` already does, or reject anything that isn't a list of strings. Both are one-line changes.

**Standards.** The snapshot has no `AGENTS.md` or `CONTRIBUTING.md`, and `docs/` only holds the plan. The new code follows the same patterns as the existing `review`/`workers` settings, and adds no new abstractions.

**Plan drift.** No worker plan was recorded. Every changed file is either named in the issue's "Touches" list (`config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`) or is the test file the issue asks for. The gate ran the required test. Nothing is missing.

```json
{"verdict":"REVISE","findings":[{"id":"F1","severity":"important","pass":"bug","class":"string-command-split-into-chars","path":"factory/config.py","line":286,"summary":"`list(manager[\"command\"])` turns a string-form manager.command (which manager_model in this module accepts as 'argv array or command string') into a list of single characters. Example: `command = \"omp -p --model x\"` gives cfg.manager == ['o','m','p',...]. Doctor then checks binary `o`, reports `manager: o` FAIL and exits 1 on a config the loader accepts. dashboard --json shows a garbled command. The future manage stage would inherit the bad argv. Fix: shlex-split strings like manager_model does, or raise ConfigError for anything that isn't a list of strings.","instances":[{"path":"factory/config.py","line":286,"summary":"list() applied to a possibly-string command"},{"path":"factory/onboard.py","line":227,"summary":"doctor reports the first character as the manager binary, giving a false FAIL and exit 1"},{"path":"factory/dashboard.py","line":52,"summary":"dashboard config joins the characters into a garbled command string"}]},{"id":"F2","severity":"nit","pass":"spec","class":"unvalidated-enum","path":"factory/config.py","line":288,"summary":"manager.review accepts any value. The issue limits it to \"escalated\" or \"all\", so a typo such as \"All\" loads silently and only shows up when a later stage reads it. A check that the value is one of the two allowed strings would catch it at load or doctor time.","instances":[{"path":"factory/config.py","line":288,"summary":"review value read without checking it against escalated/all"}]}]}
```
VERDICT: REVISE
