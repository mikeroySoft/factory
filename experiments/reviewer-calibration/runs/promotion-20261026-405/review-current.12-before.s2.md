The `[manager]` table is mostly in place, but one regression blocks approval. A `manager.command` written as a single string still passes the existing validator, then breaks `factory doctor`. I read the diff and the snapshot only; I ran no builds or tests.

**Standards:** The new code follows the existing loader and doctor patterns. The test changes follow the existing patterns in `tests/test_factory.py`. I found no breach of a documented rule.

**Spec (issue #12):**
- **Met:**
  - All three keys are in `KNOWN_KEYS`, and `manager` was already listed in `HOST_TABLES`.
  - `Config` has the new fields, and the loader reads them.
  - `factory doctor` prints a `manager:` row only when a command is set, and unknown `[manager]` keys show up as drift (both tested).
  - `dashboard --json` `config.manager` has the new block, and the template documents all three keys.
  - A loader test covers all three keys, and nothing runs the command.
- **Defect (important):**
  - **Cause:** the snapshot's `manager_model()` still accepts a command written as one string, e.g. `command = "omp -p --model x"` (`snapshot/factory/config.py:231-237`). The new loader runs `list(manager["command"])` on it (`factory/config.py:286`), which splits it into single characters: `["o","m","p"," ",...]`.
  - **Effect:** `factory/onboard.py:227` then reports `manager: o` as FAIL, so `factory doctor` exits 1 on a config that was valid before this change. `dashboard --json` (`factory/dashboard.py:841`) also shows the command as garbage.
  - **Fix:** in `load`, split a string with `shlex.split`, or reject non-list values with `ConfigError`.

**Plan drift:** No plan was recorded. Measured against the issue's Touches list, the only extra file is `tests/test_factory.py`, which the exit gate requires. `HOST_TABLES` was not edited because it already contained `manager`. The planned check, `uv run pytest tests/test_factory.py`, matches the gate's `test: PASS`.

```json
{"verdict":"REVISE","findings":[{"severity":"important","pass":"bug","path":"factory/config.py","line":286,"summary":"`cfg.manager = list(manager[\"command\"])` turns a string-form command into single characters. The snapshot still accepts strings: manager_model() at config.py:231-237 shlex-splits `command = \"omp -p --model x\"`. Trigger: an existing config that uses the string form, which was valid before this change. Effect: cfg.manager == ['o','m','p',' ',...]; doctor (onboard.py:227) reports `manager: o` as FAIL, so `factory doctor` exits 1, and dashboard --json config.manager.command is garbage. Fix: shlex-split strings or reject non-list commands with ConfigError in load."},{"severity":"nit","pass":"spec","path":"factory/config.py","line":288,"summary":"manager.review is not validated. The issue allows \"escalated\" (default) or \"all\", but any string, e.g. a typo like \"al\", is silently accepted and shown in doctor/dashboard as if valid."},{"severity":"nit","pass":"bug","path":"factory/config.py","line":287,"summary":"int(manager.get(\"rounds\")) raises a bare ValueError/TypeError on bad input such as rounds = \"x\", not the module's ConfigError. A non-list, non-string command (e.g. command = 5) also hits a TypeError at line 286 before manager_model's ConfigError at line 302 can report it."}]}
```
VERDICT: REVISE
