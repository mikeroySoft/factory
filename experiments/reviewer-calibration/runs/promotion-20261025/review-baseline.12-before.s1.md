## Standards

No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` coding standard in the snapshot; the only documented conventions are the existing loader patterns in `factory/config.py`. The diff follows them: `KNOWN_KEYS` entry (`review.diff:10`), dataclass fields with defaults (`review.diff:18-20`), `int(...)` coercion and `.get(key, default)` mirroring `gate.timeout`/`triage.model` (`review.diff:38-39`), doctor row shaped like the reviewer row (`review.diff:67-68`), dashboard `config` entry joined like `reviewer` (`review.diff:51-55`). `manager` was already in `HOST_TABLES` (snapshot `factory/config.py:50`), so the "register in HOST_TABLES" step is already satisfied without a diff hunk.

Smell baseline: nothing beyond judgement-call level; no new abstractions introduced.

## Spec (issue #12 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `review.diff:18,36-37` | met |
| `rounds = 1`, `review = "escalated"` defaults | `review.diff:19-20,38-39` | met |
| Host-layer table like `review` | pre-existing `HOST_TABLES` entry | met |
| `factory doctor` prints `manager:` row only when configured | `review.diff:67-68`, test `review.diff:139-147` | met |
| Unknown `[manager]` keys reported as drift | `review.diff:10,123,131` | met |
| `dashboard --json` `config` includes table | `review.diff:51-55` | met |
| `templates/factory.toml` documents it | `review.diff:82-90` | met |
| Loader test for all three keys | `review.diff:102-105,113-114` | met |
| Out of scope: running the command | no exec added | met |

### Required fixes

1. **`command` given as a string is silently exploded into characters.** `factory/config.py` already accepts `manager.command` as either an argv array *or* a command string and validates it as such (snapshot `factory/config.py:231-237`, `manager_model`, called at `config.py:302`). The new loader line `cfg.manager = list(manager["command"])` (`review.diff:37`) runs before that validator and, for `command = "omp -p {prompt}"`, yields `['o','m','p',' ', ...]`. Trigger: any host/repo config using the string form that the same loader explicitly permits. Impact: `doctor` reports `manager: o` → FAIL (`review.diff:68`), `dashboard --json` emits `"o m p   - p ..."` (`review.diff:52`), and the stored `cfg.manager` is garbage for the future runner. Defect class: concrete correctness (loader accepts input it then misinterprets). Minimal fix: `shlex.split` when `isinstance(cmd, str)` (matching `manager_model`), or reject non-lists with a `ConfigError` and drop the string tolerance in `manager_model` — either way one branch, no new abstraction.

### Optional suggestions

- `review` accepts any string (`review.diff:39`); the brief says `"escalated"` default, `"all"` allowed. Validating against `{"escalated", "all"}` would surface typos at `doctor` time instead of in the out-of-scope runner. Not an explicit acceptance criterion, so non-blocking.
- `int(manager.get("rounds", ...))` (`review.diff:38`) raises an uncaught `ValueError` for `rounds = "x"` — same as existing `gate.timeout`/`dashboard.port`, so consistent with repo convention; mention only.

## Plan drift

Plan status not recorded, so no worker plan to diff against. Against the issue's **Touches** list (`config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`): one extra file, `tests/test_factory.py` (`review.diff:94`), which the Exit gate explicitly requires (loader test, doctor/drift behaviour). Not drift. Gate report `test: PASS` covers the `uv run pytest tests/test_factory.py` proof. No planned proof missing.

VERDICT: REVISE
