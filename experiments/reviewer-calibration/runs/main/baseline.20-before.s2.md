## Standards (README conventions)

- `factory/manage.py:100-131` — FIX branch records the `manage` event before mutations (`manage_pass` ordering unchanged at `factory/manage.py:219-221`), pushes only on gate PASS, adds `factory-approved` only on a fresh APPROVE, never merges or re-queues. Matches the README text added at `README.md:92-102`.
- `factory/manage.py:69-71` + `factory/manage.py:63-66` — `default`, `ready-for-agent`, `ready-for-human`, `needs-triage` excluded from both ROUTE and FIX via `RESERVED_LABELS`; consistent with "restricted to configured worker labels (not `default`)".
- `factory/templates/factory.toml:32-48` — profiles are opt-in comments, matching the triage brief ("remain host-config that humans apply") and the README wording at `README.md:100-102`. Not a defect, but note: the issue title says "default profiles"; if the intent was active-by-default, this ships them inactive. Triage comment resolves the ambiguity toward opt-in, so no finding.

## Spec (issue #20 exit gate)

- `when` on `[workers.<label>]`: `factory/config.py:288-298`; legacy array form preserved via the `isinstance(entry, dict)` branch at `factory/config.py:290-291`; covered by `tests/test_factory.py:147-165` (mixed array + table, five malformed shapes rejected).
- Prompt lists labels with `when`: `factory/manage.py:199-201`; `parse` receives the filtered map at `factory/manage.py:213`, so unlisted labels fail ROUTE/FIX.
- `ROUTE chore` without a `chore` worker → HUMAN, recorded, no `issue edit`; with one → `--add-label chore`: `tests/test_factory.py:652-675`.
- FIX on red CI runs the `ci-fix` argv, passes guidance + packet, gates, pushes, reviews: `tests/test_factory.py:677-721` (three gate/verdict combinations), `tests/test_factory.py:723-732` rejects `default`, reserved labels, unconfigured labels, non-string worker.
- `dashboard.py:847` exposes `worker_when` (Touches list satisfied).

## Risks (not blocking)

- `factory/manage.py:118-121` — the `human_activity` check at `factory/manage.py:217` runs before `worker_round`, which can take up to `budget_min`. A human taking over during the worker run is not re-detected before push/approve. Same class of window exists for SPLIT/REWRITE; FIX widens it. Not an acceptance criterion.
- `factory/templates/factory.toml:38-40` directs users to `[defaults.workers.<label>]` host config. Whether the host-layer merge accepts table-form worker entries is outside the diff `[INFERENCE]`; the diff only validates the merged `workers` mapping at `factory/config.py:288`.
- `factory/manage.py:116-120` — `dispatch.worker_round` / `dispatch.review` / `dispatch.pr_comment` signatures are not in the diff; the gate's `test: PASS` on the target host is the evidence they match.

VERDICT: APPROVE
