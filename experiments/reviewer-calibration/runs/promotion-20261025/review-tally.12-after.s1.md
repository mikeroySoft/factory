## Standards
No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; conventions come from `README.md`, `skills/factory/SKILL.md`, `factory/templates/factory.toml`, and sibling code in `factory/config.py`. The diff matches existing loader style (`ConfigError`, `shlex`, layered host tables). One inconsistency: `load()`'s own errors carry a `{path}:` prefix (`config.py:283`, `:299`) while the new `manager.review` error at `factory/config.py:291` does not — same pattern as the pre-existing `manager.command` errors, so nit only. Clean cutover: `manager_model` has no remaining callers (`briefing.py` reads `cfg.manager_model` only).

## Spec (issue #12 + triage brief)
Exit-gate criteria (doctor `manager:` row, `[manager]` drift, loader test for `command`/`rounds`/`review`) were already met at base (`tests/test_factory.py:137-138`, `onboard.py:226-227`, `KNOWN_KEYS["manager"]`). The diff does not regress them. It adds two things not in the brief: (a) `review` is now validated to `escalated|all` (`config.py:290-291`) — consistent with the issue's "`"all"` allowed" wording, a guard not an abstraction; (b) a string `command` is now shlex-split into `cfg.manager` (`config.py:232-238`) instead of the base's `list(str)` → per-character argv, which would have made `doctor` and `dashboard --json` emit garbage. That is a real fix. Issue says `templates/factory.toml` documents the table; the template (`:46`) only shows the array form while the error at `config.py:238` and the new test at `tests/test_factory.py:279` advertise the string form — doc gap, nit.

## Plan drift
Plan status not recorded, so there is no named file list or planned proof to diff against. Files changed: `factory/config.py`, `tests/test_briefing.py`, `tests/test_factory.py`. Gate report: test PASS covers the new tests. No drift-exposed defect.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":291,"summary":"New manager.review ConfigError omits the `{path}:` prefix that sibling load() errors use (config.py:283, :299); harder to attribute when host and repo files both set the key."},{"severity":"nit","pass":"spec","path":"factory/config.py","line":238,"summary":"String form of manager.command is now a first-class loader input (error text and tests/test_factory.py:279 rely on it) but factory/templates/factory.toml:46 documents only the argv-array form; issue #12 says the template documents the table."}]}
```
VERDICT: APPROVE
