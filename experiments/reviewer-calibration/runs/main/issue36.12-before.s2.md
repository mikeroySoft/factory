# Review: mikeroySoft/factory #12 — `[manager]` config table

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:285-286` (set only if `"command" in manager`) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:287` | Met |
| `review = "escalated"` default, `"all"` allowed | `factory/config.py:106`, `:288`; test `tests/test_factory.py:115`, `:138` | Met |
| `KNOWN_KEYS` registration / unknown keys as drift | `factory/config.py:60`; test `tests/test_factory.py:252`, `:263` | Met |
| `HOST_TABLES` registration | Not in diff. `KNOWN_KEYS["manager"]` already existed at base (`config.py:60` old side) and README documents `[defaults.manager]`, so the table is [INFERENCE] already host-layer at base. Not verifiable from the packet; not blocking. | Unverified, likely pre-existing |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents keys | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | Nothing executes `cfg.manager` | Met |

## Standards

README is the only convention source at this head. Diff follows the existing `review` pattern (`cfg.reviewer` list, `if "command" in ...`, `shutil.which(argv[0])` in doctor, `" ".join` in dashboard). No new abstractions introduced. Template comment placement matches surrounding sections.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `manager_review` accepts any string. Issue text says `"all"` allowed, not that other values must be rejected, so not blocking; if a later feature branches on this value, a bad value will silently behave as neither. A one-line check in doctor (drift-style WARN) would surface it without adding abstraction. Same latitude exists today for other keys, so this is consistent with repo precedent.
2. `tests/test_factory.py:281` — `rows = lambda: ...` with `# noqa: E731` is fine but a nested `def` avoids the suppression. Style only.
3. `factory/templates/factory.toml:41-42` — the rewording of the pre-existing `model` comment lines is outside the brief's "documents it" scope (adjacent-line edit). Harmless; noting per surgical-change norm.

VERDICT: APPROVE
