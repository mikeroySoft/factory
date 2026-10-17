# Promotion attempt 2026-10-16: cross-file tracing instruction, tools-enabled path (issue #237)

First claim run through `promotion.decide()` on Pilot 1 (`docs/eval-promotion-contract.md`).
One lever, one round: the deployed reviewer prompt plus one appended sentence, scored on the
**tools-enabled** production path (`run_tools.py`) over the frozen 11-case corpus
(`splits.json` v2, oracles confirmed 2026-10-02), 2 repeats per arm per split.

**Verdict: `revert` — `train gain -0.250 within noise 0.083`.** No production change.

## Arms

| Arm | Contract commit | What it is |
|---|---|---|
| `current` (baseline) | `4390f481ed6743694d35193049dc0242eb4c7784` | `main` as deployed at claim time (`dispatch.review()` unchanged) |
| `candidate` | `70fea0e9cc314916acbe4494e4dc0d0dff21b361` | `4390f48` + the diff below, nothing else |

The candidate is a commit object built with `git commit-tree` on top of `4390f48`; it is on no
branch, so a revert leaves no production change behind. Its entire diff:

```diff
--- a/factory/dispatch.py
+++ b/factory/dispatch.py
@@ -960,6 +960,9 @@ def review(wt: Path, n: int, gate_report: str, expected_head: str) -> tuple[str,
         f"blocking defect; requests to add or remove abstractions must meet the "
         f"same required-fix standard. Do not turn optional suggestions into "
         f"requirements or demand speculative refactoring.\n"
+        f"For every changed flag, profile, default or signature, read each caller/"
+        f"consumer outside the diff and state whether it still holds; cite path:line "
+        f"for each.\n"
         f"Output findings as markdown. REVISE only when required fixes remain; "
         f"optional suggestions alone mean APPROVE. End with exactly one line: "
         f"`VERDICT: APPROVE` or `VERDICT: REVISE`."
```

The sentence sits before the output-format/verdict instruction so the `VERDICT:` line stays
last (the parser in `dispatch.review()` requires it). Model, flags and transport are the
production reviewer's (`anthropic/claude-fable-5-1`, `--auto-approve --max-time 600 --mode
text --no-title`, identical in both arms; see `run_tools.py` docstring for why).

## Commands

```sh
# candidate commit object (no branch moved)
git add factory/dispatch.py && tree=$(git write-tree) \
  && git commit-tree $tree -p HEAD -m "review: cross-file tracing instruction (candidate, #237)"
# -> 70fea0e9cc314916acbe4494e4dc0d0dff21b361; working tree restored afterwards

# 44 reviewer calls: 2 arms x 11 cases x 2 repeats, 6 workers
python3 experiments/reviewer-calibration/run_tools.py \
  experiments/reviewer-calibration/runs/promotion-20261016 2 current candidate --all

# score both arms, both splits
python3 experiments/reviewer-calibration/score.py \
  experiments/reviewer-calibration/runs/promotion-20261016/adjudication.json

