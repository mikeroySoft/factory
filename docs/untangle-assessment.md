# Factory untangle assessment — assessment only

## 1. Commit and verdict

Analyzed latest fetched `origin/main`: **89227b68a61b7c82f0fd36fdb8f6755ab722f994** in `mikeroySoft/factory`. Fetched origin in `/workspace/factory-ops/factory-047`, then read a `git archive` snapshot of that exact ref under this report directory. The existing checkout was not switched or edited. No Tank access, credential reads, code changes, commits, pushes, PRs, or operational changes.

**Verdict: yes, a targeted untangle is worthwhile; a whole-codebase rewrite is not.** The highest leverage is making ticket generations, worker execution, and acceptance policy explicit. Today dispatch is both application coordinator and shared infrastructure; manager, plan/evidence readers, and result retention reach back into it. This makes retry/requeue behavior depend on old journal rows, branch existence, prose, and pass order. Keep the existing safety checks, durable journal, immutable-head checks, and bounded readers. Refactor a few ownership boundaries rather than splitting every large file.

### Assessment method and limits

The `untangle-codebase` skill was followed for assessment-only steps 2–3. Comments/Tests additions were skipped per ask. Format below is **Decision → problems solved → invariants → tradeoff**, with fixes above their problems. It is a static structural assessment, not a runtime reproduction or proof of every possible defect; speculative/dead-code claims are deliberately excluded when unsupported.

Skeleton: the console script is `factory.cli:main` (`pyproject.toml:31–32`); `factory/__main__.py` delegates there. Main execution modules are dispatch (5,183 lines), manage (1,134), gate (804), config (930), triage (806), and plan (445). Supporting domain/state modules include binding, schedule, handoff, lifecycle, runtime_events, results, trace, decisions, risk, and feedback. Dashboard/workspace, evidence/briefing/roadmap, learn/eval/score, and onboard/watch/release comprise the other major feature areas. Static assets and templates live beside the package's Python files. This is a flat package with several recognizable features, not an absence of modularization.

An AST graph including top-level and function-local `from factory ...` imports finds **one 27-module strongly connected component**: actions, binding, brief, briefing, dashboard, decisions, dispatch, eval, evidence, handoff, learn, manage, onboard, outcomes, plan, release, results, roadmap, schedule, score, stats, trace, triage, watch, workspace. This is a potential dependency cycle, not proof of an import-time crash: lazy imports deliberately defer many edges. Config and lifecycle are outside that component.

Git evidence: among the latest 200 commits reachable from analyzed main, dispatch changed in 66, config in 28, manage in 20, triage in 20, and gate in 18. Dispatch co-changed with config in 19 commits, manage in 16, triage and stats in 12 each, and gate and learn in 11 each. Counts are per commit, including merges, not independent incidents or defect rates. This supports prioritizing these boundaries; it does not establish causation. Concrete history corroborates the seams: `ef612ee` supplies issue text to isolated reviewers; `904872f` carries deferred findings across passes; `1b7e6e8` corrects remote-head lease behavior; `79e246b` corrects add-only override propagation; `bc48496` isolates gate execution from worktree engine code; `1cd0717` corrects fairness across restart.

## 2. Problem list

### Hidden links

**H1 — Configuring dispatch is a prerequisite for unrelated services.** `dispatch.py:55–91` installs repository/config/path globals and resets pending-review caches. `manage.py:17,193,219,857,1024` imports and consumes dispatch's state and helpers. `triage.py:502–505,583–586` configures dispatch merely to journal decisions. `gate.py:25–39` maintains another configured singleton. Dependencies are temporal and process-wide, not visible in function signatures. Reconfiguration is especially awkward alongside concurrent ticket processing; this is an architectural risk, not an observed race.

**H2 — A nominally foundational reader imports presentation policy.** `evidence.py:28` imports dashboard and briefing; it uses `dashboard.pr_record`, `selected_issue`, and `build_ticket` at `1468,1514,1522`. `binding.py:11` imports evidence's transport/error definitions, and `plan.py:17` imports evidence too. Thus admission/routing reaches presentation and back toward execution. `dispatch.py:4642` and `triage.py:711` explicitly document lazy imports to avoid the plan → evidence → dashboard path.

**H3 — Requeue has no single explicit generation identity.** `dispatch.py:2096–2099` derives escalation round from historical escalation count. `manage.py:1047–1056` derives manager eligibility from past escalation/manage rows. `manage.py:800–824` identifies delivered feedback independently, while `903–910` applies re-release freshness filtering to CI but not ordinary review feedback. An owner requeue therefore does not itself establish a universal boundary for old feedback and spent rounds. These are separate interpretations of one lifecycle operation.

