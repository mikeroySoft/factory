# AGENTS.md

Factory is a label-driven ticket pipeline for GitHub repos. Commands, configuration,
and contracts live in [README.md](README.md); setup, ticket writing, and escalation
diagnosis live in [skills/factory/SKILL.md](skills/factory/SKILL.md). Read those
rather than copying them here. `factory/architecture.html` (dashboard `/atlas`)
draws the system.

**Evidence of done:** the dispatcher runs the gate itself after every worker attempt
and before review; that run is the evidence, not a worker's claim to have run
anything (`experiments/verification-claims/REPORT.md`).

## Feature map

Gate checks come from `.factory.toml`: `test` runs `unittest discover -s tests`, so
it exercises every test listed below. The built-in `conflict-markers`,
`protected-paths` and `leak-scan` (default pattern) checks run on every diff.
`tests/test_agents_map.py` fails when a `factory/` module is missing from this
map or a path in it does not exist; update the map in the same change.

| Feature | Entry files | Tests | Gate checks |
|---|---|---|---|
| CLI and package | `factory/cli.py`, `factory/__main__.py`, `factory/__init__.py` | `tests/test_runtime.py`, `tests/test_chat.py`, `tests/test_factory.py` | `test` |
| Configuration | `factory/config.py`, `factory/templates/factory.toml` | `tests/test_factory.py`, `tests/test_settings.py`, `tests/test_dashboard_port.py` | `test` |
| Onboarding (`init`, `doctor`, `install`) | `factory/onboard.py`, `factory/templates/ci.yml` | `tests/test_factory.py`, `tests/test_dashboard_port.py`, `tests/test_plan.py` | `test` |
| Triage | `factory/triage.py` | `tests/test_factory.py`, `tests/test_llm_usage.py`, `tests/test_idle_lifecycle.py` | `test` |
| Dispatcher and worker brief | `factory/dispatch.py`, `factory/brief.py`, `factory/templates/agent_task.md` | `tests/test_factory.py`, `tests/test_dispatch_lifecycle.py`, `tests/test_wait_decisions.py`, `tests/test_idle_lifecycle.py`, `tests/test_frontier.py` | `test` |
| Quality gate | `factory/gate.py` | `tests/test_factory.py`, `tests/test_gate_resources.py` | `test`; implements `conflict-markers`, `protected-paths`, `leak-scan` |
| Manager and human handoffs | `factory/manage.py`, `factory/handoff.py` | `tests/test_viability.py`, `tests/test_handoff.py`, `tests/test_manager_binding.py`, `tests/test_frontier.py` | `test` |
| Lifecycle journal (`events.jsonl`) | `factory/lifecycle.py` | `tests/test_lifecycle.py`, `tests/test_lifecycle_consumers.py`, `tests/test_events_rotation.py`, `tests/test_resources.py` | `test` |
| Initiatives, bindings, roadmap | `factory/plan.py`, `factory/binding.py`, `factory/roadmap.py`, `factory/templates/initiative.md` | `tests/test_plan.py`, `tests/test_binding.py`, `tests/test_binding_cli.py`, `tests/test_roadmap.py` | `test` |
| Project evidence and PR feedback | `factory/evidence.py`, `factory/feedback.py` | `tests/test_evidence.py`, `tests/test_evidence_budget.py`, `tests/test_evidence_transport.py`, `tests/test_feedback.py`, `tests/test_viability_evidence.py` | `test` |
| Dashboard | `factory/dashboard.py`, `factory/dashboard.html`, `factory/architecture.html`, `factory/navigation.css`, `factory/themes.css`, `factory/theme-picker.js`, `factory/motion.js` | `tests/test_dashboard_bind.py`, `tests/test_dashboard_port.py`, `tests/test_factory.py` | `test` |
| Runtime JSON | `factory/runtime_events.py`, `factory/runtime_local.py` | `tests/test_runtime.py`, `tests/test_factory.py` | `test` |
| Inbox briefing | `factory/briefing.py`, `factory/briefing.css` | `tests/test_briefing.py`, `tests/test_viability_evidence.py` | `test` |
| Settings | `factory/settings.py` | `tests/test_settings.py` | `test` |
| Manager console (`chat`) | `factory/chat.py`, `factory/chat.html`, `console/app` | `tests/test_chat.py` | `test` |
| Codebase history | `factory/codebase.py`, `factory/codebase.html` | `tests/test_codebase.py` | `test` |
| Accepted handoff retention | `factory/results.py` | `tests/test_results.py` | `test` |
| Metrics (`stats`) | `factory/stats.py` | `tests/test_factory.py` | `test` |
| Lessons (`learn`) | `factory/learn.py` | `tests/test_factory.py`, `tests/test_lifecycle_consumers.py` | `test` |
| Eval promotion | `factory/promotion.py`, `docs/eval-promotion-contract.md` | `tests/test_promotion.py` | `test` |
| Experiments | `experiments/b1-build/build.py`, `experiments/o1-observation/observer.py` | `tests/test_b1_build.py`, `tests/test_o1_observation.py` | `test` |
