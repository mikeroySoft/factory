## Standards
No AGENTS.md/CONTRIBUTING.md in the snapshot; README §Configuration defers to `templates/factory.toml` as the full key reference (README.md:118-119), which the diff updates (`factory/templates/factory.toml:85-90`). Loader additions mirror the existing `review.command` / `gate.timeout` shapes (`factory/config.py:283-290`). No smell-baseline hits beyond the loader inconsistency below.

## Spec (issue #12 + brief)
- `command`/`rounds`/`review` keys, defaults `None`/1/`"escalated"`: `factory/config.py:104-106, 285-288` ✔
- `HOST_TABLES`: already contains `"manager"` pre-diff (`snapshot/factory/config.py:50`), so no change needed ✔
- `KNOWN_KEYS` drift: `factory/config.py:60`; test `tests/test_factory.py:123,131` ✔
- doctor row only when configured: `factory/onboard.py:226-227`; test `tests/test_factory.py:139-147` ✔
- `dashboard --json` config: `factory/dashboard.py:840-844` ✔
- loader test for all three keys: `tests/test_factory.py:102-105,113-114` ✔
- Running the command: not implemented ✔

Defect: `manager_model()` (pre-existing, `config.py:229-237`) explicitly accepts `command` as a string ("must be an argv array or command string") and runs *after* the new line `cfg.manager = list(manager["command"])` (`config.py:286`). A string `command = "omp -p {prompt}"` — valid before this diff — now passes validation but yields a per-character list: doctor prints `manager: o` FAIL (`onboard.py:227`), dashboard shows `"o m p ..."` (`dashboard.py:841`). Regression on an accepted input shape; fix is one line (`shlex.split` when `str`, as `manager_model` does, or raise `ConfigError`).

## Plan drift
Plan not recorded. Files beyond the issue's Touches list: `tests/test_factory.py` — required by the exit gate (loader test), not drift. Gate report `test: PASS` covers `uv run pytest tests/test_factory.py`; doctor-row and drift criteria are proven by the new tests rather than a separate doctor transcript. No missing proof that exposes a defect.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/config.py","line":286,"summary":"`cfg.manager = list(manager[\"command\"])` splits a string command into characters. `manager_model()` (config.py:229-237, runs later at :302) explicitly accepts `command` as a string, so `[manager] command = \"omp -p {prompt}\"` — valid before this diff — now loads silently as ['o','m','p',...]: doctor reports `manager: o` FAIL (onboard.py:227) and dashboard config shows \"o m p ...\" (dashboard.py:841). Either shlex.split a str like manager_model does, or raise ConfigError for non-list."},{"severity":"nit","pass":"spec","path":"factory/config.py","line":288,"summary":"`manager_review` accepts any string; issue allows only \"escalated\" or \"all\". A typo is not reported by doctor drift (key is known) and only surfaces when T4 consumes it. Not required by the exit gate."}]}
```
VERDICT: REVISE
