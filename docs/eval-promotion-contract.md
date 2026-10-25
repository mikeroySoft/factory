# Eval promotion contract (#126)

Shared by `factory learn` and the experiment lane. Any claim that a prompt,
skill/instruction, rubric, model, or effort change *improves* a stage must
satisfy this contract. Method inspired by
[Automating eval design and hillclimbing](https://claude.dev/blog/automating-eval-design-and-hillclimbing/);
Factory does **not** call or embed Claude Code `build-eval`/`hillclimb`.

## 1. Splits

| Split | Use |
|---|---|
| `train` | Climber may inspect and tune against. |
| `selection` (optional) | Pick a round winner; never the final claim. |
| `lockbox` | Sealed. Scored rarely (before a promotion claim). **Never** used to choose the climb. Cases never leave it. |
| `fresh` | Post-promotion canary on new tickets; may stand in for the lockbox. |

Re-scoring the same test set every round overfits: keep decisions and
published scores come from the lockbox or fresh tickets only.

## 2. Grader hygiene (before climbing)

- Repeat-run noise estimate: at least 2 repeats per arm (`MIN_REPEATS`); the
  larger per-arm population stdev is the margin any difference must beat.
- Grade the same output twice to check grader stability.
- Separate plumbing noise (non-zero exits, timeouts, missing evidence) from
  model variance; plumbing failures are re-run, not scored.
- Headroom warning: if the baseline is already near-saturated on train, do not climb.

## 3. Climb loop

One attributable change per round (`change.kind` ∈ prompt, skill, lesson,
worker-config, routing, rubric, model, effort, gate-check). Harness changes originate only from the meta loop
(`factory learn` CURATE): add-only edits to `.factory.toml` (new
`[[gate.check]]` tables, appended `[gate].protected_paths`) and files under
the base ref's protected paths, never `.github/`, never deleting or renaming a
protected file. Lessons and CURATE PRs merge through the ordinary gate, reviewer
APPROVE, CI and `factory-approved` path; the keep/revert rule below runs online
on them (`promotion.drift`): the `score` rows of the `PROMOTION_N = 10` attempts
before the merge are the baseline (journaled in `promotion-open`), the first 10
attempts journaled after the merge and scored the fresh split (an attempt that
ran before the merge but was scored after it never counts), and a drop in `efficiency_mean` or
`gate_first_pass_rate` beyond the baseline's population stdev journals
`promotion-revert` and opens `agent/revert-lessons-<pr>`; otherwise
`promotion-keep`. One decision per merge, never replayed. The inner loop (worker
and attempt gate) and the outer loop (reviewer and merge stage) never change the
harness.
Score train (+ optional selection).

- **Keep** only if the train gain exceeds train noise **and** the
  lockbox/fresh mean rises (delta > 0). Held-out keep does **not** require
  beating lockbox/fresh noise: a lockbox bump smaller than lockbox noise
  still keeps today. "Does not regress" means exactly this (train beat
  noise; held-out mean up).
- **Revert** train-flat / train within noise, or train↑ with lockbox flat
  (delta = 0) or down — a train-only win, even when the drop is inside noise.
- On stall, bucket root cause (ambiguous task / bad grader / harness / real
  miss) before another round.

Worker regression claims set `objective: "regression"` and use the same
train/lockbox split and repeat-noise calculation. They keep parity or a drop
within the larger per-arm noise margin; a drop beyond noise in either split
reverts. This is distinct from an improvement claim, which still requires a
train gain beyond noise and a positive held-out delta.

## 3a. Frozen worker suite

`factory/eval-suite.json` version 1 pins 20 merged Factory and rocm-cli tickets,
including the real tickets behind reviewer-calibration plus recent merged work.
Each case carries the repository, issue snapshot, exact pre-work base, accepted
merge, gate checks, acceptance criteria, and train/lockbox membership. Later
versions may add cases; a lockbox case never leaves lockbox.

`factory eval --profile NAME --dry-run` lists all cases and the `[eval]`
case/token/wall budget. A real run uses `MIN_REPEATS`, the selected worker
profile, each target repository's normal gate and reviewer, and the sandboxed
no-GitHub/no-push replay path shared with `factory learn`. Current worker
context is committed before replay, except that Factory cases retain their
pinned `AGENTS.md` because their historical gate validates that file's feature
map. The run journals one `eval` row per case/repeat and one `eval-summary`
containing split means, population-stdev noise, usage, wall time, candidate
head, worker-context fingerprint, and any Prime `scope: global` entries read at
run start. An accepted-main run establishes the baseline. Rejected or unmerged
candidate runs never replace it, and any `revert` summary permanently rejects
that candidate head.

Budget exhaustion or unavailable replay evidence journals `not_tested` and
does not escalate. `[eval] schedule = true` opts the repository into exact-head
merge claims for `.factory.toml`, `.factory-lessons.md`, `AGENTS.md`,
`CLAUDE.md`, the worker setup modules and prompt template, and `SKILL.md` files.
Missing evidence reports the exact `factory eval` command instead of launching
the suite synchronously in the dispatcher. The same setting installs a
persistent weekly `factory eval --scheduled` timer; a scheduled drop beyond
noise is journaled with `flagged: true`.

## 4. Lessons

Generalized rules only, written to `.factory-lessons.md` via `factory learn`.
Never paste raw failing transcripts into prompts.

## 5. Objectives

Each stage has a primary quality metric. Only after quality holds, climb
cost/latency at quality parity (same contract, parity = no quality regression
beyond noise).

## 6. Gate

The deterministic gate remains the hard product verifier. Eval promotion never
weakens the gate, independent review, or merge policy, and never auto-promotes
settings to production.

## Recording a claim

```sh
factory learn --promotion claim.json [--dry-run]
```

`claim.json`:

```json
{"contract_version": 1, "stage": "review",
 "change": {"kind": "model", "detail": "sonnet-5.5"},
 "baseline": "reviewer@cadb998", "candidate": "reviewer@cadb998+sonnet-5.5",
 "splits": {"train":   {"baseline": [0.5, 0.5], "candidate": [0.75, 0.75]},
            "lockbox": {"baseline": [0.5, 0.5], "candidate": [0.5, 0.5]}}}
```

Prints `keep`/`revert` with the reason and appends a `promotion` event to
`.factory/events.jsonl` recording split membership scores, disposition, and
baseline/candidate identity. Invalid claims (missing or unknown `contract_version`, unknown change kind, missing
held-out split, fewer than 2 repeats) are rejected. Logic: `factory/promotion.py`.

## Pilots

1. **Reviewer calibration (#34)** — `experiments/reviewer-calibration`:
   frozen splits in `splits.json`; `score.py <adjudication.json>` reports
   `missed_blocking` and `unnecessary_revise` separately per arm and split with
   repeat noise. Model comparison (current vs Opus / Sonnet 5.5) waits until the
   corpus and noise are adequate; no production reviewer-policy change.
2. **Triage routing** — hard fail: any ticket triage promotes to
   `ready-for-agent` that the adjudicated oracle says is not ready. One hard
   fail scores the repeat 0. Ticket set must be frozen and versioned like
   `splits.json`. Deferred until Pilot 1 has a usable corpus and noise estimate.
