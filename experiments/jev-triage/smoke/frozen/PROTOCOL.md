# Jev triage shadow experiment

Date: 2026-09-16. Protocol written before inference. User authorized public Factory issue evidence and a total $5 API cap.

## Question
Can Jev supply useful issue-routing recommendations while keeping deterministic policy and side effects in Factory? This tests routing only, not production replacement: existing triage also generates rationale, clarification questions and agent briefs; Jev does not generate those strings.

## Authority and inputs
Read-only public mikeroySoft/factory issues, frozen locally. No issue edits, comments, labels, dispatch, merge, service changes or production model configuration. Never send .env, private logs or credentials as state. The root .env supplies only TYPESAFE_API_KEY; it is not executed as shell code.

Target: 40 genuine cases, 10 development and 30 evaluation, selected before model outputs. Recover historical bodies when supported by public evidence. If historical versions cannot be recovered, explicitly identify current body-only replay rather than imply historical accuracy; approval is required before substituting that experiment for historical replay. No current labels, closure state, later comments or PR outcomes enter model state. DATASET.md records exact selection, temporal limitations and exclusions.

## Conditions
Reuse factory.triage.deterministic_needs_info unchanged in both conditions. Report deterministic cases separately; their agreement is not evidence about Jev. Baseline uses the configured current triage model and exact existing triage prompt on the frozen issue object, never the live-fetching CLI. Jev receives the same title/body/comments with questions.json. Its fifth insufficient-evidence option is an explicit abstention and a contract difference from the four-way baseline; this is not a controlled model-only comparison. Existing baseline generates extra text; latency is workflow cost, not equal-output model speed.

No reference labels are established by issue state or by either model. Human labels are pending. Agent source-based assessments must be marked provisional, not human ground truth. Until human review: report disagreements and candidate errors, not measured accuracy, calibrated thresholds or proven false-admission rates.

## Model and cost
GET /v1/models authenticated successfully; only jev-latest and jev-preview were advertised. Use jev-latest; immutable version pin unavailable. Preserve the listing, requested model, returned model and timestamp. Alias drift limits exact reproducibility.

Published price: $0.042 per million input tokens; output tokens free. Source: https://typesafe.ai/blog/introducing-system-one-models-and-jev (read 2026-09-16). Billing estimates are not invoices. Bound each serialized request to 100,000 UTF-8 bytes, reserve 1,000,000 input tokens per attempted call ($0.042), and permit at most 41 paid requests including one synthetic smoke call, without automatic retries. Total reservation is $1.722, below $5. The reservation is deliberately conservative, not a provider-enforced account cap. Stop on service error or returned usage exceeding reservation. No other paid provider calls. Baseline is local; report unavailability, do not substitute another service.

## Execution and records
Freeze cases.json, questions.json and this protocol by SHA-256 in each run manifest. Save exact request/response, timing, usage and estimated cost per call. New output directories only; shared persistent attempt reservations prevent reruns from silently resetting the experiment cap. Keep failures visible. One synthetic connectivity/shape check first; stop if it fails. Development first; no prompt tuning in this initial run, so held-out inputs remain untouched by tuning. Fixed descriptive confidence cuts 0, 0.5, 0.8 and 0.95 show abstention/coverage only, not validated operating thresholds.

## Decision rule
No production adoption from this study. A successful technical run plus human-adjudicated held-out evidence of useful routing and acceptable false admissions may justify a larger shadow pilot. Without a working baseline or human reference, decision is inconclusive for replacement even if Jev calls succeed. Report all failures and high-confidence disagreements. Output: report, raw evidence, cost/latency observations and a human review worksheet; no automatically filed integration ticket or roadmap mutation.
