Inputs note: `./review.diff` and `./snapshot` do not exist in the workspace; `issue://` needs `GH_TOKEN`. I reconstructed the diff as `git diff origin/main...HEAD` in `./` (commits `051790e..4830b7d`, 5 files, matches the issue's Touches list) and fetched issue #12 via the public GitHub API. Findings below cite that diff.

## Standards
No `AGENTS.md`/`CONTRIBUTING.md` in repo; only `pyproject.toml` ruff `line-length = 100` (not CI-enforced; `ci.yml` runs unittest only) and the `docs/manager-plan.md` T3 contract. The diff mirrors the existing `review.command` loader/doctor/dashboard pattern (`config.py:283-288`, `onboard.py:225-227`, `dashboard.py:839-844`) and the file's existing `lambda … # noqa: E731` test idiom. No standards violations.

## Spec (issue #12 / plan T3)
- `KNOWN_KEYS` gains `rounds`, `review` (`config.py:60`); `manager` already in `HOST_TABLES` (`config.py:50`) so host-layer merge via `host_filter` covers the new keys. ✓
- `Config` + loader: three keys, defaults `rounds=1`, `review="escalated"` (`config.py:104-106`, `285-288`). ✓
- Doctor `manager:` row only when configured (`onboard.py:226-227`), tested at `tests/test_factory.py:276-284`. ✓
- Unknown `[manager]` key reported as drift, tested (`tests/test_factory.py:252`, `263`). ✓
- `dashboard --json` `config.manager` (`dashboard.py:840-844`). ✓
- Template documents all three keys (`factory/templates/factory.toml:43-48`). ✓
- Loader test for all three keys (`tests/test_factory.py:112-115`, `137-138`). ✓
- Running the command: not implemented. ✓

Defect: `config.py:285-286` does `cfg.manager = list(manager["command"])`. Within the same `load()`, `manager_model()` (`config.py:231-237`) explicitly accepts `command` as a string (`shlex.split`) and raises `ConfigError` otherwise. A string `command = "omp -p --model x"` therefore passes validation but `cfg.manager` becomes `['o','m','p',' ',…]`; doctor reports `manager: o` and dashboard `config.manager.command` shows `"o m p   - p …"`. Silent misparse of an input the module itself declares valid. Fix: reuse the same `shlex.split`/type check (or reject strings with `ConfigError`) before `list(...)`.

## Plan drift
Plan status not recorded → no planned file list or proof list to diff against. Changed files equal the issue's Touches set plus `tests/test_factory.py` (required by the exit gate). Gate `test: PASS` is the only proof; the issue's verification command is covered by it.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/config.py","line":286,"summary":"`list(manager[\"command\"])` mangles a string `command` (accepted by `manager_model` at config.py:231-237 via shlex) into a list of characters; doctor then reports `manager: o` and dashboard shows garbage argv. Apply the same shlex/type validation or raise ConfigError for strings."},
{"severity":"nit","pass":"spec","path":"factory/config.py","line":288,"summary":"`review` accepts any string; issue allows only \"escalated\"|\"all\". Typos load silently and surface only when T4/T6 consume it."},
{"severity":"nit","pass":"bug","path":"factory/config.py","line":285,"summary":"If `[manager]` is not a table, `\"command\" in manager` / `.get` raises TypeError/AttributeError before `manager_model()` at line 302 can raise its ConfigError(\"[manager] must be a table\"); same pattern as other tables, but this diff moves the first failure ahead of the friendly check."}
]}
```
VERDICT: REVISE
