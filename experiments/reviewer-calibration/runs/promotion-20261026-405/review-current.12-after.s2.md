The diff meets every acceptance criterion in issue #12. I found no correctness or security defect, so the verdict is APPROVE. I only read the diff; nothing was run, per the instructions.

**Standards:** The snapshot has no `AGENTS.md` or `CONTRIBUTING.md`. `docs/` holds only `manager-plan.md` and site assets. `README.md:118-119` says the `factory init` template documents every key, and the diff updates that template (`review.diff:121-133`). The new code follows the existing patterns: the doctor row matches the reviewer row (`review.diff:110-111`), the dashboard field joins the command the same way as `reviewer` (`review.diff:94-98`), and `rounds` is converted with `int(...)` like the other numeric settings.

**Spec (issue #12 and agent brief):**
- **`KNOWN_KEYS`:** now includes `command`, `rounds` and `review` (`review.diff:10`).
- **`HOST_TABLES`:** already contained `manager` before this diff (`snapshot/factory/config.py:50`), so it needed no change.
- **`Config` and loader:** the new fields default to `None`, `1` and `"escalated"` (`review.diff:18-20`). Only `"escalated"` and `"all"` are accepted for `review` (`review.diff:70-74`).
- **Doctor:** prints a `manager:` row only when a command is set (`review.diff:110-111`); tested at `review.diff:224-232`.
- **Drift:** an unknown key under `[manager]` is reported; tested at `review.diff:190,198`.
- **`dashboard --json`:** the `config` section now includes the manager settings (`review.diff:94-98`).
- **Template:** documents all three keys (`review.diff:121-133`).
- **Loader test:** covers all three keys (`review.diff:169-181`).
- **Out of scope:** nothing runs the command.

**Plan drift:** No plan was recorded, so I compared the diff against the issue's "Touches" list.
- `tests/test_briefing.py` is not named there. It changed because `manager_model` was renamed to `manager_settings` and now returns `(command, model)`. The rename is a reasonable way to share the command parsing between the old model lookup and the new setting, and the test still checks that the command is never executed (`review.diff:146-157`). It exposes no defect.
- The gate report shows `test: PASS` but does not name the issue's command, `uv run pytest tests/test_factory.py`. Since the gate is authoritative, I don't treat that as a gap.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`manager.rounds` is only passed through `int(...)`: 0 or negative values are accepted silently, and a non-integer raises a raw ValueError rather than a ConfigError. `review` gets a range check two lines later. This matches the existing numeric keys and the issue does not require validation."}]}
```
VERDICT: APPROVE
