# Grown-corpus re-run, 2026-10-16 (issue #34)

Owner-approved re-run of the packet-limited harness on the frozen 11-case corpus
(`splits.json` v2, oracles confirmed 2026-10-02 in `ADJUDICATION-CHECKLIST.md`).
Not a promotion claim. No oracle, packet, split, contract or production reviewer
policy was changed. `runs/main`, `runs/issue36-repeatability` and `runs/tools` are
untouched; this directory is a fourth run slot.

## Frozen inputs

- Contracts: `baseline` `cadb9981524ab5506693e27f56ff8ac4ef911da6`, `issue36`
  `1947fc4e3de23e71f64342f2c9cd2570d845db5e` (the default `CONTRACTS` pair).
- Model: `anthropic/claude-fable-5-1`, `--thinking low`, `--no-tools`, `--max-time 300`
  (the runner's `FLAGS`, unchanged).
- Every one of the 44 `input_sha256` values equals the `runs/main` value for the same
  contract × case, so the prompt + packet bytes are identical to the 2026-10-02 run.

## Commands

```sh
python3 experiments/reviewer-calibration/run_calibration.py \
  experiments/reviewer-calibration/runs/rerun-20261016 2
python3 experiments/reviewer-calibration/score.py \
  experiments/reviewer-calibration/runs/rerun-20261016/adjudication.json
```

44 calls, 836.8 s wall at 4 workers. One cell — `issue36` / `79-before` / s1 — hit the
`--max-time 300` deadline with empty output (`Deadline exceeded`, rc 1), the same cell
that timed out in the 2026-10-02 run. It was retried once through the same
`run_calibration.review()` function (rc 0, 272.8 s); the replaced row in `results.json`
carries the original attempt under `retry_of`. All other 43 cells rc 0 first try.

## Adjudication rules

`adjudication.json` uses the `runs/main` row schema. Mechanical fields: `verdict` is the
last `VERDICT:` line; `required`/`optional` are both true only when the output has both a
`Required…` and an `Optional…` heading; `citations` counts `path:line` tokens
(`[\w/.-]+\.[a-z]+:\d+`). Hand-labelled fields, one adjudicator reading each output
against the frozen `oracle.json`:

- `defects.<id>`: `reported` (blocking), `reported_nonblocking` (raised only under
  Optional / non-blocking), `missed`.
- `unnecessary_demands`: on APPROVE-oracle cases, the number of Required-fix findings
  (every one is outside the oracle by definition); on REVISE-oracle cases, extra
  findings that cite an acceptance criterion or README convention count 0, as in
  `runs/main`.

Per-cell labels that needed a judgement call:

| Cell | Label | Why |
|---|---|---|
| `baseline` `62-before` s1 | D1 reported, D2 missed | R2 is the `ci-fix`/`conflict` command drop; R1 (template/README claim a worker-side rejection) cites the documentation criterion, so not counted as unnecessary, following the `runs/main` precedent for the same finding |
| `issue36` `62-before` s2 | D1 reported_nonblocking | Optional item 1 is exactly D1; verdict APPROVE |
| `issue36` `20-before` s1 | D1 reported | Required fix 1 is the non-force push after a `conflict` FIX, with the `CalledProcessError`/`mechanism_failure` chain — the first packet-limited detection of this defect in any run |
| `baseline` `79-before` s2 | D1 missed | REVISE for `repo` wiring and `producer.revision`; `updated_at` never mentioned |
| `issue36` `79-before` s1, s2 | D1 reported_nonblocking | `submitted_at` vs `updated_at` raised as Optional ("null is defensible") |
| `baseline` `79-after` s2 | 4 unnecessary | Findings 1, 2, 3, 5 are demands; 4 is marked not blocking |

## score.py

```json
{
  "baseline": {
    "train":   {"cases_per_repeat": 6, "repeats": 2, "missed_blocking": 4, "unnecessary_revise": 0,
                "quality_per_repeat": [0.6667, 0.6667], "noise_pstdev": 0.0},
    "lockbox": {"cases_per_repeat": 5, "repeats": 2, "missed_blocking": 3, "unnecessary_revise": 2,
                "quality_per_repeat": [0.6, 0.4], "noise_pstdev": 0.1}
  },
  "issue36": {
    "train":   {"cases_per_repeat": 6, "repeats": 2, "missed_blocking": 4, "unnecessary_revise": 0,
                "quality_per_repeat": [0.6667, 0.6667], "noise_pstdev": 0.0},
    "lockbox": {"cases_per_repeat": 5, "repeats": 2, "missed_blocking": 1, "unnecessary_revise": 2,
                "quality_per_repeat": [0.8, 0.6], "noise_pstdev": 0.1}
  }
}
```

Counts over 44 cells; not a population rate. See `REPORT.md` §9 for the per-case
breakdown, the comparison with `runs/main`, and the contested `79-after` oracle note.
