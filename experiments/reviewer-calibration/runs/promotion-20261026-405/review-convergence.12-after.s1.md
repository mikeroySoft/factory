**Standards.** `[manager]` already in `HOST_TABLES` (snapshot `factory/config.py:50`, pre-diff); `KNOWN_KEYS` extended (`review.diff:10`), so `unknown_keys` reports `manager.unknown` drift (tested `review.diff:190,198`). Template documents all three keys (`review.diff:128-133`); README delegates full key docs to the template (`README.md:118-119`), so no README change required. Loader follows the existing `review`/`gate` pattern.

**Spec (#12 brief).** `command` argv template stored unexpanded, unset → `None` (`review.diff:18,70`); `rounds` default 1, `review` default `"escalated"` with `"all"` allowed and other values rejected (`review.diff:19-20,71-74`). Doctor emits `manager:` row only when configured (`review.diff:110-111`, tested `review.diff:224-232`). `dashboard --json` `config.manager` added (`review.diff:94-98`). Loader test covers all three keys (`review.diff:169-181`). Running the command: not implemented, as required. Rename `manager_model`→`manager_settings` has no stale callers in snapshot.

**Plan drift.** No plan recorded. Files outside issue "Touches": `tests/test_factory.py` (required by exit gate) and `tests/test_briefing.py` (mechanical ripple of the rename, `review.diff:145-157`). Gate report doesn't name the `uv run pytest tests/test_factory.py` command, but `test: PASS` is authoritative. No defect exposed.

```json
{"verdict":"APPROVE","findings":[{"id":"F1","severity":"nit","pass":"bug","class":"unvalidated-int-key","path":"factory/config.py","line":71,"summary":"`int(manager.get(\"rounds\", ...))` raises bare ValueError on a non-integer value instead of ConfigError, and accepts 0/negative; mirrors the pre-existing `gate.timeout` pattern, so consistency, not a rule violation.","instances":[{"path":"factory/config.py","line":71,"summary":"manager.rounds coerced with int() without ConfigError wrapping or >=1 check"}]}]}
```

VERDICT: APPROVE
