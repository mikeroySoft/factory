Inputs: `./review.diff` and `./snapshot` are absent from the sandbox; the cwd checkout is the gated revision (`7cd7a83`, two commits over `origin/main` `6ec3546`), so I reviewed `git diff 6ec3546..HEAD` (identical file set to the task description). Issue #12 fetched via web (no `GH_TOKEN`). Repo has no AGENTS.md/CONTRIBUTING.md; conventions come from README "Configuration" and `docs/manager-plan.md` T3.

**Spec (issue #12 exit gate / agent brief)**
- `command`/`rounds`/`review` keys in `KNOWN_KEYS` — `factory/config.py:10`; `HOST_TABLES` already contained `manager` on base (snapshot `config.py:50`), so host-layer loading works; host-layer test at `tests/test_factory.py:209-212`.
- Loader + `Config` fields — `config.py:18-20, 70-74`; `review` validated to `escalated|all` (`:73-74`); loader test for all three keys `tests/test_factory.py:169-172, 180-181`.
- Doctor row only when configured — `factory/onboard.py:110-111`, test `tests/test_factory.py:224-232`.
- Unknown `[manager]` keys as drift — handled by existing `unknown_keys`; test `tests/test_factory.py:190, 198`.
- `dashboard --json` `config` includes the table — `factory/dashboard.py:94-98`.
- Template documents it — `factory/templates/factory.toml:128-133`.
- Out of scope "running the command" respected: no `expand()`/subprocess path added; `manager_settings` docstring and `test_briefing.py:146-152` preserve the never-execute guarantee.

**Standards** — README:118-119 designates the `factory init` template as the complete key reference; satisfied. Int coercion for `rounds` matches sibling fields (`check_timeout`, `max_active`).

**Plan drift** — Plan not recorded. Beyond the issue's "Touches" list: `tests/test_briefing.py` (call-site rename `manager_model`→`manager_settings`, forced by folding command normalization into one function) and `tests/test_factory.py` (required by exit gate). No missing proof: gate `test: PASS` covers `uv run pytest tests/test_factory.py`.

```json
{"verdict":"APPROVE","findings":[{"severity":"nit","pass":"bug","path":"factory/config.py","line":54,"summary":"`command = []` yields `cfg.manager == []` rather than `None`; doctor/dashboard treat it as disabled via truthiness, but the declared type `list[str] | None` suggests `None` means disabled — normalize empty argv to None (or reject it) so later consumers can't diverge."}]}
```

VERDICT: APPROVE
