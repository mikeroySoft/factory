# SWE-Gate (arXiv:2609.04167) Verification Report — Issue #34

## Sources examined (all fetched directly, no synthesis)

| # | Source | Type | Retrieved as |
|---|---|---|---|
| S1 | https://arxiv.org/abs/2609.04167 | Primary — abstract | arXiv API markdown |
| S2 | https://arxiv.org/pdf/2609.04167v1 | Primary — full text | PDF→markdown (pages 1–11 of 11; ends at References, no appendix present) |
| S3 | https://github.com/DeepSoftwareAnalytics/SWE-Gate | Primary — replication package | GitHub API, HEAD `5f303b30dd53af3499d05558cfd82eda641be22b` |
| S4 | `.../commit/0265e9823de8c293ac70ab1d425d838c0a673439` | Primary — release commit | GitHub API (300 files, +203730/−0, 2026-09-02T07:39:51Z) |
| S5 | `data/dataset_info.json` @ main | Primary — machine-readable manifest | raw.githubusercontent.com |
| S6 | `config/{seed,instance}_repos.txt` @ main | Primary — repo pool config | raw.githubusercontent.com |
| S7 | `data/instances/` tree (318 lines / directory) | Primary — instance inventory | GitHub tree API |
| S8 | `data/model_predictions/` + its README | Primary — prediction archive layout | GitHub tree API |
| S9 | `docs/validation_coverage.md` | Primary — stated limitation doc | raw.githubusercontent.com |
| S10 | https://codex.danielvaughan.com/2026/09/04/... | Secondary blog #1 | native fetch |
| S11 | https://dev.to/mech_app_ai/swe-gate-... | Secondary blog #2 | dev.to API |
| S12 | https://github.com/DeepSoftwareAnalytics/SWE-Gate/issues | Primary — issue tracker | GitHub API (1 open issue, HF-hosting request, no errata) |

## 1. Core counts — VERIFIED (primary source, internally self-consistent)

**303 instances / 75 repositories.** Stated in the paper's Abstract, Introduction ('303 repository-level repair instances spanning 75 open-source Python repositories'), and Dataset Characteristics section (§ Benchmark, 'The current version of SWE-Gate contains 303 repository-level repair instances covering 75 open-source Python repositories'). Independently corroborated two ways:
- `data/dataset_info.json` (S5) has the hard field `"instance_count": 303`.
- Parsing the 303 directory names under `data/instances/` (S7) and mapping each embedded repo token against the known repo lists in `config/{seed,instance}_repos.txt` (S6) yields **exactly 75 distinct repositories** (e.g. nltk, datasets, mlflow, arq, cookiecutter, boltons, rich, peewee, beanie, bottle, anyio, great_expectations, pytest-cov, huey, hyperlink, starlette, marimo, pyjwt, tavern, dynaconf, arrow, tornado, apscheduler, asyncpg, hatch, poetry, textual, cleo, flake8, crawlee-python, trafilatura, scikit-image, pydantic-settings, aerich, rq, bandit, numba, build, cattrs, more-itertools, rasa, coveragepy, falcon, pytest-testinfra, sanic, pytest-xdist, tortoise-orm, python-fire, fusesoc, klein, locust, mkdocs, mkdocstrings, msgpack-python, msgspec, networkx, openapi-spec-validator, panel, pendulum, piccolo, pluggy, prefect, pydata-sphinx-theme, pyod, pyramid, pytest-bdd, python-dotenv, python-prompt-toolkit, quart, schemathesis, statsmodels, syrupy, tomlkit, typer, virtualenv). This is a manual directory-name tally, not a byte-exact machine recount of a `repo` field in `swe_if_instances.json` (that file was too large to fetch whole in this session) — flagged as `[INFERENCE: corroborating, not exhaustive-machine-verified]`.

**221 / 644 — VERIFIED and denominator clarified.** Table 3 (Evaluation §RQ1) reports per-model functional passes (`F. Pass`) and hidden failures: GPT-5.5 227/67, GPT-5.4-mini 187/67, DeepSeek-V4-Flash 202/72, GPT-4o-mini 28/15 → totals **644 F.Pass, 221 hidden failures, 34.3%**. I independently recomputed every cell from Table 2's `F. Pass`/`J. Pass` columns (227−160=67, 187−120=67, 202−130=72, 28−13=15; sums 227+187+202+28=644 and 67+67+72+15=221) and both totals match the paper's own arithmetic exactly. **644 is a sum over four separate 303-instance model runs (i.e., 4×303=1212 model-instance pairs, of which 644 pairs happened to pass the functional suite), not a count of unique benchmark instances** — the same 303 instances are evaluated once per model, so 644 double-, triple-, or quadruple-counts instances that multiple models solved.

## 2. Rate definitions and denominators (Evaluation §, verbatim formulas)

