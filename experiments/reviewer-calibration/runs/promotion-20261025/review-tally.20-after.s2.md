**Standards.** No AGENTS.md/CONTRIBUTING.md in the snapshot; the governing docs are `docs/manager-plan.md` (§4.3/§4.4, T11), `skills/factory/SKILL.md`, README. Decide→record→act order kept (`manage.py:217-220`); `Factory manager:` prefix unchanged; the manager gets no shell and cannot write config (template comments, `manage.py:104-131`).

**Spec (issue #20 / brief).**
- `when` on `[workers.<label>]` tables, array form still loads: `config.py:288-298`, test `test_worker_tables_and_legacy_arrays`. ✔
- Prompt lists labels + `when`; ROUTE/FIX reject unlisted labels: `manage.py:199-201`, `manage.py:66-68`, `parse(..., workers)` at `manage.py:210`. ✔ ROUTE chore without a chore worker → HUMAN recorded + comment; with one → label applied (tests at diff 263-286). ✔
- FIX on red CI runs the selected worker argv in the kept worktree, re-gates, pushes only on PASS, re-reviews, re-labels only on APPROVE: `manage.py:104-131`. ✔ for the red-CI path (label was already removed in `merge_pass_locked`).
- `ci-fix`/`conflict` profiles shipped commented-out in the template (`templates/factory.toml:41-47`) as human-applied host config — matches the brief's "remain host-config that humans apply".

**Defect traced end to end.** The diff's FIX targets every escalation with a kept worktree and open PR, and ships a `conflict` profile for the `refresh_pr_branch` rebase-conflict escalation. That path (and "gate failed after rebase") never removes `factory-approved` (`dispatch.py` removes it only on red CI, lines 833-834). So: approved PR → main moves → rebase conflicts → escalate (label kept) → manager FIX `conflict` → worker commits → gate PASS → `git push --force-with-lease` (`manage.py:124`) → review REVISE → `escalate` (`manage.py:130`) with the label still on the PR. Next `merge_pass_locked` sees `factory-approved` + green CI + up-to-date head and merges commits the reviewer rejected. Violates plan §4.3 ("after gate PASS + fresh codex APPROVE"), SKILL.md invariant (merge needs `factory-approved` earned), and the diff's own README claim ("Only a fresh APPROVE restores `factory-approved`"). One-line fix: remove `factory-approved` from the PR before the worker round/push.

**Plan drift.** No plan recorded. Files outside the issue's Touches: `README.md`, `tests/test_factory.py` — both expected, no defect. Gate ran `python -m unittest discover -s tests` (per `.factory.toml`), equivalent to the issue's `pytest tests/test_factory.py`.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/manage.py","line":124,"summary":"FIX force-pushes worker commits to a PR that may still carry `factory-approved` (rebase-conflict / gate-after-rebase escalations never remove it; only red CI does, dispatch.py:833). On REVISE (line 130) the label stays, so merge_pass_locked merges reviewer-rejected commits once CI is green. Violates docs/manager-plan.md §4.3 (re-add only after gate PASS + fresh APPROVE), SKILL.md merge invariant, and the README text added in this diff. Fix: `gh pr edit agent/<n> --remove-label factory-approved` before the worker round (or before the push)."},
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":128,"summary":"FIX posts no `Factory manager:` comment on the issue; the decision and guidance are visible only in events.jsonl and the worker prompt. On APPROVE the ticket stays `ready-for-human` with no issue-visible trace (plan §4.4 attribution-by-prefix convention)."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":110,"summary":"FIX precondition failures (no worktree, no PR, CHANGES_REQUESTED, wrong branch) raise ValueError, consume the manager round, and are logged only to the journal; no diagnosis reaches the issue. Same for `gh pr view` CalledProcessError when no PR exists (e.g. FIX chosen for a gate-failed-thrice escalation)."},
{"severity":"nit","pass":"bug","path":"factory/config.py","line":290,"summary":"Table-form worker entries ignore unknown keys (e.g. a misspelled `when`), and KNOWN_KEYS['workers'] is None so `factory doctor` cannot flag them; a typo silently drops the rule from the manager prompt."}
]}
```
VERDICT: REVISE
