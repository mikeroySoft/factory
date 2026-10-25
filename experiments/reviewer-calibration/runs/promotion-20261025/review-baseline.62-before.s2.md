## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `docs/` holds plans, not coding rules. `CHANGELOG.md` is release-compiled (`## 0.3.0 — …`, "Compared against 0.2.0"), no `Unreleased` section, so no per-PR entry is required. Nothing in the diff breaches a documented convention.

Judgement-call smells (non-blocking):

- **Duplicated Code** — the `omp` + bare `{prompt}` rule lives twice: `factory/config.py:59-60` (diff) and `factory/onboard.py:153-154` (diff). Doctor could call `cfg.manager_cmd(Path("p"), Path("c"))` under `except ConfigError` and get the same message for free. Two copies will drift the first time the rule changes (e.g. `-f {prompt}`).

## Spec (issue #62 + agent brief)

Acceptance criteria, each checked against the diff:

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a file path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py:56-61`, `factory/manage.py:114-116` | met (writing moved to the caller, mirroring `Config.worker`; the issue's signature cannot carry the text anyway) |
| Bare `{prompt}` element with argv[0] `omp` → `ConfigError` naming `@{prompt}`; other programs untouched | `factory/config.py:59-60`; test `tests/test_factory.py:224-226` | met |
| Default manager command = worker shape | README `README.md:40-41`, template `factory/templates/factory.toml:199` (there is no `DEFAULT_MANAGER` constant in `config.py`; manager stays `None` by default) | met |
| `doctor` row: PASS / FAIL naming placeholder or executable / WARN when unset | `factory/onboard.py:145-157`; test `tests/test_factory.py:244-270` | met |
| Nonzero exit → `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines; `escalate` event `reason: manager_failed` | `factory/manage.py:119-125`; test `tests/test_factory.py:313-322` | met |
| README + template document file for workers/manager, text for reviewer | `README.md:24-27`, `factory/templates/factory.toml:168-169,187,197-198` | met |
| Exit-gate tests (`@<path>` readable, ConfigError, 5-line cap, doctor rows) | `tests/test_factory.py:211-226, 244-270, 278-297, 299-331` | met |
| Manual District #26 replay | not in gate report; handoff cites a prior attempt's `.factory/district-26-replay.log` | **unverified here** (see drift) |

Correctness checks I traced and found sound:

- Round replay after `manager_failed`: `manage.py:237-244` picks the newest `escalate` (the failure row, same `round`), the `manage` row for that round blocks replay; `dispatch.py:72-76` keeps the next real escalation at round 2. Test `tests/test_factory.py:323-331` covers it.
- `record("escalate")` inside the `manage` lifecycle scope sets `outcome=project_escalation` (`dispatch.py:245-248`); `lifecycle.py:591-592` treats that as a completed mechanism, not a failure. Acceptable.
- `ConfigError(SystemExit)` is caught at `manage.py:131`, so a stale host config no longer aborts the whole pass; `learn.py:101` leaves it uncaught, which is the normal CLI exit path for config errors.

### Optional suggestions (non-blocking)

1. **`factory stats` now double-counts escalations.** `stats.py:167-171` counts every `escalate` row; a ticket whose manager failed once reports `escalation_count` 2 and bumps `escalations_per_week`. The issue mandates the `escalate` event "so `factory stats` can count it", so the AC is literally met, but counting it *as an escalation* is almost certainly not what the author meant. Excluding `reason == "manager_failed"` in `stats.human_touch` (same predicate as `dispatch.py:74`) or reporting it as its own column would make the metric honest. Not raised to required because the spec prescribes the event name and is silent on stats semantics.
2. **Template worker examples lost their instructions.** `factory/templates/factory.toml:177-178,182-183` replace the `ci-fix`/`conflict` example commands (inline text that told the worker "declare a flake with evidence", "restart the rebase if aborted") with bare `@{prompt}`. Those commands were valid omp invocations and the only place that guidance existed — `worker_when` is fed to the manager menu (`manage.py:252-253`), never to the worker prompt (`dispatch.py:200-226`). The brief asked only to document `{prompt}` = file for workers. Suggest reverting these two lines.
3. **Doc overstatement.** `README.md:26-27` and `factory/templates/factory.toml:169` say a bare `{prompt}` "is rejected" in a paragraph/section covering workers; only `manager_cmd` rejects it (`config.py:59`). `Config.worker` (`config.py:138-141`) does no such check. Reword to "rejected for the manager" or drop the clause from the `[workers]` comment.
4. Deduplicate the omp check as noted under Standards.

## Plan drift

Plan status: not recorded, so comparison is against the issue's "Touches" list.

Files changed beyond "Touches":
- `factory/dispatch.py:72-76` — required consequence of the new `escalate` row (round arithmetic); approved in the round-1 reviewer feedback recorded in the issue handoff comment.
- `factory/learn.py:87,97-101` — second caller of `manager_cmd`; mandatory after the signature change. (`doctor` lives in `factory/onboard.py`, not the issue's `factory/doctor.py`; same component.)

Planned proof missing from the gate report:
- The manual District #26 kept-packet replay (issue exit gate, last bullet). The handoff says a *previous* attempt produced `DECISION: FIX` and this revision did not repeat it. The gate report covers only conflict-markers/test/leak-scan. The code path it would exercise is covered by `tests/test_factory.py:278-297` with a stub `omp`, so I do not treat this as exposing a defect; it remains an unverified claim.

Neither drift item exposes a required defect.

### Required fixes

None.

VERDICT: APPROVE