**H4 — “Fresh start” is determined by filesystem/ref residue.** `dispatch.py:1858–1874` reuses an existing worktree, then local agent branch, then remote agent branch, falling back to main only if none exists. A label requeue cannot express resume versus restart. Operators must know and manipulate all three artifacts to obtain a genuinely new base. Existing branch recovery is valuable; the missing piece is explicit intent, not deleting recovery behavior.

**H5 — Worker completion is a multi-file, timing-sensitive protocol.** `dispatch.py:4361–4364,4392–4408` deletes the current plan directory, writes a prompt, snapshots handoff mtime, and recognizes completion using a changed handoff plus clean Git state and selected exit/timeout conditions. Plan, handoff, commit, deadline, and actual gated head must all line up. Those invariants are valid, but their contract is spread across orchestration and artifacts rather than an attempt result.

**H6 — Implicit pass-order ownership.** `dispatch.py:5164–5180` sequences release, land, review intake, manage, tickets, learn, then release again. The comment explains a CHANGELOG merge-order constraint. `manage.py:914–915` leaves approved/red CI handling to merge, and `handoff.py:39–69` independently mirrors manager terminal eligibility. Correctness depends on knowing which earlier stage owns a condition.

**H7 — Audit source identities omit effective configuration.** `dispatch.py:1678–1682` cites `.factory.toml` for worker selection, although host layers also control workers (`config.py:665–678`). `plan.py:295–302` fingerprints committed config only. That route fingerprint is not inherently wrong, but cannot serve as proof of the effective worker profile or its host override. An operational receipt must currently reconstruct that distinction externally.

### Needless links

**N1 — Manager is another executor, not just a decision policy.** `manage.py:378–507` reaches into worktree Git state, leases, worker rounds, push, gate/review, and approval. Normal ticket processing performs the corresponding pipeline in `dispatch.py:4583–4953`. Fixes to execution semantics must consider two orchestration paths. Shared low-level helpers do not eliminate duplicated lifecycle ownership.

**N2 — Acceptance consumers depend on dispatcher internals.** `results.py:311–331` calls `dispatch._head_evidence_matches`; `manage.py:940–941` does likewise. `manage.py:219` also calls `_latest_decision_id`. Result retention and manager policy should depend on shared acceptance/event queries, not a CLI execution module's private functions. Preserve one authoritative predicate; do not copy it into each caller.

**N3 — Model reporting and model execution are separate derivations.** `config.py:296–307` expands worker argv and separately infers a model; `dispatch.py:4373–4378` inserts a model only when the raw argv exactly equals `DEFAULT_WORKER`. The resolved argv is therefore not solely a product of config resolution. Wrapping, explicit argv, and default equality interact through knowledge held by the caller.

**N4 — Gate policy has a wide translation boundary.** `dispatch.py:1983–2030` exposes many booleans/strings then serializes them to CLI flags. `dispatch.py:2995–3032` derives that policy from labels, issue text, handoff text, comments, and sync state. This is an important installed-engine isolation boundary, not a useless subprocess; the needless coupling is independently carrying and translating loosely related policy fields at every call site.

### Surplus

**S1 — Repeated historical projection work and eligibility logic.** `lifecycle.py:201–208` reads all committed segments and live rows. `manage.py:891,1047–1056`, `dispatch.py:2059–2061`, and `handoff.py:176` repeatedly scan/filter history to reconstruct related state. This creates duplicated interpretation and potentially growing per-pass work. No latency measurement was made; do not claim a proven bottleneck. Snapshot/query ownership is the first fix, not a new database.

**S2 — Dispatch accumulates adjacent products.** Worker execution, review tooling/retries (`2400–2557`), upstream sync (`3121` onward), merge/approval (`3207–4324`), lesson promotion (`4000–4005`), and release scheduling (`5154–5180`) share one module and global context. The surplus is coordinator knowledge and dependency surface, not evidence that those features should be removed.

**S3 — Best-effort failures have weaker contracts than core ticket failures.** Lesson promotion and release catch broad exceptions and log them (`dispatch.py:4002–4005,5157–5162`) while allowing the pass to succeed. Isolation is sensible; these are not silent exceptions. However their aggregate success/failure contract differs from the structured ticket/lifecycle outcomes, complicating callers' interpretation of “pass success.”

No definite dead functions, speculative settings, or removable pass-through wrappers were established. Small wrappers such as `Config.worker()` (`config.py:292–294`) provide a coherent convenience API and are not worth deleting just for being short. No structurally significant disabled test was established.

