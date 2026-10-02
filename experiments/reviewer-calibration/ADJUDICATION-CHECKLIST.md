# Human adjudication checklist (issue #34 corpus growth)

Agent-frozen oracles below. Confirm or contest before treating scores as claim-grade.
Historical model verdicts were discovery-only; do not rubber-stamp them.

## Already agent-adjudicated (contestable)

For each case: open `cases/<id>/oracle.json` + `sources/diff.patch`, confirm `expected_verdict`
and each `primary` finding (or the empty findings list for APPROVE).

| Case | Split | Ask |
|---|---|---|
| `62-before` | train | Agree D1 (ci-fix/conflict guidance dropped to `@{prompt}`) and D2 (`stats` double-counts `manager_failed`) are blocking vs #62? |
| `62-after` | train | Agree both are fixed at `68b5d164…` and no new primary defect remains? |
| `79-before` | lockbox | Agree D1 (REST `updated_at` on reviews) is blocking vs #79 schema-1 update-time criterion? |
| `79-after` | lockbox | Agree GraphQL `updatedAt` fix closes D1 and no new primary defect remains? |
| `9-approve` | train | Agree external review-queue dashboard work meets #9 exit gate (no blocking demand)? |
| `8-approve` | train | Agree CI/merge-readiness implementation meets #8 exit gate (no blocking demand)? |
| `87-approve` | lockbox | Agree evidence build/schema identity meets #87 exit gate (no blocking demand)? |

Mark each: **confirm** / **revise oracle** / **drop case**. Edits: change `oracle.json` findings
and `expected_verdict` only; bump `splits.json` `version` if membership changes. Never move a
case out of lockbox — drop+replace with a new id instead.

## Still needs Michael if contested

If any APPROVE case should instead be REVISE, write the primary defect with `path:line` evidence
in that case's `oracle.json` and set `expected_verdict` to `REVISE` before the next calibration run.

## Out of scope for this growth PR

- Re-running `run_calibration.py` / refreshing `runs/*/adjudication.json`
- Model comparison (current vs Opus / Sonnet 5.5)
- Production reviewer prompt or gate policy changes
- Auto-promotion claims

