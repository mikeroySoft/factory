# Review: mikeroySoft/factory #12 — `[manager]` config table

## Spec coverage

| Acceptance criterion | Evidence | Status |
|---|---|---|
| `[manager] command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:285-286` (only set when `"command" in manager`) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:287` | Met |
| `review = "escalated"` default, `"all"` allowed | `factory/config.py:106`, `:288` | Met (no value validation; see Optional) |
| `KNOWN_KEYS` registration | `factory/config.py:60` adds `rounds`, `review` to existing `manager` tuple | Met |
| `HOST_TABLES` registration | Not in diff. `manager` already existed in `KNOWN_KEYS` at base and README documents `[defaults.manager]` as host-wide, so [INFERENCE] it was already in `HOST_TABLES`. Cannot confirm from the packet. | Presumed met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` covers both absent and present | Met |
| Unknown keys under `[manager]` reported as drift | `tests/test_factory.py:252`, `:263` (`manager.unknown` in `.factory.toml keys` detail); drift path is the existing `KNOWN_KEYS` walk | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:40-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution path added | Met |

## Standards

No AGENTS.md/CONTRIBUTING.md at this head. README convention "`config.py` as the single source of every repo-specific value" — followed (`factory/config.py:104-106`, `:269`, `:285-288`). New fields mirror the existing `reviewer` pattern: `Config` field + `if "command" in raw.get(...)` guard (`:283-286`), `int(...)` coercion for numerics as with `gate.timeout` (`:287` vs `:289`), `" ".join(argv)` in the snapshot (`factory/dashboard.py:841` vs `:839`), `shutil.which(argv[0])` in doctor (`factory/onboard.py:227` vs `:225`). No new abstractions introduced.

Template comment rewrite at `factory/templates/factory.toml:40-41` touches pre-existing lines, but the old text ("Only the model is configurable here") became false with this change, so the edit traces to the request.

## Required fixes

None.

## Optional suggestions

- `factory/config.py:288` — `manager_review` accepts any string; the issue text names only `"escalated"` and `"all"`. A typo (`"al"`) silently loads and would only surface when the (out-of-scope) runner reads it. Not a stated acceptance criterion and the existing loader validates no enum values elsewhere, so non-blocking; if you want it, a one-line `ValueError` in the loader is sufficient — no validation framework.
- `tests/test_factory.py:281` — `assertNotIn("manager: manage", rows())` proves absence of that exact label only; an empty-argv `command = []` would also produce no row (falsy list at `factory/onboard.py:226`) and be indistinguishable from "unset". Acceptable given "unset = disabled" is the spec; noting only.

VERDICT: APPROVE
