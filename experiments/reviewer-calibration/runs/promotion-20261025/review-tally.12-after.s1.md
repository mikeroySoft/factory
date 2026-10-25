```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":71,"summary":"`manager.rounds` is coerced with `int()` but never range-checked; `rounds = 0`/negative/`true` load silently (consistent with `gate.timeout` handling, so not a rule violation). A one-line `< 1` check would fail fast like `manager.review` does on line 73-74."}]}
```

Standards: `HOST_TABLES` already contained `manager` (config.py:50, pre-existing), `KNOWN_KEYS` extended (diff:10), unknown-key drift flows through existing `unknown_keys`/`host_drift` (onboard.py:139-151) with test coverage (diff:190,198). Template documents all three keys (diff:128-133); README defers to the template ("documents them all", README:118-119), so no README change needed. Doctor row matches reviewer-row pattern (diff:110-111).

Spec: command/rounds/review loaded with defaults unset/1/"escalated" (diff:18-20, 70-74); `"all"` accepted, other values rejected (diff:73-74, test diff:217); `{prompt}`/`{cwd}` template kept verbatim (test diff:170,180); doctor prints `manager:` only when configured (diff:110-111, test diff:224-232); `dashboard --json` `config.manager` (diff:94-98); loader test for all three keys (diff:180-181). Running the command: not implemented, docstring and template reiterate (diff:31, 128-129). Gate `test: PASS` covers the stated verification command.

Plan drift: no plan recorded. Beyond the issue's four named files, the diff touches `tests/test_briefing.py` (forced by the `manager_model`→`manager_settings` rename, diff:145-157) and `tests/test_factory.py` (required loader/doctor tests). Neither exposes a defect.

VERDICT: APPROVE
