# Issue #12: [manager] config table

Plan: `docs/manager-plan.md` (T3)

**Scope**
`[manager] command = [...]` (argv template with `{prompt}`, `{cwd}`; unset = disabled), `rounds = 1`, `review = "escalated"` (`"all"` allowed). Host-layer table like `review` (`HOST_TABLES`). `factory doctor` reports the binary; `dashboard --json` `config` includes it; `templates/factory.toml` documents it.

**Touches**
`factory/config.py` (`KNOWN_KEYS`, `HOST_TABLES`, `Config`, loader), `factory/onboard.py` doctor, `factory/templates/factory.toml`, `factory/dashboard.py`.

**Exit gate**
`factory doctor` prints a `manager:` row when configured and nothing when not; unknown keys under `[manager]` are reported as drift; `uv run pytest tests/test_factory.py` passes with a loader test for all three keys.

**Out of scope**
Running the command.

## Comment by @mikeroySoft (2026-09-05T00:52:58Z)

Triage: The issue is fully specified: it has a clear problem statement (add a `[manager]` host-layer config table), explicit acceptance criteria in the Exit gate (doctor prints a `manager:` row when configured and nothing otherwise, unknown keys reported as drift, loader test for all three keys), exact files to modify, and a concrete verification command (`uv run pytest tests/test_factory.py`). It does not require design judgment, release/signing/security policy, or cross-repo blast radius, and the out-of-scope item (running the command) keeps it contained.

Agent brief: Add a `[manager]` config table as a host-layer table like `review` (register in `HOST_TABLES` and `KNOWN_KEYS` in `factory/config.py`, extend `Config` and the loader). Three keys: `command` (argv template supporting `{prompt}` and `{cwd}` placeholders; unset = disabled), `rounds` (default 1), and `review` (default `"escalated"`, `"all"` allowed). `factory doctor` prints a `manager:` row when configured and nothing when not; unknown keys under `[manager]` are reported as drift. `dashboard --json` `config` includes the table; `templates/factory.toml` documents it. Add a loader test covering all three keys. Do NOT implement running the command. Verify with `uv run pytest tests/test_factory.py`.