### Special cases

**P1 — Host/repo precedence is not one uniform merge.** `config.py:442–458,665–678` filters host keys then recursively overlays repo config. Host engine yield policy wins separately (`716–724`); wrapper and second reviewer keys are forbidden in repo config (`768–790`); other review keys merge normally; leak-scan extras have additive semantics documented at `80–84`. These distinctions can be justified safety policy, but must be understood across several locations.

**P2 — Worker label routing has two meanings.** `config.py:282–290` uses first matching worker iteration order; `729–740` allows both prose “when” rules and deterministic label selectors. `dispatch.py:1659–1670` treats a manager-selected worker differently when the ticket's profile is label-gated. Manager's candidate set further excludes reserved and selector-gated profiles (`manage.py:1067–1070`). Priority, eligibility, and explanation are entangled with dict order and label vocabulary.

**P3 — Failure type is inferred too late.** `dispatch.py:1810–1855` returns process exit and timeout; `worker_round` returns `(ok, report, logfile, head)` (`4344–4355,4476`). It special-cases idle/budget outcomes (`4428–4445`) but otherwise proceeds to the gate after a nonzero worker exit. Callers can classify a failed round as `gate_failed` (`manage.py:465–470`; `dispatch.py:4887–4893`). An auth failure with no useful work is not represented as a distinct typed infrastructure outcome at this boundary. This explains a structural exposure; no current host login was probed.

**P4 — Human-readable report text doubles as machine protocol.** `dispatch.py:4887–4893` uses `report.startswith("worker stuck")`; `4875–4876` tests Markdown failure strings. Combined with parsing issue/handoff/comment directives in `gate_flags`, prose and formatting become behavior-changing inputs. Keep human-readable reports; stop requiring consumers to reverse-engineer outcome classes from them.

**P5 — Reviewer retry policy is embedded in review orchestration.** `dispatch.py:2524–2531` uses a three-attempt cap, provider-output-derived delay, sleep, and remaining-deadline check. Bounded retries are necessary, not automatically a smell. The structural issue is that availability/retry classification lives here while worker failure and triage health have different mechanisms (`triage.py:124` onward). Do not unify their domain verdicts; share only transport/health outcome vocabulary where it actually fits.

## 3. Redesign — decisions above the problems they solve

### Decision A: Introduce an explicit ticket generation and attempt state model

**Problems solved:** H3, H4, H5, H6, S1, P3, P4.

Model a requeue as an event with generation identity, owner decision, resume/fresh intent, base SHA, and a feedback watermark. Attempts and manager rounds belong to that generation. A generation cannot be re-escalated on pre-requeue feedback before its first fresh attempt unless an explicit policy says so. A worktree is a resource belonging to an attempt/generation, not the definition of whether work is fresh. Project state once from a journal snapshot and use that projection for manager eligibility, terminal handoff, retries, and packet construction.

**Invariants:** durable receipts, human takeover, no duplicate publication, lock/claim arbitration, and existing history remain authoritative. Preserve compatibility with old journal rows through an explicit legacy projection.

**Tradeoff:** this is the most consequential behavioral migration. Deliver it incrementally; avoid a one-shot event-format rewrite or a new database.

### Decision B: Resolve a complete worker specification before scheduling or launch

**Problems solved:** N3, P1, P2, P3, P5, H7.

Produce one resolved value containing profile identity, command/model, wrapper, environment policy, selector priority/eligibility, provenance, and health result. Selection, audit, dry-run, and actual launch consume that same value. Normalize defaults once; do not use argv equality in dispatch to infer model semantics. Resolve host/repo ownership from one explicit key-policy table, retaining the existing security restrictions. Classify launch/auth/unavailability separately from completed work with failing checks.

**Invariants:** secret-free provenance, no credential exposure, no weaker isolation, same configured gates, and explicit manager choice within eligible profiles.

**Tradeoff:** avoid inventing a universal provider plugin framework. Start with the existing command profiles and an outcome contract; use the existing worker-health effort as the natural seam.

### Decision C: Make execution one service; manager returns decisions to it

**Problems solved:** H1, H6, N1, S2, S3.

Pass a repository/pass context explicitly: resolved config, paths, journal/query interface, process runner, GitHub/Git adapters, and deadline. Extract only the actual shared attempt → gate → review → publish pipeline. Normal dispatch and manager recovery invoke it with different policies; manager no longer reconstructs push/lease/review sequencing. Dispatch becomes a short composition root. Release/learn remain separate stages with structured best-effort outcomes.

