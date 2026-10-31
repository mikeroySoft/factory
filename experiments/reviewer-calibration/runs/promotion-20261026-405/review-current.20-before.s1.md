I found no Important defects. The change meets the issue #20 exit gate:
- **ROUTE without a `chore` worker:** with no such worker, the ROUTE is rejected, a `HUMAN` decision is recorded and no label is applied. With one configured, `--add-label chore` is applied.
- **FIX:** it runs the selected `ci-fix` argv, pushes only on gate PASS, and restores `factory-approved` only after a fresh APPROVE.
- **Config:** the old array form of `[workers]` still loads, and `when` rules appear in the manager prompt.

**Spec:**
- **FIX is new in this diff.** The plan puts it in T6, which has not landed, but the exit gate requires it. It stays inside `manage.py`, which the issue names, and it is limited by `manager.rounds`: a failed FIX escalates through `dispatch.escalate`, which counts as a new round.
- **The `ci-fix` and `conflict` profiles are commented out in the template.** That is required here, not optional. The template only shows defaults as comments. An uncommented `[workers.ci-fix]` table with no `default` entry would raise `ConfigError`. Also, `onboard` flags workers committed in the repo file as settings that belong in host config. This fits the issue's rule that new profiles are host config applied by a human.

**Standards:** the repo has no AGENTS.md or CONTRIBUTING.md. The README describes the new FIX decision and the table form of `[workers]`. No skill doc needed changes.

**Plan drift:** no plan was recorded. Compared with the files the issue lists, the diff also changes `README.md` (docs) and `tests/test_factory.py` (the exit-gate tests). Both are expected, and nothing is missing from the gate report.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"spec","path":"factory/dashboard.py","line":67,"summary":"`worker_when` is added to the snapshot config, but `dashboard.html` never renders it, so the `when` rules are not visible on the dashboard."},{"severity":"nit","pass":"bug","path":"factory/manage.py","line":162,"summary":"FIX posts no issue comment. Every other applied decision comments `Factory manager: ...`. After a successful FIX with APPROVE, the issue stays `ready-for-human` with only the PR findings comment, so a human reading the issue cannot see that the manager ran a FIX round."},{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":216,"summary":"The comment 'Keep a default worker in that layer' is stricter than needed. `config.merge` merges `workers` across `defaults`, `repo` and the repo file, so a `default` in any layer is enough."}]}
```
VERDICT: APPROVE
