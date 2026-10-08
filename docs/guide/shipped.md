# Shipped and not shipped

Read this before treating a markdown file under `docs/` or `experiments/` as runtime behavior.

## Shipped on origin/main

The CLI surface is `factory/cli.py`: `init`, `doctor`, `install`, `triage`, `dispatch`, `manage`, `gate`, `stats`, `learn`, `dashboard`, `chat`, `evidence`, `plan`, `codebase`.

`README.md` matches that surface, including the dispatch pass order, manager decisions, merge preconditions, handoffs, the execution-event journal, schema-1 runtime JSON, and schema-1 project evidence.

`skills/factory/SKILL.md` is the agent runbook for setup, viability, ticket shape, diagnosis, and the lock invariant.

`docs/eval-promotion-contract.md` is the contract for `factory learn --promotion`. `factory/promotion.py` accepts or rejects a claim. The contract says promotion never weakens the gate, review, or merge policy, and never auto-promotes settings.

`factory/architecture.html` is the dashboard atlas at `/atlas`. It is an in-product map, not this guide, and not `docs/index.html`.

## Experiment, not production policy

`experiments/reviewer-calibration/` measures the reviewer on frozen cases. `REPORT.md` states that the directory owns only itself and that no production reviewer contract, prompt, gate, docs, issue label, or hold was changed. Rescoring through 2026-10-02 is explicitly not a promotion claim.

Do not describe calibration scores as the live reviewer rules. The live reviewer rules are the prompt and parser in `factory/dispatch.py` `review`.

## Plans and historical handoffs

These files are in the tree. They are not the operator contract:

- `docs/manager-plan.md`
- `docs/fm-console-plan.md`
- `docs/autonomy-execution-plan.md`
- `docs/codebase-history-plan.md`
- `docs/c1-read-interface-ticket.md`
- `docs/c1-read-interface-handoff.md`

Use them as design history. Confirm a behavior in `factory/*.py` or in `README.md` before documenting it as current.

`docs/index.html` is the marketing page. Its title line is not the architecture. Leave that file in place.
