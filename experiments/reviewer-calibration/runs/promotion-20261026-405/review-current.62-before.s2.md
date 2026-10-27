```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":178,"summary":"Unrequested rewrite of the commented `ci-fix` and `conflict` worker examples (also line 183): the inline instruction text (flake-evidence rule, keep-both-intents rule) is dropped in favour of `@{prompt}`. Issue #62 only asked to document `{prompt}` = file for workers/manager; the old sentences were valid omp inline prompts, not the bare-`{prompt}` misuse the new comment at line 169 warns about."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":272,"summary":"The `manager_failed` escalate event is counted by `stats.py:167` (`escalation_count = max(len(starts), len(escalated))`) as an additional escalation of the same ticket, so `escalation_count`/`escalations_per_week` inflate by one per manager failure. The issue asked for the event so stats \"can count it\"; stats does not filter by `reason`, so it counts it as a human escalation rather than a manager failure."},
{"severity":"nit","pass":"spec","path":"factory/onboard.py","line":145,"summary":"Doctor now emits a WARN row when `[manager].command` is unset, but CHANGELOG.md 0.3.0 (\"`factory doctor` reports the manager only when configured\") is not updated and no changelog entry references #62; the repo keeps a per-issue CHANGELOG."},
{"severity":"nit","pass":"spec","path":"README.md","line":41,"summary":"New `[manager]` example `[\"omp\", \"-p\", \"--cwd\", \"{cwd}\", \"@{prompt}\"]` is a full-tools worker invocation, while README.md:28-29 (same diff) instructs configuring the manager CLI in read-only/no-tools mode; the example contradicts the adjacent guidance."}
]}
```

Standards: no AGENTS.md/CONTRIBUTING.md in the snapshot; README and `factory init` template updated consistently with the new `{prompt}` contract (README.md:24-32, factory.toml:197-199).

Spec: every "After this change" item is present — `manager_cmd(prompt_path, cwd)` + omp bare-`{prompt}` `ConfigError` (config.py:146-151), file transport in `manage_pass` (manage.py:263-265) and `learn.manager_llm` (learn.py:97-101), bounded failure body with unexpanded argv + 5-line stderr tail + `manager_failed` escalate event (manage.py:267-273), doctor PASS/FAIL/WARN row (onboard.py:145-157); exit-gate tests exist (test_factory.py:211-226, 244-270, 278-345). `manager_failed` events are excluded from round arithmetic (dispatch.py:375-378) and the next pass skips replay because a `manage` event with the same round exists (manage.py:241-244).

Plan drift: plan not recorded. Files beyond the issue's "Touches": `factory/dispatch.py` (round exclusion, required by the new event) and `factory/learn.py` (caller of the changed `manager_cmd` signature) — both necessary. Issue exit-gate item "Manual: re-run `factory manage` on District #26's kept packet" is not in the gate report; the handoff cites a prior-attempt replay log only (unverified, not a defect in the diff).

VERDICT: APPROVE
