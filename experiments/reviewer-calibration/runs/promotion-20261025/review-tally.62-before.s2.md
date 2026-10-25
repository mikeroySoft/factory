**Spec trace (issue #62 acceptance → diff)**

- `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` by the caller (`factory/manage.py` diff lines 114–116; `factory/learn.py` 97–101 uses a temp file, same transport). Bare `{prompt}` with argv[0] `omp` → `ConfigError` naming `@{prompt}` (`factory/config.py` 59–60); other programs untouched. ✔
- Nonzero exit → `Manager command exited N (argv: <unexpanded json>)` + last 5 stderr lines, `escalate` event with `reason: manager_failed` (`factory/manage.py` 119–125). Round arithmetic excludes those events (`factory/dispatch.py` 74–75) so the next real escalation still gets round 2; `manage_pass` sees the same round + existing `manage` event and does not replay. ✔
- `factory doctor` `manager command` row: WARN unset, FAIL missing placeholder/omp bare `{prompt}`/executable, PASS otherwise (`factory/onboard.py` 145–157). ✔
- README §Configuration + template document file for workers/manager, text for reviewer (README 24–41; template 168–199). No `DEFAULT_MANAGER` constant exists in base; the documented default command now has worker shape. ✔
- Exit-gate tests present: District-shaped `@{prompt}` argv + readable file, bare `{prompt}` rejection (tests 211–226); exit-1 stub with ≤5 stderr lines + `manager_failed` event (299–331); doctor WARN/FAIL cases (244–270). ✔
- `ConfigError` caught at the manager boundary so a stale host config yields a HUMAN diagnosis instead of aborting the pass (131, test 333–345).

**Plan drift**

Plan not recorded. Files beyond the issue's "Touches": `factory/dispatch.py` (needed: round count must ignore `manager_failed` events the diff introduces) and `factory/learn.py` (caller of the changed `manager_cmd` signature). Both are required consequences, not scope creep. The issue's manual proof (re-run `factory manage` on District #26's kept packet) is not in the gate report; the handoff claims a prior attempt observed `DECISION: FIX` — unverified here, non-blocking since the exit-gate tests cover the transport.

**Findings**

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":124,"summary":"The `manager_failed` escalate event becomes the newest `escalate` row, so on the next pass `human_activity(n, escalation)` filters by its later `at`. If a human acted during the failed run (post-run check at manage.py:281 hits `continue`, no `manage` event), that activity is invisible next pass and the manager re-runs and may post a HUMAN comment over a human takeover. Narrow window (human activity during a failing run); the success path is unaffected."},
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":178,"summary":"ci-fix/conflict example commands drop their task-specific instructions (flake evidence, restart rebase against main) in favour of bare `@{prompt}`. #62 only asked to document `{prompt}` = file; the old examples already used a path. Unrequested behavioural change to documented profiles."},
{"severity":"nit","pass":"spec","path":"factory/templates/factory.toml","line":169,"summary":"Comment under `[workers]` says bare `{prompt}` \"is rejected\", but only `Config.manager_cmd` validates; `Config.worker` (config.py:138) performs no such check, so a worker misconfiguration is not rejected."}
]}
```

VERDICT: APPROVE
