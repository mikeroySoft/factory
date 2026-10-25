## Standards

No AGENTS.md/CONTRIBUTING.md in the snapshot; conventions come from README.md, `docs/manager-plan.md` (T11/T6), and `templates/factory.toml`.

- `factory/config.py:285-298` — table-or-array `[workers]` matches T11 "config.py (`[workers]` becomes table-or-array)"; host layering via `merge()` (config.py:203) deep-merges tables and replaces arrays, so `[defaults.workers.ci-fix]` composes with a repo `[workers]`.
- `factory/manage.py:102,139-166` — FIX follows T6's contract verbatim: one `worker_round` in kept worktree with guidance as `extra`, re-gate, push only on PASS, re-review, `factory-approved` only on fresh APPROVE. Failure paths go through `escalate` (new round, bounded by `manager_rounds`), consistent with "A partial … must not be replayed".
- `factory/manage.py:176-178` — prompt lists only routable labels with `when`; `parse` receives the filtered dict, so ROUTE/FIX cannot name `default`/reserved labels.
- Nit: README `[workers]` reference block (README.md:176-178, unchanged) still shows array form only; table form is documented only inside the manager paragraph (README.md:93-95).

## Spec (issue #20 / brief)

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`, prompt lists labels with `when` | config.py:291-298; manage.py:176-178 | met |
| ROUTE/FIX accepted only for listed labels | manage.py:111-115,120-123 | met |
| `ROUTE chore` without chore worker rejected and recorded; with one, label applied | tests:263-286 — HUMAN recorded via `record("manage")` + issue comment; `--add-label chore` otherwise | met |
| FIX on red CI runs the `ci-fix` argv | tests:288-330; escalation produced by dispatch.py:838-843 (CI fail) → manage → `worker_round({"ci-fix"})` | met |
| `ci-fix` and `conflict` profiles in template, human-applied | templates/factory.toml:41-47 | met |
| Array-form `[workers]` still loads | tests:236-248 | met |
| Manager not writing host config | nothing in diff writes config | met |

Trace: red CI → `land_pass` removes `factory-approved`, `escalate(n, …)` round 1 → `manage_pass` builds prompt → FIX → precondition (kept `wt-n`, PR OPEN on `agent/n`, no CHANGES_REQUESTED, branch check) → `worker_round` → gate → push → `review` → `approve_pr`. Issue stays `ready-for-human`; merge stage then needs green CI + head contains main. Coherent with README.md:21-23.

## Plan drift

- Files not named in issue Touches: `README.md` (documents new decision; appropriate), `tests/test_factory.py` (required by exit gate `pytest tests/test_factory.py`). Neither exposes a defect.
- Planned proof: `uv run pytest tests/test_factory.py` → gate `test: PASS`. No missing proof.

## Nits

- `factory/templates/factory.toml:39` — the template directs humans to put profiles in host config, but a repo with no `[workers]` table then loads host `{ci-fix: …}` alone and fails `needs a default command` (config.py:286-287) for every `factory` command. The comment warns; a friendlier path would be to fall back to built-in `default`/`chore` when the merged table lacks `default`. Pre-existing check, newly reachable by the documented workflow.
- `README.md:176` — `[workers]` config reference still shows only array form.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":39,"summary":"Host-only profile without a repo [workers] table makes config.load raise 'needs a default command' for every command; consider falling back to built-in default/chore when the merged table lacks default."},{"severity":"nit","pass":"spec","path":"README.md","line":176,"summary":"[workers] config reference still shows only the array form; table form with `command`/`when` is documented only in the manager paragraph."}]}
```

VERDICT: APPROVE
