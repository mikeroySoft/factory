# Promotion attempt 2026-10-25: structured review tally (issue #386)

Tools-enabled comparison of the current reviewer at `eb611cc794da4008ffaba73cac6108a2ed8af0a1`
(`review-baseline`) with the final #386 prompt at
`0bdaa307b51ce743dcaa7b6d9a6a33b3ab447697` (`review-tally`). Both arms used
`anthropic/claude-fable-5-1`, the frozen `splits.json` v2 corpus, and two repeats.

## Capture

```sh
python experiments/reviewer-calibration/run_tools.py --all \
  experiments/reviewer-calibration/runs/promotion-20261025 2 \
  review-baseline review-tally
```

All 44 cells returned 0 and left their review worktrees clean. Wall time was 1,414.7 s;
reviewer time summed to 3,601.2 s for baseline and 3,922.5 s for candidate. Raw output,
return codes, elapsed time, prompt hashes, and stderr are in `runs/promotion-20261025/results.json`.

## Adjudication

`runs/promotion-20261025/adjudication.json` uses the frozen case oracles. A primary defect
under baseline **Required fixes** or candidate severity `important` is `reported`; the same
defect under **Optional suggestions** or candidate severity `nit` is
`reported_nonblocking` and therefore a `missed_blocking`; absent defects are `missed`.
This rule is identical for both arms. `score.py` produced:

| Arm | Split | missed_blocking | unnecessary_revise | quality per repeat |
|---|---|---:|---:|---|
| review-baseline | train | 2 | 0 | 0.8333, 0.8333 |
| review-tally | train | 2 | 1 | 0.6667, 0.8333 |
| review-baseline | lockbox | 4 | 0 | 0.6000, 0.6000 |
| review-tally | lockbox | 3 | 1 | 0.6000, 0.6000 |

Blocking detection is no worse: train stays at 2 misses and lockbox improves from 4 to 3.
By defect-bearing case, missed cells are `12-before` 0→0, `62-before` 2→2,
`20-before` 2→1, and `79-before` 2→2. Candidate false REVISE cells are
`62-after` sample 1 and `20-after` sample 2; both are retained as errors rather than
relabelled successful reviews.

## `promotion.decide()`

Claim: `claim-20261025.json`.

```text
('revert', 'train gain -0.083 within noise 0.083')
```

The ticket's blocking-safety criterion passes, but the general promotion contract reverts:
the candidate's one train false REVISE lowers mean quality from 0.833 to 0.750, within the
candidate's 0.083 population-standard-deviation noise; lockbox quality is flat at 0.600.
This is the actual decision, not a keep claim.
