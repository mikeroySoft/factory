# Review: issue #62 — manager prompt-file transport

## Spec coverage

| Criterion | Status | Evidence |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | Met (file written by caller, not `manager_cmd`; signature in the issue already takes a path, so this is the consistent reading) | `factory/config.py:146-151`, `factory/manage.py:263-265` |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` | Met | `factory/config.py:149-150`, test `tests/test_factory.py:150-165` |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate` event `reason: manager_failed` | Met | `factory/manage.py:267-273`, test `tests/test_factory.py:752-790` |
| `doctor` row PASS/FAIL/WARN | Met | `factory/onboard.py:226-238`, test `tests/test_factory.py:410-436` |
| README §Configuration + `init` template document file-vs-text | Met, with one inaccuracy (below) | `README.md:97-105`, `README.md:215-217`, `factory/templates/factory.toml:63-65` |
| `DEFAULT_MANAGER` becomes worker shape | Not in diff. `manage.py:262` (`cfg.manager or []`) and the doctor WARN path imply no default exists at this head, so there may be nothing to change — `[INFERENCE]`, not verifiable from the packet. | — |
| `manager_failed` events don't consume escalation rounds (round-1 reviewer fix) | Met | `factory/dispatch.py:376-378` |

## Required fixes

1. **Template/README assert worker-side rejection that does not exist, and the diff rewrites two worker profiles beyond the brief.**
   - `factory/templates/factory.toml:25`: "OMP file prompts require `@{prompt}`; bare `{prompt}` passes path text and is rejected." placed under `[workers]`. Only `Config.manager_cmd` (`factory/config.py:149-150`) and the doctor manager row (`factory/onboard.py:233-234`) perform this check; no worker validation is added. `README.md:99-100` makes the same claim in a sentence covering "worker and manager commands".
   - Trigger/impact: an operator reading the template believes a worker argv containing bare `{prompt}` fails fast; in fact it runs and `omp` treats the path as prompt text — the exact silent-misconfiguration class the issue is about, now documented as impossible for workers.
   - `factory/templates/factory.toml:44` and `:48`: the `ci-fix` and `conflict` profile commands were valid under the worker contract (`{prompt}` is a path embedded in instruction text; the agent reads the file) and carried the profile's behavioral instructions (fix-or-declare-flake-with-evidence; preserve both intents, restart aborted rebase). Replacing them with `@{prompt}` deletes those instructions. Brief item (4) asks only to document `{prompt}` = file for workers and manager; changing what the opt-in profiles instruct the worker to do is not in the brief and the README (§"How a ticket moves" step on FIX, `README.md` "The template includes opt-in `ci-fix` and `conflict` profiles…") still describes those profiles by their behavior.
   - Fix: either restore the original profile commands (they are correct), or keep the rewrite but make the comment say the check applies to `[manager]` only. Remove the "rejected" claim from `[workers]` and scope the README sentence to the manager.

## Optional suggestions

- `factory/manage.py:263-264`: the prompt file is written before `manager_cmd` runs the shape check, so a bare-`{prompt}` omp config leaves a `manager-prompt-<n>.md` behind each pass. Harmless (overwritten next time), but moving `write_text` after the `manager_cmd` call would avoid it. Not required.
- `factory/manage.py:267`: `json.dumps(cfg.manager)` posts the full argv to a GitHub comment. The issue asks for exactly this; just note that any token-bearing flags in a host's manager argv become public on the ticket. No change requested.
- `factory/onboard.py:229`: WARN text says `[manager].command`; issue text says `[dispatch.manager]`. Repo config uses `[manager]` (template, README), so the diff is right and the issue wording is stale.

VERDICT: REVISE
