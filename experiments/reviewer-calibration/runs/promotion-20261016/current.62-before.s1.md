## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in this repo; standards sources are `README.md`, the `factory init` template, and `pyproject.toml` (`[tool.ruff] line-length = 100`, not enforced by `ci.yml`, already exceeded across `manage.py` — skipped).

**Required**

1. **Template regresses the `ci-fix`/`conflict` worker profiles — out of brief, loses behavior.** `factory/templates/factory.toml:44` and `:48` replace the profile commands (`"Read the ticket prompt at {prompt}. Read the failing CI job log. Fix the cause, or declare a flake …"` / `"… Resolve the rebase conflicts, preserving both sides' intents …"`) with bare `["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]`. Those commands were not bare-`{prompt}` and were never affected by the ENAMETOOLONG defect (`Config.worker`, `config.py:138-141`, has no omp check; the path was interpolated into prompt text). The `when` text (`config.py:103`) is never forwarded to the worker — it is only read by `manage.py:253` and `dashboard.py:871` — so after this change a human who uncomments a profile per README:119-120 ("opt-in `ci-fix` and `conflict` profiles") gets a worker indistinguishable from `default`: no instruction to read the CI log, demand flake evidence, or preserve both intents during a rebase. Brief item (4) asked only to *document* `{prompt}` = file for workers/manager. Restore the two original commands (they remain valid under the new doc text) or justify the removal.

**Optional**

- Duplicated omp check: `config.py:149-150` and `onboard.py:234-235` carry the same `Path(argv[0]).name == "omp" and "{prompt}" in argv` predicate with different messages. One source (e.g. doctor catches the `ConfigError` from `manager_cmd`, or a tiny shared predicate in `config`) keeps the two from drifting.
- `factory/templates/factory.toml:25` ("bare `{prompt}` passes path text and is rejected") and `README.md:99` claim rejection for workers too; nothing rejects a worker command (`config.py:138-141`). Say "rejected for the manager" or "omp treats the path as prompt text".
- `CHANGELOG.md:10` ("`factory doctor` reports the manager only when configured") is now false; a line under a new heading would keep the docs coherent. No documented rule requires it.

## Spec

Checked against issue #62 "After this change" + "Exit gate", the agent brief, and the round-1 handoff (ConfigError containment, round arithmetic).

Satisfied:
- `manager_cmd(prompt_path, cwd)` expands a path (`config.py:146-151`); prompt lands at `<factory>/manager-prompt-<n>.md` (`manage.py:263-264`); `learn.py:64-68` adapted to the new signature.
- Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}` (`config.py:149-150`); other programs untouched; caught per ticket (`manage.py:278`) — approved scope change.
- `factory doctor` `manager command` row: WARN unset / FAIL naming placeholder or executable / PASS (`onboard.py:226-238`).
- Nonzero exit → `Manager command exited N (argv: <unexpanded>)` + last 5 stderr lines (`manage.py:267-270`); `escalate` event `reason: manager_failed` (`manage.py:272-273`); excluded from round arithmetic (`dispatch.py:375-378`) — approved scope change.
- README §Configuration and template document file-for-workers/manager, text-for-reviewer (`README.md:97-105`, `:215-216`; template `:63-65`).
- Exit-gate tests present: `tests/test_factory.py:150-165`, `:410-436`, `:730-798`.

**Optional**

- `manage.py:268` reads only `proc.stderr`; the previous body fell back to `proc.stdout`. A CLI that prints its error to stdout now yields a one-line body with no diagnosis. Issue text says "stderr tail", so this is as specified; a `or proc.stdout` fallback would be one token.
- `manage.py:272` goes through `dispatch.record`, which sets the enclosing `manage` lifecycle execution to `outcome=project_escalation, reason=manager_failed` (`dispatch.py:245-248`). A crashed manager binary is closer to the `mechanism_failure` the `apply` failure branch uses (`manage.py:292`). Not required by the issue; flagging for intent.
- Manual District #26 replay is asserted only in the prior handoff comment; nothing in the diff evidences it. `[INFERENCE]` from comment 2.

Standards: 1 required (template profile regression), 3 optional. Spec: 0 required, 3 optional.

VERDICT: REVISE
