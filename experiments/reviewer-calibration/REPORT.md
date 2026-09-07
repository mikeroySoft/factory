# Reviewer calibration experiment (issue #34)

Bounded, first-party measurement of factory's reviewer stage on real Factory review
cases. Owns only this directory. No production reviewer contract, prompt, gate, docs,
issue label or hold was changed. The proposed "review-criteria coverage" gate-report
line remains **deferred** (see [Why the coverage line stays deferred](#why-the-coverage-line-stays-deferred)).

- Frozen repository baseline: `cadb9981524ab5506693e27f56ff8ac4ef911da6`
- Worktree/branch: `factory-calibration-34` / `calibration-34`
- Contract arms compared: `baseline` = `cadb998…` vs `issue36` = `1947fc4e3de23e71f64342f2c9cd2570d845db5e`

## 1. Research claims, verified against primary sources

Full ledger with per-table recomputation: [`RESEARCH.md`](RESEARCH.md).

| Claim in #34 | Verdict | Primary source |
|---|---|---|
| SWE-Gate is a new benchmark from Sun Yat-sen University | Confirmed | arXiv:2609.04167 (He, Wang, Liu, Chen, Zhang, Li), v1 submitted 2026-09-03, not peer reviewed |
| 303 instances / 75 Python repositories | Confirmed | paper Abstract + Dataset §; `data/dataset_info.json` field `instance_count: 303`; 75 distinct repos tallied from the 303 `data/instances/` directories |
| "34% of agent patches that pass tests still fail code-review criteria" | Confirmed, with denominator correction | Table 3: 221 hidden failures / 644 functional passes = 34.3%. 644 is a **sum over four separate 303-instance model runs** (4×303 = 1212 pairs), not 644 unique instances. The rate is conditional on already passing functional tests — not 221/1212 (18.2%) and not 221/303 |
| "test passage and review acceptance are different bars" | Confirmed | JSR (joint) is uniformly below FSR (functional) for all four backends; Table 2 |
| Constraint taxonomy | #34's signal understates it | Paper Table 5 has **ten** multi-label categories (Error semantics 152/50.2%, Schema-typing 143/47.2%, Ordering 86, Encoding 74, Scope generalization 62, Compatibility 55, Missing-vs-empty 51, Performance 41, Idempotence 30, Lifecycle 19); rows are explicitly not summable. The `codex.danielvaughan.com` signal lists six of ten |
| **"score the reviewer's verdicts against SWE-Gate-style review-acceptance criteria"** | **Not supported as stated** | SWE-Gate pre-validates every constraint as legitimate before any agent sees it (Benchmark § Constraint Extraction + Quality Assurance). It measures **agent** compliance, and supplies **zero** data on reviewer accuracy or over-strict/unnecessary review demands. Its numbers are also not transferable to factory's reviewer without first-party measurement — which is what this experiment does |

Source hygiene: cite arXiv + the replication package (`DeepSoftwareAnalytics/SWE-Gate`,
HEAD `5f303b30dd53af3499d05558cfd82eda641be22b`, data release
`0265e9823de8c293ac70ab1d425d838c0a673439`). The second signal
(`dev.to/mech_app_ai/...`) is **not** grounded in the paper — wrong model roster, a
failure-mode table whose rows sum to 1003 against a 1212 denominator, and a fabricated
constraint taxonomy. Do not cite it. Author-stated limitations (synthetic re-instantiated
instances, LLM-assisted construction bias, Python-only, executable constraints only,
single-sample ablation) are recorded in `RESEARCH.md` §6.

Access gap, stated honestly: the paper's supplementary material (referenced 4×, holds the
constraint-rejection taxonomy) is absent from the arXiv v1 PDF and the GitHub package.
That is exactly the material that would have spoken to illegitimate review demands.

## 2. Cases

Four packets from two real merged Factory tickets, each a **committed** pre-fix revision
paired with its **committed** fixed revision — no synthetic mutations. Both `before`
heads pass the deterministic gate, which is the condition #34 exists to catch.

| Case | Issue | Review base (`origin/main`) | Head under review | Gate (real run) | Adjudicated verdict |
|---|---|---|---|---|---|
| `12-before` | #12 | `6ec35460…` | `4830b7d95fa60fab51f8c4260ce3cc662dae4804` | PASS | REVISE |
| `12-after` | #12 | `6ec35460…` | `7cd7a8307b1217cdb4835b99bb811d0331388459` | PASS | APPROVE |
| `20-before` | #20 | `07913b42…` | `8dd65882d58341e749e64481dae0acaa3c5ad031` | PASS | REVISE |
| `20-after` | #20 | `07913b42…` | `ce5568eef63003636e98dc6eca258d1a615b5e94` | PASS | APPROVE |

Adjudicated defects (independent read of the frozen source; the historical model verdicts
were used only to *discover* candidates, never as truth):

- **`12-before` D1 (primary).** The loader stores `cfg.manager = list(manager["command"])`
  with no normalization, while the same file's `manager_model()` accepts a command
  **string** and `shlex.split`s it. A string-form `[manager] command` therefore becomes a
  per-character argv: `cfg.manager[0]` is one letter, so `factory doctor` reports a
  one-character binary and `dashboard --json` joins characters. Two paths in one file
  disagree about the type of one key, and the argv is silently corrupted rather than
  rejected. Corroboration, not oracle: `7cd7a83` is titled "Preserve legacy manager
  command parsing during integration" and introduces `manager_settings()` to normalize it.
- **`12-before` D2 (secondary).** `manager.review` accepts any string although #12 defines
  a closed set (`"escalated"`, `"all"` allowed). The Exit gate does not name value
  validation, so missing it is not scored as a miss and reporting it is not scored as an
  unnecessary demand.
- **`20-before` D1 (primary).** The FIX round pushes with
  `dispatch.run(["git","push","origin",f"agent/{n}"])`, and `dispatch.run` defaults to
  `check=True`. The `conflict` profile shipped by the same commit tells the worker to
  resolve or restart the rebase — rewriting `agent/<n>` — so the push is a non-fast-forward,
  git exits nonzero and `apply()` raises `CalledProcessError` **after a passing gate**: the
  fix is never pushed, the PR never updated, no re-review, no escalation recorded. The
  profile the ticket requires to ship cannot complete through the FIX path added alongside
  it. Corroboration, not oracle: `ce5568e` changes exactly that line to
  `--force-with-lease` and adds a rebase case to the FIX test.

Each `cases/<id>/` holds `packet.md` (the frozen reviewer envelope), `oracle.json`
(expected verdict, findings with evidence, unnecessary-demand rule, provenance) and
`sources/` (`diff.patch`, `issue.md`, `README.md`, `gate-report.md`) with SHA256 per file,
blob ids per changed path, and the base/head commits — so changes on `main` cannot
invalidate later comparisons.

**Gate evidence is real, not asserted.** `factory gate` was executed at each head inside a
throwaway `git clone --shared` whose `refs/remotes/origin/main` was set to that case's
review base; only a `[repo].slug` line was prepended to `.factory.toml` for slug
resolution, with the committed gate checks unmodified. All four report
`conflict-markers: PASS`, `test: PASS`, `leak-scan: PASS`. The live checkout, its
`origin/main` ref and other agents' worktrees were never touched.

## 3. Method

The prompt is never reconstructed. `capture_contract.py` imports `factory.dispatch` and
`factory.config` from the pinned checkout under test, calls
`dispatch.configure(Config(root=<scratch>, repo="mikeroySoft/factory", main="main",
reviewer=[python, capture_prompt.py, "{prompt}", <out>]))` and then the production
`dispatch.review(<scratch>, <issue>, <gate report>)`. The capture process records
`sys.argv[1]` verbatim and prints `VERDICT: APPROVE` solely so `review()` can finish; that
verdict is never scored. Lifecycle and event writes land in a temp root.

The captured prompt plus the frozen packet envelope (byte-identical across arms) is then
answered by one bounded process per run:
`omp -p --mode text --no-session --no-tools --no-extensions --no-skills --no-rules
--no-lsp --no-pty --no-title --thinking low --hide-thinking --max-time 300 --model
anthropic/claude-fable-5-1` — the repository's own configured reviewer model
(`DEFAULT_REVIEWER`), already installed and authenticated. No new provider was configured
and no safety check was bypassed. Every run records `prompt_sha256`, `packet_sha256`,
`input_sha256`, exit code and wall time in `runs/main/results.json`.

Confirmed identical prompt text for `1947fc4e…` and the newer `07861b1dd0f04c0c5b8d5d6ba5f9bad1fadbe061`
("docs(review): describe the review stage as implemented"), so the comparison holds for
either #36 head. Distinct prompt hashes per arm: baseline `9112ef9d76`/`4e986562f8`,
issue36 `aee3991863`/`7f47ef8e87` (the pair differs only by issue number).

Reproduce: `python experiments/reviewer-calibration/run_calibration.py runs/<name> 2`.

## 4. Results — 16 runs, 2 contracts × 4 cases × 2 samples

All 16 runs exited 0 with a parsed verdict.

| Arm | Verdict match | Primary defect missed (defect-bearing runs) | REVISE on clean cases | Required/Optional split | Median `path:line` citations |
|---|---|---|---|---|---|
| `baseline` `cadb998…` | 4/8 | **4/4** | 0/4 | 0/8 | 18 |
| `issue36` `1947fc4…` | 4/8 | **4/4** | 0/4 | 8/8 | 20 |

Per-run detail in `runs/main/adjudication.json`.

1. **Both arms miss both real defects, every time.** Every defect-bearing run returned
   `APPROVE`. Neither D1 was ever reported, in any arm or sample. The `12-before` runs
   built a criterion-by-criterion table marking every acceptance criterion "met", and the
   `20-before` runs affirmatively praised the surrounding error handling. This is a
   first-party instance of exactly the failure mode #34 names: gate PASS plus reviewer
   APPROVE on a head with a defect that fails the ticket's own intent.
2. **Neither arm produced an unnecessary demand.** Zero REVISE on the clean heads; zero
   required-fix findings on them. Under-strictness — not over-strictness — is what these
   cases exposed, so the measurement gives #36's bloat guard no defect to fix here and no
   regression to fear.
3. **The #36 contract changed the shape of the review, not its detection.** It reliably
   produced the Required/Optional separation (8/8 vs 0/8) and per-finding rule citations,
   with a similar citation count. On this case set it neither found a missed defect nor
   suppressed a legitimate one.
4. **Non-blocking reporting works.** `12-before` D2 was raised as an explicit non-blocking
   observation in 4/4 runs across both arms — reviewers do surface sub-threshold concerns
   without escalating them.
5. One `issue36` `20-before` run mentioned "rebase/force-push" while discussing merge-stage
   concurrency and concluded "No evidence of a defect". Adjudicated as a **miss**, not a
   detection.

## 5. Limitations

These bound what the numbers above may be used for.

- **Four packets from two tickets, two samples each.** Sufficient to demonstrate the
  failure mode; far too small for a rate. Do not publish "0%/100%" as a factory metric.
- **No-tools packet ≠ production reviewer.** Production runs `omp -p --no-session --model …`
  **with tools**, in the worktree, able to read any file and the live issue. Here the diff,
  issue and README are inlined and the process has no tools, for determinism and identical
  input across arms. A tool-enabled reviewer could plausibly find D1 by reading
  `dispatch.run`'s signature (not in either diff). This experiment therefore measures the
  **contract**, not the deployed reviewer's ceiling. `20-before` D1 in particular requires
  a default argument outside the packet.
- **`docs/manager-plan.md` (111KB) is not in the packets** and is disclosed as omitted
  inside each envelope, so a "documented conventions" finding sourced there is impossible.
- **One model, one thinking level.** No cross-model or effort sweep.
- **Single adjudicator.** The oracles are one engineer's reading of frozen source, with
  each finding carrying its evidence so it can be re-adjudicated or contested.
- **Gate reports were produced now, not recovered from the historical runs.** They are
  real gate executions at the exact heads, not the artifacts those PRs carried.
- **Issue text is the current GitHub state** (body + triage comment), not a snapshot taken
  at review time.
- **`--max-time 300`** instead of the 120 discussed, applied identically to both arms, to
  keep a long review from being truncated into a corrupted measurement.

## 6. Why the coverage line stays deferred

A "review-criteria coverage" line in the gate report would have to mean something
defensible. This run shows why it cannot yet: the reviewer marked **every** acceptance
criterion of #12 as met, with citations, on a head carrying a real defect. Any coverage
number computed from the reviewer's own criterion enumeration would have read as full
coverage in precisely the case it was meant to flag. A defensible line needs a signal that
is independent of the reviewer's self-report; none of the evidence gathered here supplies
one, and SWE-Gate does not either (§1).

## 7. Comparison readiness

Ready and already exercised against #36. To re-run against a corrected contract head:
point `CONTRACTS`/`CHECKOUTS` in `run_calibration.py` at the new commit, then
`python run_calibration.py runs/<name> 2`. Packets, oracles, gate reports and the capture
boundary are frozen and hashed, so a later run is comparable to this one by construction.

The honest next step for a real rate is more cases, not more samples — and cases chosen to
provoke **over**-strictness (heads that are correct but unfashionable), since these four
produced none and that is the axis #36 is built to protect.