# judge + record
factory learn --promotion experiments/reviewer-calibration/claim-20261016.json --dry-run
factory learn --promotion experiments/reviewer-calibration/claim-20261016.json
```

`run_tools.py` gained `--all` (score the whole frozen corpus, so APPROVE cases feed
`unnecessary_revise`; default stays defect-only) and the two `CONTRACTS` entries above. The
pool went from 4 to 6 workers to fit the wall budget; both arms ran under the same pool.

## Run facts

- 44/44 cells rc 0 first try; no retries; no worktree mutated (`worktree_dirty` empty in all).
- Wall time: **1557 s** (25.9 min) at 6 workers; budget was 90 min.
- Reviewer time summed over cells: 8710 s (`current` 3943 s, `candidate` 4767 s; candidate
  cells are ~21% longer, consistent with the extra tracing it was told to do).
- Cost: token usage is not captured on this path (`--mode text` emits no usage block); the
  budget unit is calls, and 44 = the ceiling `2 × 2 × |corpus|`.
- Budget: existing configured reviewer only, public repository evidence only, no model change.

## score.py output

```json
{
  "current": {
    "train":   {"cases_per_repeat": 6, "repeats": 2, "missed_blocking": 1, "unnecessary_revise": 0,
                "quality_per_repeat": [0.8333, 1.0], "noise_pstdev": 0.0833},
    "lockbox": {"cases_per_repeat": 5, "repeats": 2, "missed_blocking": 3, "unnecessary_revise": 0,
                "quality_per_repeat": [0.6, 0.8], "noise_pstdev": 0.1}
  },
  "candidate": {
    "train":   {"cases_per_repeat": 6, "repeats": 2, "missed_blocking": 1, "unnecessary_revise": 3,
                "quality_per_repeat": [0.6667, 0.6667], "noise_pstdev": 0.0},
    "lockbox": {"cases_per_repeat": 5, "repeats": 2, "missed_blocking": 3, "unnecessary_revise": 1,
                "quality_per_repeat": [0.8, 0.4], "noise_pstdev": 0.2}
  }
}
```

| Arm | Split | missed_blocking | unnecessary_revise | quality per repeat | noise |
|---|---|---|---|---|---|
| current | train | 1 / 12 | 0 / 12 | 0.833, 1.000 | 0.083 |
| current | lockbox | 3 / 10 | 0 / 10 | 0.600, 0.800 | 0.100 |
| candidate | train | 1 / 12 | **3** / 12 | 0.667, 0.667 | 0.000 |
| candidate | lockbox | 3 / 10 | **1** / 10 | 0.800, 0.400 | 0.200 |

## decide() verdict

`factory learn --promotion experiments/reviewer-calibration/claim-20261016.json --dry-run` →

```
revert: train gain -0.250 within noise 0.083
```

Recorded (non-dry) as a `promotion` event in the configured root's `.factory/events.jsonl`,
same line. Train mean fell 0.917 → 0.667; the contract stops there (train must beat noise
before the lockbox is consulted). For the record the lockbox mean was down (0.700 → 0.600,
within its 0.2 noise), which would also have been a revert.

## Per-cell adjudication (REVISE-oracle cases and every wrong verdict)

Labels follow `runs/rerun-20261016/README.md`: `reported` (blocking), `reported_nonblocking`
(raised only under Optional), `missed`. On REVISE-oracle cases, extra required fixes that cite
an acceptance criterion or a README/docs rule count 0 `unnecessary_demands` (every such finding
here did cite one). Full rows in `runs/promotion-20261016/adjudication.json`; raw outputs in
`<arm>.<case>.s<n>.md`.

| Cell | Verdict | Primary defects | Scored as | Note |
|---|---|---|---|---|
| current `12-before` s1, s2 | REVISE, REVISE | D1 reported ×2 | ok | string-form `manager.command` → char list, `doctor` prints `manager: o`, both runs |
| current `62-before` s1 | REVISE | D1 reported, **D2 missed** | missed | template profile regression blocked; `stats` double-count never mentioned |
| current `62-before` s2 | REVISE | D1 reported, D2 reported_nonblocking | ok | D2 under Optional ("may be intended; if not, mirror `dispatch.py:376`") |
| current `20-before` s1 | APPROVE | D1 missed | missed | |
| current `20-before` s2 | REVISE | D1 reported | ok | the non-fast-forward push after a `conflict` FIX, with the `CalledProcessError` → `mechanism_failure` chain and the `--force-with-lease` precedent — first tools-enabled detection of #20's D1 in any run (`REPORT.md` §8 had 0/4) |
| current `79-before` s1, s2 | REVISE, REVISE | D1 missed ×2 | missed ×2 | both REVISE for snapshot cadence / rate-limit fan-out (cites the "preserve full-snapshot cadence" criterion); `updated_at` never mentioned |
| candidate `12-before` s1, s2 | REVISE, REVISE | D1 reported ×2 | ok | same finding as current; s1 adds a non-table `[manager]` `TypeError` trigger |
| candidate `62-before` s1 | APPROVE | D1 reported_nonblocking, **D2 missed** | missed | its consumer trace *looked at* `stats.py:167` and wrote "counted in `escalation_count`; matches 'so `factory stats` can count it'" — read the line, judged it holds |
| candidate `62-before` s2 | REVISE | D1 reported_nonblocking, D2 reported_nonblocking | ok (by scorer) | REVISE only for an out-of-oracle `human_activity` ordering finding (cites CHANGELOG); both oracle defects demoted to Optional. `score.py` counts a REVISE with no `missed` label as a hit; this cell is a hit for the wrong reason |
| candidate `20-before` s1 | REVISE | D1 reported_nonblocking | ok (by scorer) | REVISE for a stale `factory-approved` label after FIX (cites README:99-100); D1 is Optional ("fail-closed, outside the exit gate") |
| candidate `20-before` s2 | APPROVE | D1 missed | missed | consumer table covered `apply`, `parse`, `Config.workers`, `worker_round`, merge stage — not the push |
| candidate `79-before` s1 | REVISE | D1 missed | missed | REVISE for `producer.revision` reporting a foreign repo HEAD (cites schema-1) |
| candidate `79-before` s2 | APPROVE | D1 reported_nonblocking | missed | Optional 6 states D1 exactly ("REST reviews expose `submitted_at`, not `updated_at` … `source_updated_at` is always null") and approves |
| candidate `62-after` s1 | REVISE | — | unnecessary | 1 required: `manager_failed` lifecycle outcome `project_escalation` vs README event contract |
| candidate `9-approve` s2 | REVISE | — | unnecessary | 1 required: opted-in PRs beyond the 100-PR GraphQL window drop from the queue |
| candidate `8-approve` s2 | REVISE | — | unnecessary | 1 required: README "does not monitor CI" sentence now false |
| candidate `20-after` s2 | REVISE | — | unnecessary | 1 required: same stale `factory-approved` finding as candidate `20-before` s1, on the fixed head |

Every `current` APPROVE-oracle cell returned APPROVE with "Required fixes: none" (14/14).

## What the lever did not fix

1. **It did not close the compositional miss.** The instruction names "flag, profile, default
   or signature". The three persistent misses are none of those: `20-before` D1 is a push
   argument's interaction with a profile that rewrites history (0/2 → 0/2 by verdict; the
   one candidate run that saw it filed it as Optional), `62-before` D2 is a consumer that
   *does* appear in the trace and was judged to hold, `79-before` D1 is a REST field name
   (0/4 blocking across both arms; the candidate found it once and approved anyway). Each
   arm: 4 misses of its 8 REVISE-oracle cells. The lever moved where the misses sit, not how
   many there are.
2. **It bought unnecessary REVISEs with the extra reading.** 0/14 → 4/14 per arm across the
   APPROVE-oracle cells. Each was a plausible cross-file or doc-contract finding the reviewer was now
   instructed to go looking for, promoted to Required with a cited rule. Two of them
   (`20-after` s2, `62-after` s1) are the same findings the candidate raised on the matching
   `-before` heads, i.e. they are head-independent claims about `main`, outside the diff's
   obligation.
3. **The scorer's REVISE-by-any-reason hit is now visible.** Two candidate cells
   (`62-before` s2, `20-before` s1) score as hits with every oracle defect demoted to
   Optional. `score.py` treats `reported_nonblocking` as not-missed when the verdict is
   REVISE; that was harmless while required fixes were oracle defects, and it is not now.
   Not changed here (frozen scorer for this claim); flag for the next round.
4. **Cost without quality.** +21% reviewer seconds per cell, +0 detections.

`current` itself on this path: train 0.917, lockbox 0.700. The deployed reviewer's weakness
on the tools path is still `79-before` (REST `submitted_at`/`updated_at`, 0/2) and `20-before`
(1/2) — what `REPORT.md` §8 called multi-file compositional defects. Prompting the reviewer
to trace consumers of changed flags/defaults/signatures is not the lever for those: the
defects are not at a changed signature's consumer; they are at an unchanged API's contract
(`dispatch.run` `check=True`; GitHub REST schema) composed with the diff.

Out of scope and untouched: model/provider, forced-disagreement reviewer (#35), per-criterion
coverage pre-screen (#141), the gate.
