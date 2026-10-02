# Review: issue #62 — manager prompt-file transport

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path | `factory/config.py:146-151`; caller writes `<factory>/manager-prompt-<n>.md` at `factory/manage.py:263-265` | Met |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:149-150` (exact-element match, argv[0] basename `omp` only) | Met |
| Doctor `manager command` row: PASS / FAIL (placeholder, executable) / WARN (unset) | `factory/onboard.py:226-238`; cases in `tests/test_factory.py:410-436` | Met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + ≤5 stderr lines, `escalate` event `reason: manager_failed` | `factory/manage.py:267-273`; asserted at `tests/test_factory.py:764-772` | Met |
| README §Configuration + `factory init` template document file/text split | `README.md:97-100`, `README.md:215-216`; `factory/templates/factory.toml:25`, `:50`, `:63-65` | Met (see Required 1 on wording) |
| Exit-gate tests (argv `@<path>` readable; ConfigError; HUMAN body; doctor rows) | `tests/test_factory.py:150-165`, `:730-751`, `:753-785`, `:410-436` | Met |
| Round arithmetic excludes `manager_failed` (round-1 reviewer fix) | `factory/dispatch.py:376-377`; asserted `tests/test_factory.py:775-785` | Met |
| `ConfigError` contained per ticket, no replay (round-1 reviewer fix) | `factory/manage.py:278-279`; `tests/test_factory.py:787-799` | Met |

Note on the brief's `DEFAULT_MANAGER` item: the diff changes no such constant. The doctor WARN case loaded via `config.load` with no `[manager]` yields a falsy `cfg.manager` (`tests/test_factory.py:412`, `onboard.py:226`), so no code default is applied; the template/README defaults were updated instead (`factory/templates/factory.toml:65`, `README.md:216`). `[INFERENCE]` that no `DEFAULT_MANAGER` argv exists in `config.py`; if one does and still carries bare `{prompt}`, `manager_cmd` would now raise for every default-configured host — worker should confirm.

## Required fixes

1. **Docs claim a worker-side rejection the diff does not implement.**
   - `factory/templates/factory.toml:25` (under `[workers]`): "bare `{prompt}` passes path text **and is rejected**."
   - `README.md:97-98`: under "Worker and manager commands … bare `omp {prompt}` … **is rejected**."
   - The only rejection added is in `Config.manager_cmd` (`factory/config.py:149-150`); nothing in the diff validates worker argv, and `doctor` checks workers only for PATH resolution (unchanged context at `factory/onboard.py:223-224`). `[INFERENCE]`: no pre-existing worker check, since the issue's premise is that `config.py` accepts any argv.
   - Defect: operator configures `default = ["omp", "-p", "--cwd", "{cwd}", "{prompt}"]`, expects the documented rejection, gets a worker that silently receives a path as its prompt — the exact failure class this issue fixes, now with docs that say it can't happen.
   - Fix: scope the sentence to the manager (e.g. "…passes path text; `manager_cmd` rejects it") or drop the "is rejected" clause from the `[workers]` comment and README worker sentence. Alternatively add the same check to `Config.worker` — but that is beyond the brief and not required.

## Optional suggestions

- `factory/onboard.py:233` duplicates the `Path(argv[0]).name == "omp" and "{prompt}" in argv` predicate from `factory/config.py:149`. A single helper on `Config` would keep doctor and runtime in lockstep; two copies can drift. Not blocking.
- `factory/manage.py:264` `write_text` and `factory/learn.py:65` `NamedTemporaryFile(mode="w")` use locale encoding. If the packet/issue body contains non-ASCII under a `C` locale (systemd user units often are), this raises `UnicodeEncodeError` before the manager runs. `encoding="utf-8"` on both is cheap. Only flag if the existing worker `.factory-prompt.md` write does the same — match whichever convention it uses.
- `factory/learn.py:66-68`: a `ConfigError` from `manager_cmd` propagates out of `ask`. Fine if `factory learn`'s CLI entry already catches `ConfigError` from `config.load`; otherwise `learn` with a bare-`{prompt}` omp manager shows a traceback instead of the one-line fix. Not an acceptance criterion.
- The new `escalate` row (`factory/manage.py:272-273`) will be picked up by any `stats` trace-escalation count that filters on `event == "escalate"` without inspecting `reason`. The issue asked for this to be countable, so this is expected; worth a glance that it doesn't double an existing ticket escalation in the ticket table.

VERDICT: REVISE
