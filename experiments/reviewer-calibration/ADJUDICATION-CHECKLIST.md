# Human adjudication checklist (issue #34 corpus growth)

Michael Roy confirmed all seven contestable cases on 2026-10-02 after reading each frozen packet (not a glance). `expected_verdict` and findings were not changed. Splits membership was not changed. Historical model verdicts stay discovery-only and are not truth.

## Confirmed 2026-10-02 by Michael Roy

| Case | Split | Confirmed oracle | Status |
|---|---|---|---|
| `62-before` | train | REVISE — D1 (ci-fix/conflict guidance dropped to `@{prompt}`) and D2 (`stats` double-counts `manager_failed`) are blocking | confirmed |
| `62-after` | train | APPROVE — both defects fixed at `68b5d164…`; no new primary defect | confirmed |
| `79-before` | lockbox | REVISE — D1 (REST `updated_at` on reviews) is blocking vs #79 schema-1 update-time criterion | confirmed |
| `79-after` | lockbox | APPROVE — GraphQL `updatedAt` closes D1; no new primary defect | confirmed |
| `9-approve` | train | APPROVE — external review-queue dashboard work meets #9 exit gate | confirmed |
| `8-approve` | train | APPROVE — CI/merge-readiness implementation meets #8 exit gate | confirmed |
| `87-approve` | lockbox | APPROVE — evidence build/schema identity meets #87 exit gate | confirmed |

These seven are no longer contestable and no longer need Michael. Do not rewrite an oracle because a later model run disagrees.

## If a future case is contested

Mark it **confirm** / **revise oracle** / **drop case**. Edits: change that case's `oracle.json` findings and `expected_verdict` only; bump `splits.json` `version` if membership changes. Never move a case out of lockbox — drop and replace with a new id instead.

If an APPROVE case should instead be REVISE, write the primary defect with `path:line` evidence in that case's `oracle.json` and set `expected_verdict` to `REVISE` before the next calibration run.

## Not part of this confirmation

Pilot cases `12-before`, `12-after`, `20-before`, and `20-after` were already in the scored baseline. They were not in the contestable set and were not re-adjudicated here.

Still out of scope: production reviewer prompt or gate policy changes, auto-promotion claims, and treating scores as a population rate. Rescoring the grown corpus (`run_calibration.py`, then `score.py`) is separate from confirmation and does not revise these oracles.
