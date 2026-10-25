## Evidence basis

`./review.diff` and `./snapshot` are absent from the working directory (`ls` shows only the repo; the only `review.diff` on disk, `/tmp/factory-review-zo3x3zy7/`, is a different repo). Diff reconstructed from git: base `6ec3546` (origin/main) → gated head `4830b7d` (HEAD, merge of `agent/12`). Line cites below are snapshot lines at HEAD. Issue #12 fetched from GitHub (closed, label `ready-for-agent`; one triage comment with the agent brief, no scope-change comments).

## Standards

No `AGENTS.md`/`CONTRIBUTING.md` in repo; standards sources are `docs/manager-plan.md` (T3) and existing patterns in `factory/config.py`.

- `factory/config.py:60,104-106,269,285-288` — follows the `review` table pattern exactly (`KNOWN_KEYS` entry, dataclass fields, loader `raw.get(...)` + `list(...)`). `HOST_TABLES` already contained `"manager"` at base (`config.py:50`), so no change needed there.
- `factory/onboard.py:226-227` — mirrors the `reviewer:` row one line above. Conditional on `cfg.manager` satisfies "nothing when not".
- `factory/templates/factory.toml:40-48` — documents all three keys plus placeholders.
- Smell baseline: nothing notable. No new abstractions introduced.

## Spec (issue #12 exit gate + brief)

| Criterion | Status | Evidence |
|---|---|---|
| `command` argv template, unset = disabled | met | `config.py:104,285-286`; `onboard.py:226` |
| `rounds = 1` default | met | `config.py:105,287` |
| `review = "escalated"`, `"all"` allowed | met (unvalidated, see Optional) | `config.py:106,288` |
| Host-layer table in `HOST_TABLES` | met (pre-existing) | `config.py:50` |
| `doctor` prints `manager:` row only when configured | met + tested | `onboard.py:226-227`; `tests/test_factory.py:276-285` |
| Unknown `[manager]` keys reported as drift | met + tested | `config.py:60`; `tests/test_factory.py:252,263` |
| `dashboard --json` `config` includes it | met, untested | `dashboard.py:840-844` |
| Template documents it | met | `templates/factory.toml:40-48` |
| Loader test for all three keys | met | `tests/test_factory.py:112-115,137-138` |
| Out of scope: running the command | respected | no exec path added |

### Required fixes

1. **String-form `manager.command` is mangled into a character list.** `factory/config.py:286` does `cfg.manager = list(manager["command"])`. The same table's existing validator `manager_model()` (`config.py:231-233`) explicitly accepts `command` as a string and `shlex.split`s it ("legacy manager argv"; README.md:171-172 refers to "an existing `manager.command`"). Trigger: `[manager] command = "omp -p --model x"` — `load()` succeeds (`manager_model` splits the string and returns `x`), but `cfg.manager == ['o','m','p',' ',...]`. Impact: `factory doctor` (`onboard.py:227`) reports `manager: o` → FAIL, and `dashboard --json` `config.manager.command` (`dashboard.py:841`) emits `"o m p   - p ..."`. This violates the exit gate "`factory doctor` reports the binary" for an input form the codebase already accepts. Fix is one line: either reject non-lists with `ConfigError("manager.command must be an argv array")` (consistent with `config.py:237`) or reuse the `shlex.split` branch; the list-only template contract makes rejection the simpler choice.

### Optional suggestions

- `config.py:288` — `review` accepts any string; spec names only `"escalated"`/`"all"`. A `ConfigError` on other values would catch typos at `doctor` time rather than when T4 consumes it. Not required: no consumer exists yet (running is out of scope).
- `dashboard.py:840-844` has no test; exit gate does not require one, but a one-line assertion in the existing `DashboardTest` would pin the shape T4 will read.

## Plan drift

Plan status: not recorded, so compared against the issue's **Touches** list. Files changed: `factory/config.py`, `factory/dashboard.py`, `factory/onboard.py`, `factory/templates/factory.toml` (all named) + `tests/test_factory.py` (required by the exit gate's "loader test"). No unplanned files. Gate report shows `test: PASS`; the issue's named command `uv run pytest tests/test_factory.py` is not listed verbatim, so coverage of that exact invocation is `[INFERENCE]` from the generic test row. Drift exposes no additional defect.

VERDICT: REVISE