**Invariants:** immutable-head evidence, gate subprocess isolation, leases, locks, host claims, interruption cleanup, and worker GitHub isolation. Do not remove guards because the coordinator becomes smaller.

**Tradeoff:** move one lifecycle slice at a time. A class containing all current globals would merely rename the tangle.

### Decision D: Put acceptance and routing facts below execution and presentation

**Problems solved:** H2, N2, H7; much of the import component.

Extract shared read-only primitives: GitHub read transport/errors, bounded source reading, ticket/PR classification, acceptance queries, and configuration provenance. Binding and plan depend on those, not dashboard. Dashboard/evidence/briefing compose domain facts for their respective outputs. Results and manager depend on a public acceptance predicate, not `dispatch._...`.

**Invariants:** preserve bounded reads, explicit incomplete coverage, ownership checks, exact-head validation, no side effects in read paths, and one acceptance policy.

**Tradeoff:** avoid a generic “utils” bucket. Separate transport, domain policy, and presentation by their reasons to change. The practical success criterion is that importing admission/routing no longer imports dashboard/dispatch, not achieving an arbitrary module count.

### Decision E: Separate policy/results from Markdown and CLI serialization

**Problems solved:** N4, P4, H5, parts of S1 and P3.

Use explicit GateRequest, AttemptOutcome, ReviewOutcome, and EscalationReason values internally. A gate CLI adapter serializes the request; Markdown renders results for people. Preserve current compatibility parsers at external boundaries. A single outcome carries code, infra/project classification, head, artifact references, and completion evidence; consumers do not inspect prose to decide whether an attempt counts.

**Invariants:** unchanged gates and override authorization, installed-engine execution boundary, all refusal/no-mutation checks, and full reviewer context.

**Tradeoff:** choose a handful of domain values, not a schema framework for every dictionary. Retain old reports as evidence during migration.

## 4. Recommendations for Michael

1. **Approve a bounded reliability untangle, not a rewrite.** First address requeue generation and typed worker outcomes; they directly target repeated operator intervention and wasted retries. Keep the work behind current safety gates.
2. **Use existing requeue/worker-health efforts as the first two slices.** Require explicit fresh-vs-resume intent, a pre-requeue feedback watermark, and infra/auth classification before failed attempts spend product-work retry budget. Do not prescribe unrelated UI or repository restructuring.
3. **Next remove the two dependency inversions:** shared acceptance out of dispatch; read transport/ticket classification out of dashboard-dependent evidence. These are small, measurable architecture wins with multiple callers.
4. **Then consolidate manager and normal execution.** Move a working lifecycle slice into an explicitly contextual service; preserve the old paths until their callers migrate. Do not start by mechanically splitting dispatch into ten files.
5. **Require effective-worker proof for configuration changes.** The displayed resolved argv/model/provenance must be the exact value launch uses. Preserve intentional host-only policy and report its source without secrets.
6. **Judge completion by behavior and dependency direction:** an owner requeue gets a genuine fresh attempt; old feedback cannot immediately bounce it; auth/no-commit is infra; resume and restart are distinguishable; acceptance readers do not import dispatch; plan/binding do not import dashboard. Module size and graph size are supporting indicators, not goals.

This report makes no merge, deployment, operational, budget, or implementation decision. It recommends the smallest structural work likely to remove recurring failure classes.

## 5. Scope note

Comments and Tests additions were skipped as requested. Existing comments were read only where they revealed structural contracts; tests were not added, altered, or executed. No implementation or fixes were applied.

## Addendum

## Bot roles (three-way split)

Standing fleet design (Michael, voice 2026-11-05):

- **Factory PM** — direction: goals, engineering calls among bots; bring Michael only vision/taste/design/money.
- **Factory Ops** — gate triage: human-gate recommendations to Factory PM; assessments like this untangle report.
- **Factory developer (proposed, not created)** — only writes and verifies code in `mikeroySoft/factory` (ub-mode, prove it works, hand PRs to Shipping).

## Open decisions (as of 2026-11-05 ~10:55 AM PT)

Nothing started from this assessment until Michael picks:

1. Hold — keep the reference and stop here.
2. Start Decision 1 — tell Factory PM / Ops to begin #430 / #662 slices (bounded reliability untangle).
3. Design the Factory developer bot (dr eggbot).

Library Page (interactive copy): https://cursor.com/canvas/store-c381e32a-a1a8-4cc7-9026-b5a17593e7d4/620eb7e1-fc07-4b43-ac15-e70b141ddb2f
