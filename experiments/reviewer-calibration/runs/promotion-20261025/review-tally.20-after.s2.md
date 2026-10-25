**Spec trace** (issue #20, brief, exit gate):

- `[workers.<label>]` table with `command` + optional `when`, array form preserved — `config.py` diff lines 45–55; legacy arrays and tables coexist, validated argv; tested at `tests/test_factory.py` diff 236–254.
- Manager prompt lists routable labels with `when` — `manage.py` diff 176–178; reserved labels excluded.
- ROUTE/FIX accept only listed labels — `manage.py` diff 110–126, parse receives the filtered `workers` (diff 187). `ROUTE chore` without a `chore` worker → HUMAN recorded, no `issue edit` (test diff 263–286); with one → `--add-label chore`.
- FIX on red CI runs the selected argv in the kept `wt-<n>` under the ticket lock (manage_pass already holds `ticket_lock(n)`), re-gates via `worker_round`, pushes `--force-with-lease` only on gate PASS, re-reviews, `approve_pr` only on APPROVE, otherwise `escalate` (round increments via `escalation_packet`, bounded by `manager_rounds`). Merge stage (`merge_pass_locked`) keys off `factory-approved` + CI, not issue labels, so "does not requeue the issue" is consistent. Test diff 288–347 covers gate FAIL, REVISE, APPROVE, and rebase (`conflict`) paths including remote head assertions.
- `ci-fix`/`conflict` profiles shipped commented in `templates/factory.toml` (diff 218–224), host layering via `merge` (recursive dict) + `HOST_TABLES` includes `workers` so `[defaults.workers.<label>]` works as documented.
- All existing `cfg.workers` consumers (`onboard.py:223`, `dashboard.py:698`, `stats.py:316`) still receive `dict[label, argv]`.

**Standards**: no AGENTS.md/CONTRIBUTING.md in repo; `skills/factory/SKILL.md` invariants (workers push `agent/<n>` only; merge needs gate PASS + `factory-approved` + green CI) are preserved. README updated. `KNOWN_KEYS["workers"] = None` so `doctor` won't flag `when` as drift.

**Plan drift**: plan not recorded. Changed files beyond issue "Touches": `README.md` (docs for new decision/table form) and `tests/test_factory.py` (required by the exit gate's `uv run pytest tests/test_factory.py`). Gate report `test: PASS` covers that command. No drift exposes a defect.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":67,"summary":"`worker_when` is added to the snapshot config JSON but the dashboard config card (dashboard.html:894) still renders only `cfg.workers`; the `when` rules are exposed in `--json` yet invisible in the UI."},{"severity":"nit","pass":"spec","path":"README.md","line":15,"summary":"Table form is documented only in the manager paragraph; the Configuration reference block (README.md:176 `[workers]`) still shows array form only, so readers of the config section miss `when`."}]}
```
VERDICT: APPROVE
