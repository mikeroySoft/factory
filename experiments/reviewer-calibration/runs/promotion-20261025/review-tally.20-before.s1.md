Prerequisite gaps, stated up front:

- `./review.diff` and `./snapshot` do not exist in the working directory. I reviewed `git diff origin/main...HEAD` (commits `7fc5fe2`, `8dd6588`) in the checkout at HEAD `8dd6588` instead; line numbers below are post-image lines in that revision.
- Issue #20 is unreachable: `GH_CONFIG_DIR` is pointed at an empty dir and the API returns 403 (private repo). I did not bypass the sandbox with user credentials. Spec evidence is `docs/manager-plan.md` T11 (lines 341-352), which `docs/manager-plan.md:167` ties to "#20" (`[workers]` entry with a `when` rule once #20 lands). Acceptance criteria from the issue comments/agent brief were not read; findings against them are `[INFERENCE]` from T11.

## Standards
No AGENTS.md / CONTRIBUTING.md in the repo; README is the convention source. README §Commands/§How a ticket moves updated (`README.md:85-103`) consistently with the new decision. Config layering (`config.py:45-51` host tables) still works for table-form workers because `merge()` is recursive on dicts (`config.py:203-208`); template guidance at `factory/templates/factory.toml:37-39` matches that. Template keeps "defaults shown commented out" convention (`factory.toml:41-47`). No smell worth flagging; `RESERVED_LABELS` (`manage.py:31`) dedupes an existing literal set, not speculative.

## Spec (T11 / #20 [INFERENCE])
- `[workers.<label>]` table-or-array with `when`: `config.py:285-298`, validated; legacy arrays unchanged. ✔
- Manager prompt lists labels with `when`: `manage.py:199-201`. ✔
- ROUTE/FIX only for listed labels, reserved labels excluded: `manage.py:54,66-69`; `parse()` receives the filtered dict (`manage.py:213`). ✔
- FIX dispatches exactly one `worker_round` in the kept worktree with guidance as `extra`, re-gates, pushes only on PASS, re-reviews, `factory-approved` only on fresh APPROVE: `manage.py:104-131`. Bounded: a failed FIX calls `escalate`, which increments the round (`dispatch.py:369-372`), so default `manager_rounds=1` stops it. ✔
- Two profiles shipped in template, opt-in via host config; manager cannot write config: `factory.toml:41-47`. ✔
- Exit gates (ROUTE chore rejected/applied; FIX on red CI runs the `ci-fix` argv) covered by `tests/test_factory.py:652-686, 688-730, 732-742`; gate `test: PASS`.

## Plan drift
Plan status not recorded → nothing to diff against. Versus T11 "Touches" (`config.py`, `manage.py`, `dashboard.py`, `templates/factory.toml`): extra files are `README.md` and `tests/test_factory.py` (docs + tests; no defect). Gate report names no proof beyond `test: PASS`; the three T11 exit gates are exercised by the added tests, so no planned proof is missing.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/manage.py","line":127,"summary":"FIX success path (gate PASS + APPROVE) leaves no `Factory manager:` comment on the still-`ready-for-human` issue; the human owner gets no attributable trace that a FIX round ran and re-approved the PR (plan §4.4 attribution intent). Gate/REVISE paths do comment via escalate()."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":117,"summary":"FIX records a normal `attempt` event, but stats.worker_metrics attributes attempts by the ticket's claim labels, so `ci-fix` rounds are counted/costed under the original worker row and the `ci-fix` row in `stats --by-worker` stays empty."},
{"severity":"nit","pass":"bug","path":"factory/config.py","line":290,"summary":"Table-form worker entries ignore unknown keys (e.g. a `whne` typo) silently; KNOWN_KEYS['workers'] is None so `factory doctor` cannot report them either."}
]}
```
VERDICT: APPROVE