Let N = 303 (per model), N_F = functional-passing patches, N_F∩C = patches passing both suites:
- **FSR** (Functional Success Rate) = N_F / N — over *all instances*.
- **CFR** (Constraint Following Rate) = N_F∩C / N_F — over *functionally-successful repairs only* (this is the rate that '221 of 644' feeds: **HFR = 1 − CFR = (N_F − N_F∩C) / N_F**, denominator N_F=644 summed across models, **not** N or N×4=1212).
- **JSR** (Joint Success Rate) = N_F∩C / N — over *all instances*; paper calls this 'the primary metric of SWE-Gate.'

This distinction matters for issue #34: 221/644 = 34.3% is a **conditional** rate (of patches that already passed functional tests, how many are hidden failures), not 221/1212 = 18.2% (share of all model-instance attempts) and not 221/303 (share of unique tasks). Blog S11 conflates these (see §5).

## 3. Per-model tables — meaning, reproduced

**Table 2 (Constraint-Provided, +C — the benchmark's primary/intended condition):**

| Model | F.Pass | J.Pass | FSR | CFR | JSR |
|---|---|---|---|---|---|
| GPT-5.5 | 227 | 160 | 74.9% | 70.5% | 52.8% |
| GPT-5.4-mini | 187 | 120 | 61.7% | 64.2% | 39.6% |
| DeepSeek-V4-Flash | 202 | 130 | 66.7% | 64.4% | 42.9% |
| GPT-4o-mini | 28 | 13 | 9.2% | 46.4% | 4.3% |

All five derived percentages per row were recomputed from the two raw integer columns (F.Pass/303, J.Pass/F.Pass, J.Pass/303) and match the paper to the reported decimal in every cell.

**Table 4 (−C vs +C ablation, i.e. omitting vs. providing the natural-language constraint text to the agent):** every model's JSR rises when the constraint is provided (GPT-5.5 41.3→52.8, +11.5pp; GPT-5.4-mini 36.3→39.6, +3.3pp; DeepSeek-V4-Flash 38.0→42.9, +4.9pp; GPT-4o-mini 3.3→4.3, +1.0pp), and CFR rises for all four (+10.2 to +25.6pp), but FSR *drops* for three of four models (largest: GPT-4o-mini −6.6pp) — the paper explicitly cautions this is 'an observed trade-off under the controlled input ablation rather than a general causal claim' because each cell is a single generation per model/instance (no repeated trials, no variance estimate). Recomputed joint-success totals (125+110+115+10=360 under −C vs. 160+120+130+13=423 under +C) match the paper's stated 'increases from 360 to 423' exactly.

**Table 3** is the hidden-failure table underlying the abstract's headline 221/644/34.3% figure (see §1).

**Table 5 (RQ3, per constraint-category breakdown, +C only):** rows are explicitly **multi-label and not mutually exclusive** — the paper states outright 'rows are not mutually exclusive and their counts should not be summed' (row N's sum to 713 against 303 instances, confirming overlap). Findings: for the three stronger models, the *hardest* categories by CFR are Scope Generalization (46.3–63.0%) and Lifecycle Cleanup/Resource (53.8–62.5%); the *easiest* are Missing-vs-Empty/Sentinel Distinction (74.2–81.6%) and Ordering/Argument Preservation (75.0–79.6%). GPT-4o-mini's category CFRs are explicitly flagged by the authors as unreliable due to too few functional successes per category ('should not be interpreted as superior constraint following').

## 4. Constraint taxonomy — full, not the abbreviated blog version

The paper's actual multi-label taxonomy (Table 5, all ten categories with N): **Error semantics (152, 50.2%)**, **Schema/metadata/typing (143, 47.2%)**, Ordering/argument preservation (86), Encoding/escaping/quoting (74), Scope generalization (62), Compatibility/deprecation (55), Missing-vs-empty/sentinel distinction (51), Performance/structure (41), Idempotence/duplicate processing (30), Lifecycle cleanup/resource (19). Blog S10 lists only six of these (omitting Compatibility/Deprecation, Missing/Sentinel Distinction, Performance/Structure, Idempotence/Duplicate Processing) and mislabels it 'six categories' — an incomplete but not fabricated secondary claim. Blog S11's taxonomy ('style/formatting, architectural patterns, security boundaries, performance expectations, maintainability requirements') **does not correspond to any category in Table 5 or the in-text taxonomy list** — it is unsupported.

## 5. Does SWE-Gate measure reviewer accuracy or unnecessary review demands? — NO, and this is the key finding for #34

SWE-Gate's construction pipeline (Benchmark §, 'Constraint Extraction' and 'Quality Assurance' subsections) is explicitly designed to *pre-validate* every constraint as a legitimate engineering requirement before any agent ever sees it: an LLM extracts atomic suggestions from review comments; deterministic + LLM-based candidate filtering rejects requests that are workflow-only, documentation-only, vague, or 'merely restate the functional issue'; only candidates where 'the review adds rather than restates a requirement' survive; a final manual pass again verifies 'the constraint remains faithful to its review-derived seed.' In other words, **every constraint in the released 303 instances has already been filtered to exclude illegitimate/over-strict/unnecessary demands by construction.** The subsequent evaluation (RQ1–RQ3) then measures only whether the *agent's patch* satisfies these pre-validated constraints. This means:
- SWE-Gate is a **one-sided, agent-capability benchmark** (does the agent under-deliver relative to a known-good requirement?), not a **reviewer-calibration benchmark** (is the reviewer's/gate's demand itself correct, proportionate, or over-strict?).
- It supplies **zero data** on how often real review comments/requested changes turn out to be unnecessary, over-engineered, or incorrect demands — the rejection-rate/rejection-taxonomy data that *would* speak to that ('Stage-wise candidate counts and rejection reasons are reported in the supplementary material') is **referenced four times in-text but the supplementary material itself is not present in the fetched arXiv v1 PDF** (paper ends at References, page 11; no appendix pages were retrieved) — this is an access gap, stated honestly rather than inferred.
- RQ2's FSR-drops-when-constraint-is-provided finding is adjacent but distinct: it shows *true* requirements can compete with functional correctness during repair, not that reviewers over-demand.

**Conclusion for #34:** no primary evidence in this paper or its replication package supports a 'dual-sided calibration' claim (i.e., that AI/human reviewers are simultaneously missing real defects *and* raising unnecessary/over-engineered demands). SWE-Gate only evidences the first half (functional-only eval misses real, already-legitimated requirements). The paper cites one directly adjacent related work — Yu et al., 'Does Pass Rate Tell the Whole Story? Evaluating Design Constraint Compliance in LLM-Based Issue Resolution' (arXiv:2604.05955, ref [60]) — as a prior benchmark that 'attaches design constraints but relies on LLM-based verification,' and separately cites LLM-judge-bias literature (refs [61] Zheng et al. MT-Bench/Chatbot Arena, [62] Wang et al. 'Large Language Models are not Fair Evaluators') purely as related-work caveats about judge subjectivity — SWE-Gate does not reuse or measure against these; they are not primary evidence of over-engineering/false-positive review demands either, only tangential citations. `[INFERENCE: these related-work pointers are the closest available leads if #34 wants to chase a genuinely dual-sided source, but neither was read in full here and neither is SWE-Gate itself.]`

## 6. Sampling / synthesis / construction limitations (author-stated, not inferred)

- **Synthetic instances, not the original issue–PR pairs.** SWE-Gate deliberately does *not* reuse the source issue/PR 1:1 ('reusing the source issue–pull request pair preserves a public one-to-one correspondence... [creating memorization risk]'); instead it abstracts the bug pattern + constraint and re-instantiates it in a *different* repository via an LLM/agent-driven synthesis pipeline (mutant patch, functional test, constraint test, non-compliant patch, gold patch all LLM/agent-generated, then Docker-validated). The authors state this 'mitigates direct solution memorization **without claiming to eliminate all training-data contamination**.'
- **LLM-assisted construction and screening bias, acknowledged by the authors in the Conclusion**: 'Incorporating broader maintainer feedback into instance construction could... reduce potential bias introduced by LLM-assisted synthesis and screening.'
- **Single Python-only scope**; Conclusion explicitly flags 'extend SWE-Gate beyond Python' as future work.
- **Only executable constraints are covered** by design; Conclusion flags 'develop reliable evaluation methods for review requirements that cannot yet be expressed as executable tests' as an open problem — i.e., constraints that can't be turned into a deterministic test (purely subjective/stylistic taste calls) are out of scope entirely, by construction.
- **Single-sample ablation.** RQ2's ±C comparison is one generation per model per instance per condition — no repeated sampling/seeds, so the FSR/CFR/JSR deltas in Table 4 carry no reported variance and the paper itself declines to make a causal claim from them.
- **Incomplete optional artifacts.** 48 of 303 instances (15.8%) ship without a standalone `validation_matrix.json`; the release explicitly refuses to backfill/fabricate these ('their files have not been reconstructed or fabricated', `docs/validation_coverage.md`).
- **Reproducibility requires network access.** Per `SLIM_PACKAGE.json`, full repo source snapshots under `data/docker_contexts/base/*` are intentionally omitted from the ≤50MB package; rebuilding requires fetching exact GitHub commits at Docker-build time.
- **Not yet peer-reviewed.** This is an arXiv v1 preprint (submitted 2026-09-03, cs.SE/cs.AI, Sun Yat-sen University + collaborators), no venue/proceedings listed; treat its numbers as author-reported, not independently peer-validated, pending further versions.

## 7. Secondary-source accuracy assessment

| Claim | S10 (danielvaughan.com) | S11 (dev.to) | Paper ground truth |
|---|---|---|---|
| Model roster | GPT-5.5, DeepSeek-V4-Flash, GPT-5.4-mini, GPT-4o-mini ✅ | 'GPT-4, Claude, and two open models' ❌ | GPT-5.5, GPT-5.4-mini, DeepSeek-V4-Flash, GPT-4o-mini |
| Table 2 numbers | Reproduced exactly ✅ | Not reproduced (different, unrelated table) | Verified in §3 |
| 221/644/34.3% | Cited correctly, correct denominator framing ✅ | 'Among 644... 221 failed... 34% false-positive rate' — headline number right but then contradicted by its own table (see below) ❌ | 221/644=34.3%, N_F=644 |
| 'Failure Mode Breakdown' table (359 functional fails/35.8%, 221 constraint fails/22.0%, 423 both-pass/42.2%) | N/A | Fabricated — not in paper. 1212 total model-instance pairs − 644 F.Pass = 568 functional fails (not 359); 221/1212=18.2% (not 22.0%); the table's own three rows (359+221+423=1003) don't even sum to 1212 or 644 ❌ | No such table exists |
| Constraint taxonomy | 6 of 10 categories, correct % for the 2 largest ⚠️ (incomplete) | Style/architecture/security/performance/maintainability — none match Table 5 ❌ | 10 categories, Table 5 |
| 'Constraint Violation Patterns' (missing type hints 18%, duplicated code 15%, wrong abstraction layer 12%, hardcoded values 11%, incomplete docstrings 9%) | N/A | Fabricated — no such breakdown appears anywhere in the paper ❌ | Not present |

**Recommendation:** cite S1/S2 (arXiv) and the replication package (S3-S9) as the sole primary sources for any #34 write-up. S10 is safe to cite only for the Table-2 headline numbers (with the taxonomy caveat noted). S11 should not be cited as evidence at all.

## 8. Access failures (stated honestly)

- The paper's 'supplementary material' (referenced 4×: repository-domain mappings, complete rejection taxonomy/rationales, stage-wise candidate counts) was **not retrievable** — the fetched arXiv v1 PDF ends at the References section (page 11) with no appendix; no separate supplementary PDF/zip was found on arXiv or in the GitHub package.
- `data/dataset/swe_if_instances.json` (the canonical per-instance metadata file with the authoritative `repo` field) was **not fully fetched** — it was large and only referenced by path; the 75-repo figure was instead corroborated by manually parsing the 303 `data/instances/` directory names (§1), which is a strong but not byte-exact cross-check.
- `THIRD_PARTY_NOTICES.md` and direct `config/*.txt`/`data/dataset_info.json` fetches via the `owner/repo:path` selector form returned HTTP 404 on this GitHub mirror; all such files were successfully retrieved instead via `raw.githubusercontent.com/<owner>/<repo>/main/<path>` — noting this path quirk in case it recurs for #34's own citations.

## 9. Explicit non-extrapolation statement

Nothing above should be read as evidence about any internal Factory review-gate, benchmark, or calibration policy. SWE-Gate is an external, single-paper, unpublished-preprint benchmark of four commercial/open LLM backends under Mini-SWE-Agent on 303 synthesized Python repair tasks; none of its numbers were computed against, or are transferable to, Factory's own gate/reviewer without new, first-party measurement.

## Construction summary (verified)

SWE-Gate is a repository-level SWE benchmark, not a reviewer-behavior study: it mines real merged-PR review comments from 10 mature seed repos, uses an LLM to extract atomic, verifiable 'review constraints' from those comments, runs those constraints through deterministic + LLM + manual quality-assurance filters (rejecting vague/unenforceable/functional-restating candidates), then transfers each surviving constraint into a compatible target repo by synthesizing a mutant bug, a functional test, a constraint test, a non-compliant patch (passes functional, fails constraint), and a gold patch (passes both). Four coding-agent backends (GPT-5.5, GPT-5.4-mini, DeepSeek-V4-Flash, GPT-4o-mini) under Mini-SWE-Agent are then scored on all 303 instances under both a Constraint-Provided (+C) and Constraint-Omitted (-C) condition. Because the constraints are pre-validated as legitimate before any model ever sees them, the benchmark's entire signal is one-directional: it quantifies agents' failure to satisfy already-legitimate requirements (functional-only eval is too lenient), and supplies zero data on the opposite failure mode (reviewers/gates issuing illegitimate or over-strict demands / over-engineering pushback). Any 'dual-sided calibration' argument for issue #34 must be sourced elsewhere; SWE-Gate does not supply it.
