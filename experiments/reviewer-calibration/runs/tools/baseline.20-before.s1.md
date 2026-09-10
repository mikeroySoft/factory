## Standards

Sources: `factory/templates/factory.toml` header ("defaults are shown commented out"), `factory/config.py:45-67` (host-layer/KNOWN_KEYS comments), `README.md:83-105`, `docs/manager-plan.md` §4.3 + T11. No AGENTS.md/CONTRIBUTING.md in repo; CI runs only `unittest` (`.github/workflows/ci.yml:11`), so line-length is not enforced and is not reported.

No hard violations. Judgement calls only:

- `factory/manage.py:54`, `factory/manage.py:65` — `parse()` re-subtracts `RESERVED_LABELS` / checks `worker in RESERVED_LABELS`, but its only caller (`factory/manage.py:199,213`) already passes a pre-filtered `workers` dict. Redundant defense-in-depth; harmless.
- `factory/manage.py:117-131` vs `factory/dispatch.py:1056-1070` — Duplicated Code: the FIX branch re-states the bounce-loop shape (`worker_round` → escalate on FAIL → `git push` → `review` → `pr_comment` → `approve_pr`/`escalate`) inline via `dispatch.*` rather than a shared helper. Control flow is a faithful mirror; no behavioral divergence found.
- `factory/manage.py:104-131` — the `manage` lifecycle scope never sets `execution.review_round` before `dispatch.review` (`factory/dispatch.py:1039,1054` always do), so events emitted during a FIX carry `review_round: null`. `null` is schema-valid (`factory/lifecycle.py:438-439`); audit-trail inconsistency only.
- `factory/config.py:292` — argv validation duplicates the shape used by `manager_settings` (`factory/config.py:228-256`) without a shared validator.
- `factory/config.py:58-59` (`KNOWN_KEYS["workers"] = None`, pre-existing) — with sub-keys now existing under `[workers.<label>]`, a typo like `whn =` is silently ignored rather than reported by `factory doctor`. Pre-existing blind spot, widened by this change.

Verified compliant: table entries merge correctly under existing `merge()` (`factory/config.py:203-208`; `workers` already in `HOST_TABLES`); every `cfg.workers` consumer (`dispatch.py:931,958`, `dashboard.py:698,846`, `onboard.py:223`, `stats.py:316`) still receives `dict[str, list[str]]`; template blocks (`factory/templates/factory.toml:41-47`) follow the commented-out convention; README paragraph (`README.md:93-102`) matches `manage.py` behavior (worker selected by `{data["worker"]}` only at `:118`; push only after gate PASS at `:121-124`; no requeue — FIX returns at `:131` before the `:158-159` relabel).

## Spec

Exit gate, item by item:

1. "ROUTE chore ... without a chore worker is rejected and recorded" — `factory/manage.py:54` rejects unlisted label → falls to HUMAN at `:72`; recorded at `:219` before `apply`. Test: `tests/test_factory.py:652-676` (`configured=False` → `HUMAN`, no `issue edit`).
2. "with one, the label is applied" — `factory/manage.py:149-155`; test `tests/test_factory.py:672-674` asserts `--add-label chore`.
3. "a FIX on red CI runs the ci-fix argv" — `factory/manage.py:117-118` dispatches `{data["worker"]}`; test `tests/test_factory.py:678-724` runs `fix-worker` stub and asserts guidance + packet reached it.
4. "Existing array-form [workers] still loads" — `factory/config.py:290`; test `tests/test_factory.py:147-164` mixes both forms.
5. "manager prompt lists labels with their `when`" — `factory/manage.py:200-201`; asserted at `tests/test_factory.py:672`.
6. "ROUTE and FIX are only accepted for listed labels" — `factory/manage.py:54`, `:65`; test `tests/test_factory.py:726-736` rejects `ci-fix` (unconfigured), `default`, `ready-for-agent`, non-string.
7. "Ship two default profiles in templates/factory.toml: ci-fix ... conflict ..." — `factory/templates/factory.toml:41-47` with the stated semantics. Judgement: they are commented out and the comment at `:37-39` directs them to host config. Literal spec location satisfied and consistent with the template's commented-defaults convention; but unlike `chore` (`factory/config.py:101`) they are not active defaults, so out of the box the manager prompt lists no `ci-fix` and a FIX naming it is rejected until a human copies the block. `README.md:101-102` states this explicitly. Not an exit-gate failure.
8. Touches `dashboard.py` — `factory/dashboard.py:847` exposes `worker_when` in the snapshot; no UI rendering. Compliant with the Touches list.

Scope notes (judgement, non-blocking):

- `factory/manage.py:109-112` — preconditions (`reviewDecision != CHANGES_REQUESTED`, `git branch --show-current` check) are not in #20's text. They match `docs/manager-plan.md` §4.3 (`:143`) and the merge stage's own CHANGES_REQUESTED skip (`factory/dispatch.py:805-807`). Defensive, consistent.
- `factory/manage.py:121-131` — re-gate/push/re-review/re-approve follows `docs/manager-plan.md:283-285` verbatim. #20's gate only says "runs the ci-fix argv"; without the follow-through a FIX would be a dead-end worker run, so this is the minimum coherent implementation, not creep.

Summary: Standards 5 judgement findings, worst = inline duplication of the bounce loop (`factory/manage.py:117-131`). Spec 0 missing requirements, 1 judgement finding (profiles opt-in rather than active defaults, `factory/templates/factory.toml:37-47`).

VERDICT: APPROVE
