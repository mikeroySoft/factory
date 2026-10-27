**Standards** — no `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `skills/factory/SKILL.md:24` requires the `factory init` template to document every key → `templates/factory.toml` hunk (review.diff:85-90) satisfies it. `KNOWN_KEYS`/loader/`Config` edits follow the existing `review` pattern. `HOST_TABLES` already contained `"manager"` (snapshot `config.py:50`), so the "register in HOST_TABLES" item is pre-satisfied.

**Spec (#12 exit gate)** — all three criteria have diff evidence: doctor row only when configured (`onboard.py` review.diff:67-68, test review.diff:139-147); unknown `[manager]` keys as drift (review.diff:123,131); loader test for `command`/`rounds`/`review` (review.diff:102-105,113-114); `dashboard --json` `config.manager` (review.diff:51-55); running the command untouched.

**Plan drift** — plan not recorded; issue "Touches" names `config.py`, `onboard.py`, `templates/factory.toml`, `dashboard.py`. Extra file `tests/test_factory.py` is required by the exit gate, not drift. Planned proof (`uv run pytest tests/test_factory.py`) is covered by gate `test: PASS`.

**Defect** — `cfg.manager = list(manager["command"])` (review.diff:37). The same table is also passed through `manager_model()` at snapshot `config.py:302`, which explicitly accepts `command` as a string (`shlex.split`, error text "argv array or command string", `config.py:231-237`). A string `command = "omp -p {prompt}"` therefore passes load without error but yields `cfg.manager == ['o','m','p',' ',...]`: `doctor` reports `manager: o` FAIL (review.diff:68) and `dashboard --json` emits a space-joined character soup (review.diff:52). One-line fix: reject non-list `command` with `ConfigError`, or split strings the same way `manager_model` does.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"bug","path":"factory/config.py","line":37,"summary":"`list(manager[\"command\"])` on a string command (explicitly accepted by `manager_model()` on the same table, config.py:231-237) silently produces a per-character argv: load succeeds, `doctor` checks `shutil.which(\"o\")`, dashboard config shows garbage. Reject non-list with ConfigError or shlex.split like manager_model."},
{"severity":"nit","pass":"spec","path":"factory/config.py","line":39,"summary":"Issue says review = \"escalated\" with \"all\" allowed; loader stores any string unvalidated. Harmless until a consumer reads it (T5), but a typo won't surface in `doctor`."}
]}
```

VERDICT: REVISE
