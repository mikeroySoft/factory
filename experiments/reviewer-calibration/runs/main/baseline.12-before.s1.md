## Findings

### Spec (issue #12)

| Criterion | Evidence | Status |
|---|---|---|
| `[manager]` keys `command`, `rounds`, `review` in `KNOWN_KEYS` | `factory/config.py:60` (`"manager": ("model", "command", "rounds", "review")`) | Met |
| `Config` fields with defaults `rounds = 1`, `review = "escalated"`, unset command = disabled | `factory/config.py:104-106` (`manager: list[str] \| None = None`, `manager_rounds: int = 1`, `manager_review: str = "escalated"`) | Met |
| Loader reads all three | `factory/config.py:269`, `:285-288` | Met |
| `factory doctor` prints `manager:` row only when configured | `factory/onboard.py:226-227` (guarded by `if cfg.manager:`); test `tests/test_factory.py:276-284` covers absent → present | Met |
| Unknown `[manager]` keys reported as drift | `tests/test_factory.py:252` adds `[manager]\nunknown = true`; `:263` asserts `manager.unknown` in the keys-drift detail | Met |
| `dashboard --json` `config` includes the table | `factory/dashboard.py:840-844` | Met |
| `templates/factory.toml` documents it | `factory/templates/factory.toml:43-48` | Met |
| Loader test for all three keys | `tests/test_factory.py:112-115`, `:137-138` | Met |
| Out of scope: running the command | No execution path added | Met |

Non-blocking observations (no citation of a defect; not REVISE-worthy):

- `HOST_TABLES` is named in the issue's Touches but does not appear in the diff. `KNOWN_KEYS` already contained `"manager"` at base (`factory/config.py:60` context line shows `("model", "command")` pre-change) and README documents `[manager].model` as host-wide via `[defaults.manager]`, so `manager` is very likely already registered in `HOST_TABLES` `[INFERENCE]` — cannot confirm from the packet.
- `cfg.manager_review` (`factory/config.py:288`) accepts any string; the issue phrases `review` as an enumeration (`"escalated"`, `"all"`). No acceptance criterion requires rejection of other values, and the sibling keys (`reviewer`, `workers`) are likewise unvalidated in the loader, so this matches existing convention.

### Standards (README)

- `config.py` remains the single source of the repo-specific values; other modules consume `cfg.manager*` (`factory/dashboard.py:841-843`, `factory/onboard.py:227`). Consistent.
- Doctor/dashboard rendering follows the existing `reviewer` pattern (`factory/onboard.py:225` → `:227`; `factory/dashboard.py:839` → `:840-844`).
- Template comment documents `{prompt}`/`{cwd}` placeholders (`factory/templates/factory.toml:45`), consistent with README's `[review]`/`[workers]` documentation style.

No findings that fail the issue's acceptance criteria or documented conventions.

VERDICT: APPROVE
