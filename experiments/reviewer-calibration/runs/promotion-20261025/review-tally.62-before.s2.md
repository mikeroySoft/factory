**Scope traced:** `manage_pass` → `manager_cmd` → `dispatch.run` → failure body / `escalate(manager_failed)` → `escalation_packet` round arithmetic → `stats.human_touch`; doctor row; `learn.manager_llm`; README/template.

**Spec:** All four acceptance bullets implemented and tested (`config.py:146-151`, `manage.py:263-273`, `onboard.py:226-238`, README/template). `manager_cmd` takes a pre-written path rather than writing the file itself; observable contract (`<factory>/manager-prompt-<n>.md`, `@<path>` readable) still met via `manage.py:263-264`.

**Plan drift:** No plan recorded. Beyond the issue's "Touches": `factory/dispatch.py` (round arithmetic — required once `manager_failed` events exist) and `factory/learn.py` (caller of the changed signature). Both justified. Issue exit-gate manual step (District #26 kept-packet replay) is not in the gate report; handoff claims a prior attempt did it — unverified.

```json
{"verdict":"REVISE","findings":[
{"severity":"important","pass":"spec","path":"factory/templates/factory.toml","line":178,"summary":"Unrequested regression: the `ci-fix` and `conflict` host profiles (docs/manager-plan.md T11: 'ci-fix (read the failing job log, fix or declare flake with evidence) and conflict (resolve the rebase, keep both intents, no semantic changes)'; CHANGELOG #20) lose their worker instructions. `cfg.worker_when` is only rendered into the manager prompt (manage.py:252-253); the FIX worker receives only the manager's `guidance` + packet (manage.py:168). The old argv was valid omp usage ({prompt} as a path inside prompt text) and did not conflict with #62's `{prompt}`=path contract. Also line 183. Revert both lines."},
{"severity":"nit","pass":"spec","path":"README.md","line":27,"summary":"'bare `omp {prompt}` … is rejected' is only true for the manager (`manager_cmd`); `Config.worker` (config.py:138-141) performs no such check. Same overclaim in factory/templates/factory.toml:169 under [workers]."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":124,"summary":"The `manager_failed` escalate row is recorded inside the `manage` lifecycle scope, so `dispatch.record` (dispatch.py:245-248) flips the manage execution's exit outcome to `project_escalation` with reason `manager_failed`, which is not in `runtime_events.REASONS` (runtime_events.py:34-44) and is normalised to None in `--runtime-json`."},
{"severity":"nit","pass":"bug","path":"factory/manage.py","line":124,"summary":"`stats.human_touch` counts every `escalate` row (stats.py:167-170), so each manager failure adds one to `escalation_count`/`escalations_per_week` although the ticket was already in ready-for-human; counts manager failures as human escalations rather than separately."}
]}
```

VERDICT: REVISE
