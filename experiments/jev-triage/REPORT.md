# Jev triage shadow experiment — results

Date: 2026-09-16. **Execution complete; human adjudication pending.**

## Decision

**Keep production unchanged. Jev is technically promising for a larger shadow routing trial, but this run does not establish safe admission or qualify a triage replacement.** First adjudicate the three candidate admission problems below and label the held-out cases. The same suspect recommendations occurred in the current local baseline: replacing its model alone would not fix the underlying ambiguity about specification readiness versus authority.

No integration ticket, roadmap amendment, GitHub label/comment, dispatch, merge, deployment, or provider configuration change was made. Owner disposition is pending. This report is evidence for a later roadmap decision, not authorization to implement it.

## Observations

| Measure | Result |
|---|---:|
| Frozen public Factory issues | 40: 10 development, 30 evaluation |
| Shared deterministic `needs-info` decisions | 3: #35, #36, #37 |
| Paired model-routed cases | 37 |
| Jev successful calls / failures | 37 / 0 |
| Local baseline successful calls / failures | 37 / 0 |
| Model-route agreements | 37 / 37 |
| Jev model routes | 29 `ready-for-agent`; 8 `ready-for-human` |
| Jev model `needs-info`, `wontfix-proposal`, `insufficient-evidence` | 0 each |
| Jev median / nearest-rank p95 request latency | 0.212 s / 0.301 s |
| Baseline median / nearest-rank p95 case latency | 15.457 s / 28.916 s |
| Jev replay input / output tokens | 39,920 / 2,553 |
| Estimated Jev cost, including synthetic smoke | **$0.001702092** |
| Paid calls including smoke | 38; no retries |
| Conservative cumulative budget reservation | $1.596 of the authorized $5 cap |

The baseline was the actually configured local `ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M` at `http://127.0.0.1:11435/v1/chat/completions`, not the repository's fallback Qwen setting. Jev was requested as `jev-latest`; every successful response, including smoke, reported **`jev-1.13.0`**. The account's discovery endpoint advertised only `jev-latest` and `jev-preview`; an immutable version was not advertised.

The observed ratio of median latencies is **72.8×**. This is a workflow comparison, not equal-output inference: the baseline also generates a rationale, question and implementation brief, while Jev returns only the typed routing judgment and probabilities. Baseline total measured case time was 612.83 s; Jev's 37 measured requests totaled 8.01 s. These are single observations per case, not repeated performance benchmarks. Local power/hardware cost was not measured, so there is no defensible dollar-cost ratio against the local baseline.

Cost is calculated from returned usage and the published **$0.042/million input tokens, output free** rate, not verified against an account invoice. The smoke used 606 input tokens and cost an estimated $0.000025452. The runner reserves one million tokens before every attempt, caps serialized requests at 100,000 bytes and stops after at most 41 attempts across reruns. Actual largest frozen request was 22,948 bytes. This is a conservative application-side reservation, not a provider-enforced account spending limit.

## Interpretation: agreement can preserve mistakes

An independent agent assessed all 40 frozen cases without reading either model's outputs or current issue state. All 40 evidence quotes were checked as literal substrings of the frozen bodies. These judgments are **provisional agent assessments, not human ground truth**. They disagree with both models on three cases:

