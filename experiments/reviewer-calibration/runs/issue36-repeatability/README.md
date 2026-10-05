# issue36 repeatability arm

This is **not a new contract and not a new model**.

After PR #148, `runs/main` already scores `baseline` (`cadb998…`) against `issue36` (`1947fc4…`, prompt-identical to `07861b1…`). Nothing in the repo names a next contract or prompt head. The promotion contract's model comparison (current vs Opus / Sonnet 5.5) is explicitly deferred until the corpus and noise are adequate, and `REPORT.md` still says this corpus is too small for a rate. Issue #141 is a held Jev pre-screen, not a reviewer-contract candidate.

`issue36` is the current best arm: train quality 0.8333 / 0.8333 (mean 0.8333) versus baseline 0.8333 / 0.6667 (mean 0.75, `noise_pstdev` 0.0833). Lockbox is tied at 0.6 / 0.6 (`noise_pstdev` 0.0) on both arms.

This directory is the third run slot. It re-runs that same `issue36` contract, two samples, against the frozen v2 oracles and splits. `CONTRACTS` in `run_calibration.py` is unchanged so the default pair stays baseline vs issue36.

## Run

Completed 2026-10-02 PT on rocm-tank: 11 cases × 2 samples, contract `issue36` only (`1947fc4e3de23e71f64342f2c9cd2570d845db5e`), model `anthropic/claude-fable-5-1`. 22/22 samples rc 0. No sample hit `--max-time 300`, so nothing was retried. Raw outputs are `results.json` and `issue36.<case>.s<n>.md`. There is no `adjudication.json` and no scores. Do not invent rows.

```sh
python3 experiments/reviewer-calibration/run_calibration.py \
  experiments/reviewer-calibration/runs/issue36-repeatability 2 issue36
```

Run from the repository root on rocm-tank. The script clones contract checkouts from `/home/mike/dev/mikeroysoft/factory`.

## How to score, after a real run

`run_calibration.py` writes raw reviews only. Adjudication against `cases/<id>/oracle.json` is separate (same fields as `runs/main/adjudication.json`). Do not invent rows. Then:

```sh
python3 experiments/reviewer-calibration/score.py \
  experiments/reviewer-calibration/runs/issue36-repeatability/adjudication.json
```

Compare to the frozen `runs/main` noise, not to a re-run baseline:

- Train: mean delta versus baseline train must beat **0.0833** (the larger per-arm population stdev; this arm's own train stdev replaces that margin if it is larger).
- Lockbox: keep needs mean delta **> 0**. Lockbox noise of 0 is not a win while quality stays 0.6.

Do not file a promotion claim. A same-contract repeat is not an attributable change, and a lockbox delta of 0 reverts under `factory/promotion.py`. No production reviewer policy or gate change.
