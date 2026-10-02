# Review: #12 `[manager]` config table (6ec3546..4830b7d)

## Spec coverage

| Criterion (issue #12) | Evidence | Status |
|---|---|---|
| `command` argv template, unset = disabled | `factory/config.py:104` (`manager: list[str] \| None = None`), `:285-286` (set only when `"command" in manager`) | Met |
| `rounds = 1` default | `factory/config.py:105`, `:287` | Met |
| `review = "escalated"` default, `"all"` allowed | `factory/config.py:106`, `:288` | Met (no validation; see Optional) |
| `KNOWN_KEYS` registers new keys | `factory/config.py:60` | Met |
| `HOST_TABLES` registration | Not in diff. README already documents `[defaults.manager]` as host-wide, so `manager` is presumably in `HOST_TABLES` at base. `[INFERENCE]` — unverifiable from the packet; gate PASS on the `host settings committed` assertions at `tests/test_factory.py:264-266` is consistent with it. | Not blocking |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227`; test `tests/test_factory.py:276-284` | Met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252`, `:263` | Met |
| `dashboard --json` `config` includes table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:40-48` | Met |
| Loader test covering all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution path added | Respected |

## Standards

Only README.md exists as documented convention at this head; no AGENTS.md/CONTRIBUTING.md. The diff follows the existing `review`/`reviewer` pattern exactly: same loader shape (`factory/config.py:283-286` mirrors `:283-284`), same doctor `shutil.which(argv[0])` probe (`factory/onboard.py:225` vs `:227`), same `" ".join(argv)` serialization in the snapshot (`factory/dashboard.py:839` vs `:841`). No new abstractions introduced. Template comment rewrite at `factory/templates/factory.toml:40-41` is confined to the `[manager]` block it extends.

## Required fixes

None.

## Optional suggestions

1. `factory/config.py:288` — `manager_review` accepts any string. Issue enumerates exactly two values (`"escalated"`, `"all"`); a typo (`"escalted"`) is silently accepted and will only surface when the (out-of-scope) runner reads it. A one-line check raising/warning on values outside `{"escalated", "all"}` would catch it at `doctor` time. Not required: the issue does not state rejection of other values as an acceptance criterion.
2. `tests/test_factory.py:283` — the "configured" branch writes `[manager]` only with `command`; the dashboard `config.manager` shape at `factory/dashboard.py:840-844` is untested. Exit gate doesn't require it; a throwaway `dashboard --json` check suffices.

VERDICT: APPROVE