| Case | Split | Jev and baseline | Jev confidence | Provisional concern |
|---|---|---|---:|---|
| [#15](https://github.com/mikeroySoft/factory/issues/15) | Development | `ready-for-agent` | 0.63 | New manager `FIX`/`CLOSE`/approval powers may require human-policy review even though implementation details are concrete. |
| [#20](https://github.com/mikeroySoft/factory/issues/20) | Evaluation | `ready-for-agent` | 0.53 | Automated `ROUTE`/`FIX` behavior may change operational authority rather than merely implement a preapproved mechanism. |
| [#62](https://github.com/mikeroySoft/factory/issues/62) | Evaluation | `ready-for-agent` | **0.97** | The body says existing inline-text configurations “keep working,” then requires rejecting their bare `{prompt}` form with `ConfigError`. |

For #15 and #20, a human must decide whether the issue itself sufficiently settles the authority policy. For #62, the exit gate explicitly tests rejection: a human may decide that the precise gate overrides the contradictory “keep working” wording, or require clarification. The baseline's rationale treats the compatibility behavior as already defined; Jev supplies no prose explanation. Do not silently turn this interpretation dispute into an accuracy label.

**A confidence-only admission rule would not flag #62 at a 0.95 cut.** This is a concrete reason not to equate a concentrated answer distribution with verified readiness. It is not proof of a confirmed model error until adjudication settles the intended contract.

## Confidence coverage, not validated thresholds

No prompt or threshold was tuned after development outputs. These cuts were declared descriptively before replay. On the **28 model-routed held-out cases** (the other two are deterministic):

| Minimum confidence | Retained model judgments | `ready-for-agent` judgments among retained |
|---|---:|---:|
| 0 | 28 / 28 | 21 |
| 0.50 | 24 / 28 | 20 |
| 0.80 | 14 / 28 | 13 |
| 0.95 | 6 / 28 | 6 |

All eight Jev `ready-for-human` judgments are #21 and #96–#102. Only #96 has confidence at least 0.80. Applying a high cutoff indiscriminately would discard many appropriate-looking human referrals, not just questionable admissions. No threshold is recommended from this data, and no raw judgment caused an action.

## Validity and limits

- **Historical framing:** 39 never-edited public bodies plus the publicly recovered creation version of #5; no selected title had an observed public rename. All comments are omitted. This is creation-title/body-only replay, not the full input that historical triage actually saw. Public history may be incomplete.
- **Leakage control:** model input contains only issue number, title, body and empty comments. No labels, current state, provenance, timelines or later comments were sent. Nine explicit outcome/routing-bearing bodies were excluded. Selection and exclusions are in DATASET.md.
- **Selection bias:** one author, one repository, eight days, mostly detailed implementation tickets. No suitable known wontfix/duplicate/invalid example was available. The three obvious under-specified cases were resolved by deterministic code, so this does not test Jev's missing-information sensitivity well.
- **Contract differences:** Jev has a fifth explicit abstention option and a more explicit authority-precedence instruction. The baseline has four routes and generates text. Identical issue evidence does not make this a controlled model-only comparison.
- **Ground truth:** 37/37 agreement is not 100% accuracy. Neither historical labels, the baseline, nor the independent assessing agent establishes truth. False-admission rate and probability calibration remain unmeasured.
- **Reproducibility:** exact inputs, requests, responses, hashes and resolved model IDs are retained. The requested alias can change; one observation per case does not characterize model consistency. The baseline records configured model identity and raw answer text, not server build or quantization hashes.
- **Credentials:** .env was already user-supplied, was not executed as shell code, and only its key was used for API authentication. `/.env` was added to .gitignore and verified ignored/untracked. No key is included in retained experiment artifacts.

## Verification and review

Executed with Python 3.14.7:

```sh
python experiments/jev-triage/check.py
python experiments/jev-triage/run.py smoke experiments/jev-triage/smoke
python experiments/jev-triage/run.py replay experiments/jev-triage/replay
python experiments/jev-triage/baseline.py experiments/jev-triage/baseline
python experiments/jev-triage/summarize.py
```

The offline boundary check rejects nonfinite confidence, unknown choices, missing/non-normalized probability distributions and input usage above reservation. The actual smoke and replay exercised authentication, response parsing, persistence and the complete network path. Both replay processes exited successfully with all 40 cases recorded. No product test suite was used as a substitute for running the experiment.

Independent validity review found two reporting defects before summary generation: deterministic `needs-info` routes were attributed to Jev, and a partial running baseline could be summarized. Both were corrected. The summary now separates deterministic/model distributions, exposes baseline status, and refuses a running baseline. That refusal was exercised while the baseline was running; the corrected summary then ran successfully on completed evidence.

## Retained artifacts and next decision

- [PROTOCOL.md](PROTOCOL.md), [DATASET.md](DATASET.md), [cases.json](cases.json), [questions.json](questions.json): frozen design and inputs.
- [summary.json](summary.json): reproducible measurements.
- [replay/](replay/), [smoke/](smoke/), [baseline/](baseline/): manifests and raw results. Jev frozen subdirectories retain manifest-matched inputs/code/protocol; factory-source retains the imported triage/config implementation.
- [agent-assessment.json](agent-assessment.json), [provisional-differences.json](provisional-differences.json): output-blinded provisional source assessment and the three disagreements.
- [human-review.json](human-review.json): per-case worksheet; `human_label` and `human_evidence` intentionally remain null. This worksheet shows model results, so review using it is not blinded. For blinded human labeling, start with cases.json instead.
- [attempts.jsonl](attempts.jsonl): persistent paid-attempt reservations, including smoke; do not delete to reset the cap.

Recomputing `summarize.py` and running `check.py` is offline. Do not rerun the paid commands expecting replay caching: output directories must be new and budget reservations persist. Only three attempted calls remain under the frozen 41-attempt protocol; another full replay requires a new explicit experiment decision, not deletion of the ledger.

**Proposed next decision:** adjudicate #15/#20/#62, then label the held-out cases with a settled definition of authority-changing work. If the resulting admission error profile is acceptable, authorize a larger read-only shadow trial with genuinely incomplete, conflicting and no-match cases. Keep prose generation, deterministic checks and operational authority separate. Do not promote this result directly to automated admission.

Sources: [TypeSafe API](https://docs.typesafe.ai/api.md), [Choice](https://docs.typesafe.ai/primitives/choice.md), [published pricing](https://typesafe.ai/blog/introducing-system-one-models-and-jev).
