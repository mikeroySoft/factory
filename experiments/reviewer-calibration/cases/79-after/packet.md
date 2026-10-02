## Frozen review packet — case 79-after

This packet supplies, verbatim, the evidence the reviewer would otherwise read from the
repository and GitHub. The reviewer process for this experiment has no tools, so the diff,
the issue text and the repository README are inlined below. Cite `path:line` from the diff.

- repository: mikeroySoft/factory
- issue: #79
- review base (origin/main): 8fba9c5e33a7b0122683334e424183af5ff1b5fd
- head under review: e20ebcd5b9bd4d4a80cdd3a643a56b7e02518d1e
- documented conventions present at this head: README.md (inlined below). No AGENTS.md or
  CONTRIBUTING.md exists at this commit. docs/manager-plan.md (if present) is NOT inlined and is
  outside this packet; do not assume its contents.

### Issue

# Issue #79: [B2] Expose source-versioned PR feedback evidence in the existing dashboard and briefing

Prepared against canonical GitHub `main` revision `8fba9c5e33a7b0122683334e424183af5ff1b5fd`. Re-read `main`, open PR changed-file ownership, issue ownership, and Factory locks immediately before starting work; rebase onto the then-current canonical `main` rather than copying whole files from an older PR. `B2` is a planning handle, not a GitHub issue number, and this ticket has no `Blocked by:` dependency.

## Scope

Add one bounded, read-only PR-feedback collector/normalizer for Factory-owned PR evidence. The same collector output must be consumed by the existing full `factory dashboard --json` snapshot at `tickets[].pr.feedback`, the existing Review drawer/Inbox evidence, and `factory.briefing.sources_for`; it is also the only producer contract a later, separately authorized #15/B3 consumer may use. Keep the existing Inbox and Factory Manager briefing advisory and read-only. This ticket observes and presents evidence only: it never dispatches a worker, delivers feedback, decides readiness, approves, merges, closes, relabels, resolves a review thread, submits a review, or invokes a model.

Use a narrow `factory/feedback.py` module so later #15 can call the collector/normalizer without importing dashboard internals and so dashboard, briefing, and future consumers cannot grow competing GitHub interpretations. Reuse the existing full-snapshot GitHub transport and accepted bounded lifecycle/event readers for provenance. Do not add an event bus, provider framework, database, persistent session, worker-log parser, or second GitHub client per consumer. The collector must not run or add network calls on the `factory dashboard --runtime-json` path; preserve that existing output contract.

Preserve the existing `pr.checks`, `pr.comments`, `pr.verdicts`, `review_decision`, gate text, and current snapshot fields for their existing consumers. Feedback consumers move to the richer object; they do not redefine those legacy fields as source-versioned evidence. When a PR is present but a feedback source cannot be read, return the schema-1 feedback object with explicit unavailable/partial coverage and retained independent facts. An older engine with no `feedback` key is unsupported/unknown, never equivalent to a complete empty observation.

### Normative schema 1

The shared collector returns exactly one feedback object with these required keys and meanings:

- `schema_version`: integer `1`. Unknown versions are unsupported and cannot authorize later dispatch.
- `producer`: `{name: "factory.pr-feedback", revision: string|null}`. `revision` is a verifiable source/build revision when available, otherwise null; a CLI version alone is not revision proof.
- `observed_at`: UTC timestamp for this collection, not provider change time.
- `observation_id`: `sha256:` plus the deterministic digest defined below.
- `repository`: `{id: string|null, slug: string, host: string}`. Use the GitHub native repository ID; do not derive identity from a filesystem path.
- `pr`: `{id: string|null, number: int, url: string, head_sha: string|null, state: "open"|"closed"|"merged"|"unknown", draft: bool|null}`. `head_sha` is the head actually observed for this collection.
- `owner`: `{issue: {id: string|null, number: int, url: string}|null, relation: "factory_issue"|"unverified"|"ambiguous"|"none", evidence: [string]}`. For `agent/<n>`, verify an actual same-repository issue/link plus retained Factory claim/PR provenance. Branch text, assignee login, PR author, or the shared GitHub account alone never proves ownership. Conflicts and missing links remain explicit and non-actionable.
- `coverage`: an object with exactly `pr`, `reviews`, `threads`, and `checks`. Each value is `{status: "complete"|"partial"|"unavailable", observed_at: string|null, truncated: bool, reason: string|null}`. A complete empty source means observed empty; partial or unavailable never means none.
- `items`: array sorted by `evidence_id` and then `source_revision`. CI/status, reviews, Factory reviews, and inline review threads/comments coexist; there is no singular PR-level feedback kind.
- `errors`: bounded array of `{source: "pr"|"reviews"|"threads"|"checks", code: string, message: string}`. Sanitize messages so credentials and raw configuration cannot appear. One failed source must not discard usable independent sources.

Every `items[]` record has all of these fields, with explicit nulls where specified:

- `evidence_id: string`: stable provider identity namespaced by provider, host, repository native ID, PR native ID, `kind`, and provider source ID. Never derive it from a timestamp, array index, display ordering, or wording. If a required provider source ID is absent or contradictory, record a bounded gap/error instead of inventing an item identity.
- `kind`: one of `review`, `review_comment`, `check_run`, `commit_status`, or `factory_review`. Ordinary PR discussion is separate quoted context, not an automatically actionable inline review. A comment containing regex `VERDICT` without accepted review-execution and target-SHA provenance is legacy/unknown, not trusted independent approval.
- `source_id: string`, `source_url: string|null`, `source_revision: string`, `source_updated_at: string|null`, `review_id: string|null`, `thread_id: string|null`, `check_run_id: string|null`, and `run_attempt: int|null`. Use real provider IDs/links and actual provider UTC update time when supported. A Factory review uses its accepted recorded review execution/event identity and known target SHA, not its rendered prose.
- `observed_head_sha: string|null` and `source_head_sha: string|null`. The first is the observed PR head for this collection; the second is the commit actually reviewed, checked, or commented on. Preserve the provider's original/current comment commit distinction in `location.original_commit_sha`. Never fill a missing source SHA with the current PR head.
- `relevance`: `current_head`, `historical`, or `unknown`. Equal non-null observed/source SHAs establish current-head relevance for reviews/checks; unequal SHAs establish historical relevance. Preserve provider outdated/current-position evidence for comments: `thread_outdated=true` cannot become current actionability merely because SHAs compare equal. Missing or contradictory source SHA, or a head race during collection, yields `unknown` with the gap recorded in coverage/errors.
- `disposition`: `{review_state: string|null, thread_resolved: bool|null, thread_outdated: bool|null, check_status: string|null, check_conclusion: string|null}` using documented provider values. Disposition is independent of relevance and delivery. Dismissed reviews, resolved/outdated threads, and passing, failed, rerun, neutral, skipped, cancelled, pending, or unavailable checks remain distinguishable. A failed check is not a failed worker; a dismissed review is not delivered feedback.
- `author`: `{login: string|null, kind: "human"|"bot"|"factory_reviewer"|"unknown"}`. Attribution must be evidence-backed. Provider User versus Bot alone does not distinguish a human from a Factory process using shared credentials; `factory_reviewer` requires recorded Factory review provenance.
- `body: string` and `truncated: bool`. Empty body is valid when the source has none. `location` is `{path: string|null, line: int|null, side: string|null, original_line: int|null, original_commit_sha: string|null}`. `summary: string|null` is only the provider review/check title or name, never a generated verdict or identity. Keep source detail links when body text is truncated.

The producer must not emit or infer `delivered`, `fixed`, `approved_for_merge`, an aggregate `ready` bit, worker completion, or any equivalent authority. Flattening consumers must retain the containing repository, PR, and owner identities.

### Canonicalization, revision, and observation identity

Canonical JSON is UTF-8 with sorted object keys, compact separators, normalized identity strings, explicit nulls, and source-ID-sorted arrays. `source_revision` is `sha256:` over exactly these retained normalized fields: `kind`, `source_id`, `review_id`, `thread_id`, `check_run_id`, `run_attempt`, `source_head_sha`, `source_updated_at`, `author`, `body`, `truncated`, `location`, `disposition`, and `summary`.

Exclude collection/current-head timestamps, `source_url` and other display coordinates, presentation labels/order, relevance, and unrelated provider metadata from `source_revision`. Therefore editing retained body/location/resolution/dismissal or changing a check outcome changes the revision under the same source ID, while observing an unchanged old source against a new PR head changes relevance without pretending the source changed. A rerun with a new check-run ID or run attempt is new evidence identity/revision. Provider result reordering and repeated equivalent polls are not new feedback. When body content is truncated and the provider supplies no reliable update/version signal, coverage must state that byte-exact change detection beyond the retained body is unavailable; later consumers cannot autonomously act on that incomplete source.

`observation_id` is `sha256:` over canonical repository/PR native IDs, the observed head, sorted `(evidence_id, source_revision)` pairs, and each source coverage `status`, `truncated`, and `reason`. Exclude `observed_at`, every provider/collection/generation timestamp used only for display, including `coverage.*.observed_at`, and harmless source order. Equivalent reads therefore have the same observation identity.

Query real reviews and real review threads/comments, with native IDs, links, commit identity, and resolved/outdated state. Query native check runs and commit statuses with IDs, target head, status, conclusion, run attempt, and link, not name-only aggregates. Preserve Factory-review versus external-review provenance and do not double-count one provider review as two independent reviewers.

### Bounded observation, races, and partial reads

Use fixed shared constants, not operator-supplied runtime policy: one initial and one final PR-head read around the detail group; at most 100 retained items in each of reviews, threads/comments, and checks/statuses; at most two provider pages per source when pagination is required; at most 20,000 UTF-8 bytes of retained body per item; at most 32 sanitized errors; and a 30-second timeout for the feedback detail group. Stop cleanly at each bound. Mark the affected coverage `partial`, `truncated=true` when applicable, and give a machine-stable reason/code plus a human-readable bounded reason. Record actual page/count/body/time limitations; absence beyond a cap is never resolution, dismissal, disappearance, or complete evidence.

If the head changes between the initial and final reads, preserve all returned facts and their real `source_head_sha`, use the final observed head when available for `pr.head_sha`/`observed_head_sha`, mark affected coverage partial with both observed head values in the bounded reason, and set applicability to `unknown` rather than silently retargeting details. If the final head is unavailable, keep the source facts, expose null/unknown head and unavailable/partial coverage, and do not promote relevance.

A failed or partial reviews read cannot erase a successful checks read, and the converse also holds. In this B2 read-only producer, return every independently observed current fact and explicit coverage; do not create a durable cache/store. The later #15 consumer must preserve previously persisted evidence with its original timestamps across partial/unavailable refreshes and must not delete unseen IDs, resolve threads, dismiss reviews, reset retry/dedup state, or advance failed-source acknowledgement. Only a complete authoritative source refresh may establish disappearance/supersession; absence from a capped result cannot.

### Existing read-surface integration

- `factory dashboard --json`: project the identical feedback object at `tickets[].pr.feedback` in the full snapshot only. Preserve full-snapshot cadence and existing ticket/PR fields.
- Existing Review drawer in `factory/dashboard.html`: show all simultaneous evidence with source provenance link, source commit versus current observed head, relevance, disposition, body/location, and coverage/errors. Render source body only through the existing safe DOM/text helpers; never interpolate it as HTML or treat it as instructions.
- Existing Inbox/Factory Manager briefing: extend `factory.briefing.sources_for` with cited, bounded feedback sources and a coverage notice. Earlier human decisions/constraints keep priority in the existing source/byte budget; append feedback and explicitly report any omission instead of silently evicting earlier constraints. Keep the existing no-tools/read-only system authority. Tests and smoke proof must not call a production model.
- Missing schema support, unknown versions, partial coverage, ambiguous ownership, legacy verdict comments, and head races must render as unknown/non-actionable evidence rather than an empty success or readiness statement.

## Touches

- `factory/feedback.py`: one narrow shared bounded collector/normalizer, canonicalization, identity, coverage, and error handling. This is the only new source file expected.
- `factory/dashboard.py`: existing `GRAPHQL`/`github`, `pr_checks`, `pr_record`, `build_ticket`, and `snapshot` full-snapshot path; pass native PR/repository/issue/provenance and accepted event evidence into the shared collector. Do not add feedback collection to the runtime-JSON branch.
- `factory/briefing.py`: `sources_for` only, preserving existing source-priority/cap/error conventions and read-only/no-tools behavior.
- `factory/dashboard.html`: existing `tabReview`, task facts, Inbox raw evidence, and coverage presentation only; no new dashboard or action control.
- `tests/test_factory.py` and `tests/test_briefing.py`; use a dedicated `tests/test_feedback.py` only if it is smaller and clearer than adding the canonicalization cases to the existing files. Update existing contract-dependent checks rather than source-text/field-copy assertions.
- `README.md` and `CHANGELOG.md`: document schema location, fixed bounds, unknown/partial behavior, and read-only authority. Do not depend on unpublished local planning documents. Include a real sanitized schema-1 example in the PR/handoff evidence rather than creating another planning framework.

At the prepublication ownership refresh, PR #76 (`agent/4`, targeting `stable`) changes only bind-error handling in `factory/dashboard.py:main` relative to canonical `main`, not the snapshot/feedback functions. Its large diff against `stable` mostly reflects base divergence. Leave that PR untouched and preserve its independent ownership. No open PR changes `factory/briefing.py` or `factory/dashboard.html` relative to canonical `main`. PR #77 (`agent/5`, also targeting `stable`) owns external-review intake changes outside B2; leave it untouched. Open issue #7 may touch `factory/dashboard.py` only if needed and open issue #9 names `factory/dashboard.py`/`factory/dashboard.html`; both are unassigned, have no PR, and remain on the separately ordered #5→#6→#7→#8→#9 external-review chain. They are scheduling overlaps, not behavioral dependencies. Open PR #74 changes `README.md` but no B2 implementation/read-surface file; integrate B2 documentation last and rebase rather than adding a fake blocker. Before editing, re-read all open PR changed-file lists and the relevant Factory locks. If another owner has begun writing the same functions or presentation sections in `factory/dashboard.py`, `factory/briefing.py`, or `factory/dashboard.html`, do not edit concurrently: leave the ticket in normal intake/handoff and report the exact owner/PR/file overlap for serialization. Do not add a permanent `Blocked by:` line for file scheduling.

## Exit gate

1. Add focused behavior checks that provide simultaneous failing CI, a current-head changes-requested review, an unresolved inline thread/comment, and a provenance-backed Factory review. Assert all independent items survive together with native IDs/links, source/observed heads, location, disposition, ownership evidence, explicit nulls, and complete coverage. Assert an ordinary PR comment or provenance-free `VERDICT` does not become trusted Factory review evidence.
2. Repeat an equivalent provider read with reordered results and a changed collection timestamp: every `evidence_id`, `source_revision`, and `observation_id` stays identical. Edit a body/location/resolution/dismissal under its existing provider ID and rerun/change a check attempt/outcome: only the applicable semantic revision/identity and enclosing observation identity change.
3. Exercise same-head, advanced-head, head-change-during-collection, missing source SHA, dismissed review, resolved/outdated thread, cancelled/pending/passing/failing check, empty-complete source, pagination/count cap, body truncation, timeout, malformed response, and one-source failure. Historical/unknown evidence never becomes current; independent successful facts remain; coverage/errors distinguish empty, partial, unavailable, and complete.
4. Add focused regression boundaries: existing checks/comments/verdict/gate fields and current full-snapshot consumers still work; a PR on an older engine with no feedback key renders unsupported/unknown; `factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged; non-PR tickets gain no fabricated feedback; `sources_for` retains earlier human constraints ahead of appended feedback and exposes coverage/omission; source text such as `<img onerror=...>` renders literally and executes nothing; no mutation command, event append, model call, or new durable state occurs.
5. In a disposable temporary repository with real Factory configuration and controlled read-only `gh`/provider stand-ins, run the actual full CLI `python -m factory dashboard --json` and inspect `tickets[].pr.feedback` for simultaneous CI/review/thread evidence plus historical and partial evidence. Then launch the actual server with `python -m factory dashboard --host 127.0.0.1 --port <free-port> --no-open`, open it in a real browser, select the existing ticket Review drawer and Inbox evidence, and visibly confirm links, source/current heads, relevance, disposition, escaped body/location, and coverage/errors. Exercise `briefing.sources_for` against the same snapshot and inspect its cited bundle without invoking `briefing.respond`, OMP, a provider model, `/api/act`, or any GitHub mutation. Record the exact disposable commands, browser observations, and absence of mutation calls in the handoff; remove the temporary repository/process afterward.
6. Run the repository gate exactly once after implementation and smoke evidence settle: `python -m unittest discover -s tests`. No formatter, linter, alternate `pytest` command, or changed gate policy substitutes for this command.
7. At accepted handoff, give #15's integration owner the real B2 issue number, accepted merge revision, implemented schema/types/nullability/enums, exact constants and canonicalization, collector entry point/failure behavior, and a sanitized real full-snapshot example containing simultaneous CI/review/thread evidence plus historical and partial evidence. #15 remains unchanged and held from re-entry until a separately authorized owner accepts that handoff and amends it with the real same-repository issue number; an open issue, proposed schema, or unmerged helper is not an accepted producer.

## Out of scope

No changes to `factory/dispatch.py`, `factory/manage.py`, `factory/triage.py`, `factory/lifecycle.py`, `.factory/events.jsonl` schema, `/api/act`, manager decision menus, worker prompts, delivery/dedup/acknowledgement state, readiness/admission policy, review submission/resolution, branch edits, labels, issue/PR state, or merge behavior. #15/B3 owns later bounded delivery and remains `ready-for-human`; do not amend, requeue, relabel, close, duplicate, or build it here. #5–#9 own the external/human PR review-only lane; do not replace or implement that lane and never grant it fix, push, merge, or close authority. B1 readiness explanations, B4 history, B5 causal receipts, F01 lifecycle, F02 waits, F03 network-free runtime projection, collaboration #55–#59, District implementation, notifications, controller/daemon/database work, releases, installation, rollout, and production evidence collection remain unchanged/held.

Preserve configured PR-target safeguards accepted in #78 and all exact-head S0 protections already on canonical `main`: failed reviewer execution cannot approve; gate and independent review evidence stay bound to the assessed immutable head; refresh requires fresh gate and review; stale/missing/ambiguous/partial/legacy evidence is non-authorizing; the provider expected-head precondition prevents a last-moment head race; CI remains fail-closed; current-main containment, human requested-changes veto, ticket/merge locks, one-merge-per-pass remain intact. This read-only ticket cannot weaken or grant any of those authorities. Merge of B2 source will not mean release, installation, rollout, public publication, #15 execution, or delivered outcome.

## Comment by @mikeroySoft (2026-09-10T22:17:34Z)

Triage: This issue is a fully-specified, bounded engineering task: it has a clear problem statement (add one read-only PR-feedback collector/normalizer), a normative schema-1 with exact fields/enums/nullability, precise canonicalization/identity rules, fixed bounds, integration points, and an explicit exit gate with observable done-conditions and exact verification commands. It does not require design judgment, does not touch release/signing/security policy, and explicitly cannot weaken or grant any authority (it is read-only), so it is not ready-for-human. No information is missing that would block implementation, so it is not needs-info, and it is clearly actionable, so not wontfix.

Agent brief: Create factory/feedback.py as the single bounded, read-only PR-feedback collector/normalizer returning schema-1 objects with keys schema_version, producer, observed_at, observation_id, repository, pr, owner, coverage (pr/reviews/threads/checks), items, and errors. Implement sha256-based source_revision over the retained normalized fields and observation_id over canonical native IDs + sorted (evidence_id, source_revision) pairs + coverage status/truncated/reason; use native GitHub IDs/links, real provider update times, head/commit provenance, relevance (current_head/historical/unknown), disposition, and evidence-backed author attribution. Enforce fixed bounds (≤100 items per source, ≤2 pages, ≤20KB body/item, ≤32 sanitized errors, 30s timeout), initial+final PR-head reads with head-race/partial-read handling, and never emit delivered/approved/ready/authority bits. Integrate the identical object into factory/dashboard.py full-snapshot path only at tickets[].pr.feedback (never the runtime-JSON path), factory/briefing.py sources_for (append after existing human constraints, report omissions), and factory/dashboard.html Review drawer/Inbox (render body only via existing safe helpers). Preserve existing pr.checks/comments/verdicts/review_decision/gate fields and read-only/no-tools authority. Add tests to tests/test_factory.py and tests/test_briefing.py (tests/test_feedback.py only if clearer) covering simultaneous CI/review/thread/factory-review evidence, provenance-free VERDICT rejection, idempotent re-reads, head races, pagination/truncation/timeout/malformed/one-source-failure, and regression boundaries; document in README.md and CHANGELOG.md with a sanitized real schema-1 example. Verify with: python -m unittest discover -s tests, plus python -m factory dashboard --json inspecting tickets[].pr.feedback and a live browser check of the Review drawer/Inbox, confirming no mutation/model/GitHub-write calls and no change to runtime-json output.

## Comment by @mikeroySoft (2026-09-10T23:07:54Z)

Factory dispatcher escalating: REVISE verdict after 1 review round(s).

Escalation packet: `/home/mike/dev/mikeroysoft/factory/.factory/escalations/79.md`

Worker logs: `/home/mike/dev/mikeroysoft/factory/.factory/logs/79-attempt-4.log`

Worker handoff notes:

,
              "source_url": "https://github.com/example/project/pull/80#pullrequestreview-2",
              "source_revision": "sha256:7385804af18bbfa75fba4d0c41cb2e7b4d34c314e2ad21d1f3ba09693bd2adc4",
              "source_updated_at": null,
              "review_id": "RV_old",
              "thread_id": null,
              "check_run_id": null,
              "run_attempt": null,
              "observed_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "source_head_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              "relevance": "historical",
              "disposition": {
                "review_state": "DISMISSED",
                "thread_resolved": null,
                "thread_outdated": null,
                "check_status": null,
                "check_conclusion": null
              },
              "author": {
                "login": "shared",
                "kind": "unknown"
              },
              "body": "Historical review retained",
              "truncated": false,
              "location": {
                "path": null,
                "line": null,
                "side": null,
                "original_line": null,
                "original_commit_sha": null
              },
              "summary": null
            },
            {
              "evidence_id": "github:github.com:R_1:PR_80:review_comment:RC_1",
              "kind": "review_comment",
              "source_id": "RC_1",
              "source_url": "https://github.com/example/project/pull/80#discussion_r1",
              "source_revision": "sha256:6e2c80cf03e8838993a9cb457d977815f168054a64d4a1dcf775e07a3c941d5c",
              "source_updated_at": "2026-09-10T12:00:00Z",
              "review_id": "RV_1",
              "thread_id": "T_1",
              "check_run_id": null,
              "run_attempt": null,
              "observed_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "source_head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "relevance": "current_head",
              "disposition": {
                "review_state": null,
                "thread_resolved": false,
                "thread_outdated": false,
                "check_status": null,
                "check_conclusion": null
              },
              "author": {
                "login": "reader",
                "kind": "unknown"
              },
              "body": "<img onerror=alert(1)>",
              "truncated": false,
              "location": {
                "path": "factory/example.py",
                "line": 7,
                "side": "RIGHT",
                "original_line": 6,
                "original_commit_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
              },
              "summary": null
            }
          ],
          "errors": [
            {
              "source": "threads",
              "code": "comment_page_limit",
              "message": "Thread comment page retains at most 100 comments; further comments are unknown."
            }
          ]
        }
      },
      "spend": {
        "seconds": 0,
        "cost": null,
        "rounds": 0
      },
      "human_touch": {
        "escalation_count": 0,
        "escalation_times": [],
        "manager_failures": 0,
        "resolutions": [],
        "ready_for_human_minutes": 0.0,
        "requeue_count": 0
      },
      "events": [
        {
          "at": "2026-09-10T12:00:00Z",
          "kind": "pr-opened",
          "detail": "#80"
        },
        {
          "at": "2026-09-10T12:00:00Z",
          "kind": "verdict",
          "detail": "APPROVE",
          "body": "VERDICT: APPROVE",
          "url": "https://github.com/example/project/pull/80#issuecomment-legacy"
        }
      ],
      "timeline_truncated": false,
      "lock_held": false,
      "attempts": [],
      "gate": null,
      "review": null,
      "prompt": null,
      "pr_body": null,
      "worktree": null
    }
  ]
}
```

### git diff origin/main..HEAD

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index d01fe75..c6ec802 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,7 @@
 
 ## Unreleased
 
+- Add schema-1 source-versioned PR feedback at full dashboard `tickets[].pr.feedback`, shared by Review, Inbox and briefing. Retain simultaneous native review/thread/check evidence and provenance-backed Factory reviews with deterministic identities, explicit unknown/partial coverage, fixed 100-item/two-page/20 KB-body/32-error/30-second bounds, and head-race handling. Reviews carry the provider's own `updatedAt`, so an edited review revises its source revision. Detail reads run only for open PRs; closed and merged PRs keep the schema-1 envelope with `not_collected` sources, which is unknown rather than empty or unsupported. Read-only: no feedback delivery, readiness or merge authority; runtime JSON is unchanged. (#79)
 - Add optional Codebase history: stable commit-timeline maps, baseline comparisons, confidence-aware relationships, pinned source citations, and bounded background refresh through `factory[atlas]`. (#67)
 - Add opt-in direction viability to `factory manage`: `needs-review` PRs before `needs-viability` issues, evidence-cited BUILD/DONT_BUILD/DEFER comments, and label-event replay protection. Only issue BUILD enters `needs-triage`; PRs remain recommendation-only, with no review or handoff mechanics.
 - Fix manager prompt transport to use files, including `factory learn`; validate manager commands in doctor, bound nonzero-exit diagnostics, and count manager failures separately from escalation totals and rounds without overriding human takeover. (#62)
diff --git a/README.md b/README.md
index e44ad67..7b74ff5 100644
--- a/README.md
+++ b/README.md
@@ -696,6 +696,92 @@ for row in read_events(Path(".factory/events.jsonl")):
 PY
 ```
 
+## Source-versioned PR feedback (schema 1)
+
+The full `factory dashboard --json` / `/api/snapshot` observation exposes
+`tickets[].pr.feedback`. The existing Review drawer, Inbox raw evidence, and
+`factory.briefing.sources_for` consume this same object. Non-PR tickets have no
+fabricated feedback. `--runtime-json` does not invoke this collector
+and retains its network-free contract.
+
+`factory.feedback.collect(read, *, repository, pr, issue=None, events=(),
+provenance_complete=True, producer_revision=None, observed_at=None,
+collect_details=True)` is the shared producer. `read` is the existing dashboard
+GitHub transport, accepting `endpoint` for fixed REST GETs or `query`/`variables`
+for GraphQL reads, plus a remaining `timeout`. Source exceptions become sanitized
+coverage/errors without discarding independently observed facts. There is no
+feedback cache, event append, model invocation, delivery, dispatch, readiness, or
+approval decision. The full dashboard's pre-existing lifecycle reconciliation
+remains unchanged.
+
+Detail reads are limited to open pull requests, so closed history never
+multiplies provider calls per refresh. `collect_details=False` reads nothing: the
+schema-1 envelope still carries the caller's independently known repository/PR
+identities and state, `head_sha` stays null, every source is `unavailable` with
+the `not_collected` reason/error code, and ownership stays `unverified`. Review,
+Inbox and briefing report that intentional noncollection explicitly; it is not an
+empty, resolved, or unsupported observation, and it is distinct from an older
+engine that has no `feedback` key at all.
+
+The required envelope is `schema_version`, `producer`, `observed_at`,
+`observation_id`, `repository`, `pr`, `owner`, `coverage`, `items`, and `errors`.
+Native repository/PR IDs are retained alongside host/slug, PR number/URL/head,
+state and nullable draft. Ownership needs an actual same-repository closing-issue
+link plus retained Factory claim provenance; branch text and shared credentials
+do not establish it. Relations are `factory_issue`, `unverified`, `ambiguous`,
+or `none`. A missing key or unsupported schema is unknown, not an empty success.
+
+Items retain native source IDs/links, source revision/update time, review/thread/
+check-run IDs, nullable run attempt, source versus observed head, disposition,
+author, body/truncation, location and provider name/title. Kinds are `review`,
+`review_comment`, `check_run`, `commit_status`, and `factory_review`; all can
+coexist. Review state, thread resolved/outdated state, and check status/conclusion
+remain independent. Missing source SHA is never filled with the current head.
+Relevance is `current_head`, `historical`, or `unknown`; outdated threads cannot
+become current through SHA equality. Provider User attribution remains unknown
+because shared credentials may belong to Factory. A Factory review requires a
+valid recorded review execution result, successful parse/exit and matching target
+SHA; ordinary discussion or `VERDICT` prose is legacy context, not that evidence.
+Unavailable provider fields remain explicit nulls (including check-run update
+time or run attempt when the API does not supply them).
+
+Fixed bounds in `factory/feedback.py`: `ITEM_LIMIT=100` per reviews, threads/
+comments and checks/statuses; `PAGE_LIMIT=2`; `BODY_LIMIT=20000` UTF-8 bytes per
+body; `ERROR_LIMIT=32`; `DETAIL_TIMEOUT=30` seconds, with initial/final head reads.
+Reviews and review threads are read as bounded GraphQL connections carrying native
+IDs, links, the reviewed commit and the provider's own `updatedAt` (an edited review
+revises it; a submission time would not). Both use up to two pages; the combined
+checks source reserves one page for native check runs and one for commit statuses. Nested thread comment
+overflow is explicit rather than an unbounded fan-out. Provenance reuses the
+existing safe, bounded 2 MB committed-event tail reader. Every source (`pr`,
+`reviews`, `threads`, `checks`) reports `status` (`complete`, `partial`,
+`unavailable`), nullable `observed_at`/`reason`, and `truncated`. A cap, malformed
+response, missing SHA, failed read, or head race never establishes disappearance
+or resolution. A failed final read exposes an unknown head; raced reads retain
+facts and both observed heads while making relevance unknown. Truncated text
+without a reliable provider update signal explicitly lacks byte-exact change
+detection beyond the retained body.
+
+Canonical JSON is UTF-8, sorted keys, compact separators and explicit nulls.
+Identity strings are stripped/NFC-normalized; host/slug and SHA hex are lowercase.
+Evidence IDs namespace provider, host, repository ID, PR ID, kind and source ID.
+`source_revision` is `sha256:` over exactly `kind`, `source_id`, `review_id`,
+`thread_id`, `check_run_id`, `run_attempt`, `source_head_sha`, `source_updated_at`,
+`author`, `body`, `truncated`, `location`, `disposition`, `summary`.
+`observation_id` hashes repository/PR native IDs, final observed head, sorted
+`(evidence_id, source_revision)` pairs, and coverage status/truncated/reason.
+Collection timestamps, URLs, relevance and presentation order are excluded.
+Unchanged polls keep identity; edits/resolution/dismissal/outcome changes revise
+the same source. The producer revision is a clean source-checkout Git revision,
+otherwise null, never a CLI version.
+
+Briefing appends feedback behind existing evidence and human constraints and
+reports omissions in its reserved coverage citation. Source text is quoted
+untrusted evidence, rendered through safe text helpers. No consumer may infer
+delivery, fixed status, merge approval or readiness from this read-only schema.
+B2 (#79) does not authorize #15 delivery or change its held status; acceptance
+requires the actual merged producer revision and a separately authorized handoff.
+
 ## Bounded runtime JSON (schema 1)
 
 `factory dashboard --runtime-json` prints one JSON object and exits. It is a
diff --git a/factory/briefing.py b/factory/briefing.py
index 9cc9991..43658fe 100644
--- a/factory/briefing.py
+++ b/factory/briefing.py
@@ -17,6 +17,7 @@ from datetime import UTC, datetime
 from pathlib import Path
 
 from factory.config import Config
+from factory.feedback import NOT_COLLECTED
 
 REQUEST_CAP = 100_000
 QUESTION_CAP = 4_000
@@ -205,7 +206,7 @@ def sources_for(
         (f"escalations/{number}.md", "Escalation packet"),
         (f"manager-{number}.md", "Manager notes"),
         (f"wt-{number}/.factory/gate-report-{number}.md", "Gate report"),
-        (f"review-{number}.md", "Review verdict"),
+        (f"review-{number}.md", "Legacy review verdict · provenance unknown"),
         (f"pr-body-{number}.md", "PR body"),
     ]
     pr = ticket.get("pr") or {}
@@ -228,6 +229,10 @@ def sources_for(
     state = {k: ticket.get(k) for k in ("number", "title", "state", "stage", "labels", "assignees", "worker", "lock_held", "phase", "updated_at", "spend")}
     if pr:
         state["pull_request"] = {k: pr.get(k) for k in ("number", "state", "approved", "draft", "checks", "review_decision", "merged_at")}
+        state["pull_request_evidence_notice"] = (
+            "Legacy labels, check rollups and review decisions are context, not source-versioned "
+            "approval or delivery authority. Use the appended PR feedback and its coverage."
+        )
     add("Current ticket state", json.dumps(state, ensure_ascii=False, indent=2))
     events = sorted(ticket.get("events", []), key=lambda e: e.get("at") or "")
     comments = [e for e in events if e.get("body")]
@@ -264,7 +269,8 @@ def sources_for(
         add(f"PR #{pr['number']} gate report", pr["gate_text"], url=pr.get("url", ticket["url"]))
     for e in reversed(comments):
         if e not in decisions:
-            add(f"{e.get('kind', 'Comment').capitalize()} · {e.get('at', '')}", e["body"], url=e.get("url") or ticket["url"])
+            label = "Legacy verdict · provenance unknown" if e.get("kind") == "verdict" else e.get("kind", "Comment").capitalize()
+            add(f"{label} · {e.get('at', '')}", e["body"], url=e.get("url") or ticket["url"])
     timeline = [{k: e[k] for k in ("at", "kind", "detail") if k in e} for e in events]
     add("Issue timeline", json.dumps(timeline, ensure_ascii=False, indent=2), url=ticket["url"], truncated=ticket.get("timeline_truncated", False))
     attempts = sorted(ticket.get("attempts", []), key=lambda a: a.get("attempt", 0), reverse=True)
@@ -280,15 +286,81 @@ def sources_for(
     if errors:
         add("Snapshot collection errors", "\n".join(errors))
 
+    feedback_notice = ""
+    feedback_start = len(candidates)
+    if pr:
+        feedback = pr.get("feedback")
+        if not isinstance(feedback, dict) or "schema_version" not in feedback:
+            feedback_notice = (
+                "PR feedback is unsupported/unknown: this snapshot has no "
+                "schema-versioned feedback object. Absence is not a complete empty observation."
+            )
+        elif type(feedback["schema_version"]) is not int or feedback["schema_version"] != 1:
+            feedback_notice = (
+                f"PR feedback is unsupported/unknown: schema_version "
+                f"{feedback['schema_version']!r} is not supported. Its contents were not interpreted."
+            )
+        elif any(
+            key not in feedback
+            for key in ("producer", "observed_at", "observation_id", "repository", "pr", "owner", "coverage", "items", "errors")
+        ) or not isinstance(feedback["items"], list):
+            feedback_notice = (
+                "PR feedback is unsupported/unknown: the schema-1 snapshot is incomplete. "
+                "Its contents were not interpreted."
+            )
+        else:
+            identity = {
+                "schema_version": feedback["schema_version"],
+                "producer": feedback["producer"],
+                "observed_at": feedback["observed_at"],
+                "observation_id": feedback["observation_id"],
+                "repository": feedback["repository"],
+                "pr": feedback["pr"],
+                "owner": feedback["owner"],
+            }
+            coverage = {
+                "coverage": feedback["coverage"],
+                **identity,
+                "errors": feedback["errors"],
+            }
+            uncollected = [
+                error.get("source") for error in feedback["errors"] or []
+                if isinstance(error, dict) and error.get("code") == NOT_COLLECTED
+            ]
+            feedback_notice = (
+                "PR feedback schema 1 coverage (read-only evidence; partial or unavailable "
+                "coverage is unknown, not empty):\n"
+                + (f"Sources {', '.join(sorted(filter(None, uncollected)))} were not collected "
+                   f"({NOT_COLLECTED}): detail reads are limited to open pull requests. "
+                   "Intentional noncollection is not an empty, resolved, or unsupported observation.\n"
+                   if uncollected else "")
+                + json.dumps(coverage, ensure_ascii=False, separators=(",", ":"))
+            )
+            for item in feedback["items"]:
+                source_id = item.get("source_id") if isinstance(item, dict) else None
+                kind = item.get("kind") if isinstance(item, dict) else None
+                payload = {**identity, "item": item}
+                add(
+                    f"PR feedback · {kind or 'unknown'} · {source_id or 'unknown source'}",
+                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
+                    url=item.get("source_url") if isinstance(item, dict) else None,
+                    truncated=bool(item.get("truncated")) if isinstance(item, dict) else False,
+                )
+
     sources, remaining, omitted = [], CONTEXT_CAP - 1_000, 0
-    for candidate in candidates:
+    feedback_omitted = feedback_shortened = 0
+    for index, candidate in enumerate(candidates):
         if len(sources) >= SOURCE_COUNT - 1 or remaining < 256:
             omitted += 1
+            if index >= feedback_start:
+                feedback_omitted += 1
             continue
         source = dict(candidate)
         encoded = source["text"].encode("utf-8")
         cap = min(SOURCE_CAP, remaining)
         source["text"] = encoded[:cap].decode("utf-8", errors="ignore")
+        if index >= feedback_start and len(encoded) > cap:
+            feedback_shortened += 1
         source["truncated"] = bool(source.get("truncated") or len(encoded) > cap)
         if source["truncated"]:
             source["label"] += " · truncated"
@@ -298,6 +370,15 @@ def sources_for(
             sources.append(source)
             remaining -= len(source["text"].encode("utf-8"))
     notices = []
+    if feedback_omitted:
+        notices.append(
+            f"{feedback_omitted} PR feedback evidence item(s) omitted by the "
+            f"{CONTEXT_CAP}-byte / {SOURCE_COUNT}-source context limit."
+        )
+    if feedback_shortened:
+        notices.append(
+            f"{feedback_shortened} PR feedback evidence item(s) shortened by the context byte limit."
+        )
     if omitted:
         notices.append(f"{omitted} additional evidence source(s) omitted by the {CONTEXT_CAP}-byte / {SOURCE_COUNT}-source context limit.")
     if ledger and ledger[1]:
@@ -306,9 +387,25 @@ def sources_for(
         notices.append(ticket.get("timeline_coverage") or "GitHub issue timeline contains only the latest 100 events; earlier decisions may be missing.")
     if pr.get("comments_truncated"):
         notices.append("PR comments contain only the latest 30 entries; earlier decisions may be missing.")
+    if feedback_notice:
+        notices.append(feedback_notice)
     if notices:
         text = "\n".join(notices)
-        sources.append({"id": "S" + str(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")), "label": "Evidence coverage · truncated", "text": text, "truncated": True})
+        data = text.encode("utf-8")
+        cap = min(SOURCE_CAP, CONTEXT_CAP - sum(len(source["text"].encode("utf-8")) for source in sources))
+        cut = len(data) > cap
+        suffix = "\nCoverage detail omitted by context byte limit." if cut else ""
+        text = data[:max(0, cap - len(suffix.encode("utf-8")))].decode("utf-8", errors="ignore") + suffix
+        incomplete = bool(
+            cut or omitted or ledger and ledger[1]
+            or ticket.get("timeline_truncated") or pr.get("comments_truncated")
+        )
+        sources.append({
+            "id": "S" + str(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")),
+            "label": "Evidence coverage" + (" · truncated" if incomplete else ""),
+            "text": text,
+            "truncated": incomplete,
+        })
     return sources
 
 
diff --git a/factory/dashboard.html b/factory/dashboard.html
index e44d76f..a42dd0a 100644
--- a/factory/dashboard.html
+++ b/factory/dashboard.html
@@ -244,6 +244,12 @@
   .attempt .ah button { margin-left: auto; }
   .empty { color: var(--dim); font-size: 11px; padding: 14px 16px; }
   .err { background: rgba(255,42,109,.1); border: 1px solid var(--mag); color: var(--mag); padding: 8px 14px; font-size: 11px; }
+  .feedback-evidence { display: grid; gap: 8px; }
+  .feedback-item { border: 1px solid var(--line); padding: 8px 10px; }
+  .feedback-item > summary { color: var(--ink); word-break: break-word; }
+  .feedback-item .kv { margin: 10px 0; }
+  .feedback-item pre { max-height: 24vh; }
+  .feedback-errors { display: grid; gap: 6px; }
 
   /* tooltip */
   .tip { position: fixed; z-index: 70; pointer-events: none; background: rgba(5,7,12,.95); border: 1px solid var(--cyan);
@@ -984,14 +990,109 @@ function tabGate(t, c) {
   if (text) c.append(pre(text));
   if (t.gate && t.pr && t.pr.gate_text && t.pr.gate_text !== t.gate.text.trim()) { c.append(h('div', { class: 'section-h' }, 'report attached to the PR at open'), pre(t.pr.gate_text)); }
 }
+function feedbackEvidence(t, expanded = false) {
+  const root = h('div', { class: 'feedback-evidence', dataset: { feedbackSchema: t.pr?.feedback?.schema_version ?? 'missing' } });
+  if (!t.pr) {
+    root.append(h('div', { class: 'empty feedback-unsupported' }, 'No pull request exists, so there is no PR feedback observation.'));
+    return root;
+  }
+  const f = t.pr.feedback;
+  if (!f || f.schema_version !== 1) {
+    root.append(h('div', { class: 'err feedback-unsupported', role: 'status' },
+      f ? `PR feedback schema ${String(f.schema_version ?? 'unknown')} is unsupported. This evidence is unknown and non-actionable.`
+        : 'This engine does not expose source-versioned PR feedback. Evidence is unsupported/unknown and non-actionable.'));
+    return root;
+  }
+  const value = v => v == null || v === '' ? 'unknown' : String(v);
+  const kvList = pairs => h('dl', { class: 'kv' }, ...pairs.flatMap(([label, content]) => [h('dt', {}, label), h('dd', {}, content)]));
+  const sourceLink = (url, label) => {
+    const safe = safeSourceURL(url);
+    return safe ? h('a', { href: safe, target: '_blank', rel: 'noopener noreferrer' }, label) : null;
+  };
+  const owner = f.owner || {};
+  const ownerIssue = owner.issue || null;
+  const ownerUnknown = owner.relation !== 'factory_issue' || !ownerIssue;
+  root.append(
+    h('div', { class: 'section-h' }, `source-versioned PR feedback · schema ${f.schema_version}`),
+    kvList([
+      ['repository', `${value(f.repository?.slug)} · host ${value(f.repository?.host)} · native id ${value(f.repository?.id)}`],
+      ['pull request', h('span', {}, sourceLink(f.pr?.url, `#${value(f.pr?.number)} ↗`) || `#${value(f.pr?.number)}`, ` · native id ${value(f.pr?.id)} · ${value(f.pr?.state)} · draft ${value(f.pr?.draft)}`)],
+      ['observed head', h('code', {}, value(f.pr?.head_sha))],
+      ['observed at', value(f.observed_at)],
+      ['observation', h('code', {}, value(f.observation_id))],
+      ['producer', `${value(f.producer?.name)} · revision ${value(f.producer?.revision)}`],
+    ]),
+    h('div', { class: `feedback-owner${ownerUnknown ? ' err' : ''}` },
+      h('div', { class: 'section-h' }, 'containing owner identity'),
+      kvList([
+        ['relation', `${value(owner.relation)}${ownerUnknown ? ' · unknown/non-actionable' : ''}`],
+        ['issue', ownerIssue ? h('span', {}, sourceLink(ownerIssue.url, `#${value(ownerIssue.number)} ↗`) || `#${value(ownerIssue.number)}`, ` · native id ${value(ownerIssue.id)}`) : 'unknown'],
+      ]),
+      h('div', { class: 'hint' }, 'Ownership is evidence, not readiness or approval authority.'),
+      owner.evidence?.length ? h('ul', {}, ...owner.evidence.map(entry => h('li', {}, entry))) : h('div', { class: 'empty' }, 'No ownership evidence recorded.')),
+    h('div', { class: 'section-h' }, 'source coverage'),
+    h('div', { class: 'checks feedback-coverage' },
+      ...['pr', 'reviews', 'threads', 'checks'].map(source => {
+        const coverage = f.coverage?.[source];
+        const state = coverage?.status === 'complete' ? 'SUCCESS' : coverage?.status === 'unavailable' ? 'ERROR' : 'PENDING';
+        return h('span', { class: `chk ${state}`, dataset: { feedbackSource: source }, title: coverage?.reason || 'No coverage reason recorded' },
+          `${source} ${value(coverage?.status)} · observed ${value(coverage?.observed_at)} · truncated ${value(coverage?.truncated)}${coverage?.reason ? ` · ${coverage.reason}` : ''}`);
+      })),
+    h('div', { class: 'hint' }, 'Partial or unavailable coverage never means that no feedback exists.'),
+  );
+  const uncollected = (f.errors || []).filter(error => error?.code === 'not_collected').map(error => value(error.source));
+  if (uncollected.length) root.append(h('div', { class: 'err feedback-not-collected', role: 'status' },
+    `Detail sources ${uncollected.join(', ')} were not collected: detail reads are limited to open pull requests. Intentional noncollection is unknown, not empty, resolved, or unsupported evidence.`));
+  root.append(h('div', { class: 'section-h' }, `simultaneous evidence · ${f.items?.length || 0} item${f.items?.length === 1 ? '' : 's'}`));
+  if (!f.items?.length) {
+    const complete = ['reviews', 'threads', 'checks'].every(source => f.coverage?.[source]?.status === 'complete');
+    root.append(h('div', { class: 'empty feedback-empty' }, uncollected.length ? 'No items were returned because these sources were not read for this non-open pull request.' : complete ? 'The observed feedback sources were complete and returned no items.' : 'No items were returned, but incomplete coverage makes absence unknown.'));
+  }
+  for (const item of f.items || []) {
+    const disposition = item.disposition || {}, location = item.location || {};
+    const detail = h('details', { class: 'feedback-item', open: expanded, dataset: { evidenceId: value(item.evidence_id), relevance: value(item.relevance), kind: value(item.kind) } },
+      h('summary', {}, `${value(item.kind)} · ${value(item.summary || item.source_id)} · relevance ${value(item.relevance)}`),
+      kvList([
+        ['evidence id', h('code', {}, value(item.evidence_id))],
+        ['source id', value(item.source_id)],
+        ['source revision', h('code', {}, value(item.source_revision))],
+        ['observed head', h('code', {}, value(item.observed_head_sha))],
+        ['source head', h('code', {}, value(item.source_head_sha))],
+        ['updated at', value(item.source_updated_at)],
+        ['author', `${value(item.author?.login)} · ${value(item.author?.kind)}`],
+        ['review/thread', `${value(item.review_id)} / ${value(item.thread_id)}`],
+        ['check run/attempt', `${value(item.check_run_id)} / ${value(item.run_attempt)}`],
+        ['relevance', value(item.relevance)],
+        ['review state', value(disposition.review_state)],
+        ['thread resolved', value(disposition.thread_resolved)],
+        ['thread outdated', value(disposition.thread_outdated)],
+        ['check status', value(disposition.check_status)],
+        ['check conclusion', value(disposition.check_conclusion)],
+        ['location', `${value(location.path)} · line ${value(location.line)} · side ${value(location.side)} · original line ${value(location.original_line)} · original commit ${value(location.original_commit_sha)}`],
+      ]),
+      item.truncated ? h('div', { class: 'err' }, 'Body truncated by the bounded collector; use the provider source for omitted text.') : null,
+      h('div', { class: 'section-h' }, 'source body'),
+      pre(item.body),
+      sourceLink(item.source_url, 'Open provider source ↗') || h('span', { class: 'hint feedback-source-unavailable' }, 'Provider source link unavailable.'));
+    root.append(detail);
+  }
+  root.append(h('div', { class: 'section-h' }, `collection errors · ${f.errors?.length || 0}`));
+  if (f.errors?.length) root.append(h('div', { class: 'feedback-errors' }, ...f.errors.map(error =>
+    h('div', { class: 'err feedback-error', dataset: { feedbackSource: value(error.source), feedbackCode: value(error.code) } }, `${value(error.source)} · ${value(error.code)} · ${value(error.message)}`))));
+  else root.append(h('div', { class: 'empty' }, 'No collection errors recorded.'));
+  root.append(h('div', { class: 'hint' }, 'Feedback is read-only evidence. It does not imply delivery, resolution, readiness, or approval for merge.'));
+  return root;
+}
 function tabReview(t, c) {
+  c.append(feedbackEvidence(t, true));
   const vs = t.pr ? t.pr.verdicts : [];
   const last = vs.at(-1);
-  c.append(h('div', { class: 'section-h' }, last ? `latest verdict: ${last.verdict} · ${ago(last.at)}` : 'no reviewer verdict on the PR'));
-  if (last || t.review) c.append(h('button', { onclick: () => openFM(t.number, t.review ? { path: t.review.path, label: 'Local review' } : { match: 'verdict|review', label: 'PR review verdict' }) }, 'Ask FM about this review'));
-  if (last) c.append(h('div', { class: 'checks' }, ...vs.map(v => h('span', { class: `chk ${v.verdict === 'APPROVE' ? 'PASS' : 'FAIL'}`, title: fmt(v.at) }, `${v.verdict} ${hm(v.at)}`))));
-  vs.slice().reverse().forEach((v, i) => c.append(h('details', { open: i === 0 }, h('summary', {}, `${v.verdict} · ${fmt(v.at)}`), pre(v.body))));
-  if (t.review) c.append(h('div', { class: 'section-h' }, `local review-${t.number}.md · ${t.review.verdict || '?'} · ${ago(t.review.mtime)}`), h('details', {}, h('summary', {}, t.review.path), pre(t.review.text)));
+  c.append(h('div', { class: 'section-h' }, 'legacy review context · not source-versioned authority'));
+  c.append(h('div', { class: 'hint' }, last ? `Latest legacy verdict record: ${last.verdict} · ${ago(last.at)}. This is not trusted source-versioned approval.` : 'No legacy reviewer verdict record on the PR.'));
+  if (last || t.review) c.append(h('button', { onclick: () => openFM(t.number, t.review ? { path: t.review.path, label: 'Local review' } : { match: 'verdict|review', label: 'Legacy PR review context' }) }, 'Ask FM about this legacy review context'));
+  if (last) c.append(h('div', { class: 'checks' }, ...vs.map(v => h('span', { class: 'chk', title: fmt(v.at) }, `legacy ${v.verdict} ${hm(v.at)}`))));
+  vs.slice().reverse().forEach((v, i) => c.append(h('details', { open: i === 0 }, h('summary', {}, `legacy ${v.verdict} · ${fmt(v.at)}`), pre(v.body))));
+  if (t.review) c.append(h('div', { class: 'section-h' }, `legacy local review-${t.number}.md · ${t.review.verdict || '?'} · ${ago(t.review.mtime)}`), h('details', {}, h('summary', {}, t.review.path), pre(t.review.text)));
 }
 function tabBranch(t, c) {
   const w = t.worktree, p = t.pr;
@@ -1156,14 +1257,29 @@ function taskFacts(item) {
   facts.push(t.assignees.length ? `Currently assigned to @${t.assignees.join(', @')}. Routing labels alone do not release an assignment.` : 'Currently unassigned.');
   if (t.gate) {
     const failures = Object.entries(t.gate.checks).filter(([, value]) => value === 'FAIL').map(([name]) => name);
-    facts.push(failures.length ? `Gate failures: ${failures.join(', ')}.` : `Latest gate: ${Object.entries(t.gate.checks).map(([name, value]) => `${name} ${value}`).join(' · ') || 'no check results recorded'}.`);
+    facts.push(failures.length ? `Legacy local gate failures: ${failures.join(', ')}.` : `Legacy local gate context: ${Object.entries(t.gate.checks).map(([name, value]) => `${name} ${value}`).join(' · ') || 'no check results recorded'}.`);
   } else facts.push('No local gate report is available.');
   if (t.pr) {
-    facts.push(`PR #${t.pr.number}: ${t.pr.state.toLowerCase()}, ${t.pr.approved ? 'factory-approved' : 'not approved'}, CI ${t.pr.checks.state || 'unknown'}.`);
+    facts.push(`PR #${t.pr.number}: ${t.pr.state.toLowerCase()}, ${t.pr.approved ? 'factory-approved label present' : 'factory-approved label absent'}, legacy CI rollup ${t.pr.checks.state || 'unknown'}. Labels and legacy rollups are not source-versioned feedback authority.`);
     const failed = t.pr.checks.list.filter(c => ['FAILURE', 'ERROR'].includes(c.result)).map(c => c.name);
-    if (failed.length) facts.push(`Failing CI checks: ${failed.join(', ')}. Approval does not bypass CI.`);
+    if (failed.length) facts.push(`Legacy failing CI check context: ${failed.join(', ')}. Approval does not bypass CI.`);
     const verdict = t.pr.verdicts.at(-1);
-    if (verdict) facts.push(`Latest reviewer verdict: ${verdict.verdict} (${fmt(verdict.at)}).`);
+    if (verdict) facts.push(`Latest legacy reviewer verdict record: ${verdict.verdict} (${fmt(verdict.at)}). It is not trusted source-versioned approval.`);
+    const feedback = t.pr.feedback;
+    if (!feedback || feedback.schema_version !== 1) {
+      facts.push(feedback ? `PR feedback schema ${String(feedback.schema_version ?? 'unknown')} is unsupported; feedback is unknown and non-actionable.` : 'This engine does not expose source-versioned PR feedback; feedback is unsupported/unknown and non-actionable.');
+    } else {
+      const owner = feedback.owner || {};
+      facts.push(owner.relation === 'factory_issue' && owner.issue
+        ? `Feedback owner identity: Factory issue #${owner.issue.number} (${owner.evidence?.length || 0} evidence record${owner.evidence?.length === 1 ? '' : 's'}).`
+        : `Feedback owner identity: ${owner.relation || 'unknown'}; ownership is unknown/non-actionable.`);
+      const counts = { current_head: 0, historical: 0, unknown: 0 };
+      for (const evidence of feedback.items || []) counts[evidence.relevance] = (counts[evidence.relevance] || 0) + 1;
+      facts.push(`Source-versioned feedback: ${feedback.items?.length || 0} items · ${counts.current_head} current head · ${counts.historical} historical · ${counts.unknown} unknown. Coverage: ${['pr', 'reviews', 'threads', 'checks'].map(source => `${source} ${feedback.coverage?.[source]?.status || 'unknown'}`).join(' · ')}.`);
+      const uncollected = (feedback.errors || []).filter(error => error?.code === 'not_collected').map(error => String(error.source));
+      if (uncollected.length) facts.push(`Feedback detail sources ${uncollected.join(', ')} were not collected: detail reads are limited to open pull requests. This is intentional noncollection, not empty, resolved, or unsupported evidence.`);
+      facts.push('Feedback is read-only evidence and does not authorize delivery, resolution, readiness, or approval for merge.');
+    }
   }
   return h('ul', {}, ...facts.map(fact => h('li', {}, fact)));
 }
@@ -1364,6 +1480,7 @@ function renderTaskBriefing(item) {
   const raw = h('details', { class: 'briefing-sources' }, h('summary', {}, `Inspect raw evidence${sources.length ? ` · ${sources.length} sources` : ''}`));
   if (sources.length) raw.append(...sources.map(source => sourceDetails(t.number, source)));
   else raw.append(h('details', {}, h('summary', {}, 'Issue body'), pre(t.body)), h('button', { onclick: () => openTicket(t.number, 'timeline') }, 'Open task timeline and artifacts'));
+  if (t.pr) raw.append(feedbackEvidence(t));
   return h('article', {},
     h('header', { class: 'briefing-intro' }, h('p', { class: 'briefing-label' }, `${INBOX_KINDS[item.kind]} · #${t.number} · ${t.title}`),
       h('h2', { id: 'briefing-question', class: 'briefing-question', tabindex: -1 }, briefing ? cited(briefing.question, sources, t.number) : item.kind === 'needs-info' ? item.why : `What should happen next for #${t.number}?`),
diff --git a/factory/dashboard.py b/factory/dashboard.py
index dfd2b76..6fecd9d 100644
--- a/factory/dashboard.py
+++ b/factory/dashboard.py
@@ -27,7 +27,7 @@ from pathlib import Path
 from urllib.parse import parse_qs, urlparse
 from uuid import uuid4
 
-from factory import __version__, briefing, codebase, config, dispatch, lifecycle, stats
+from factory import __version__, briefing, codebase, config, dispatch, feedback, lifecycle, stats
 from factory.config import (
     LABEL_AGENT,
     LABEL_APPROVED,
@@ -121,9 +121,10 @@ GRAPHQL = """
 query($owner:String!,$name:String!{UVARS}){
 {UPSTREAM}
   repository(owner:$owner,name:$name){
+    id
     issues(first:100,orderBy:{field:CREATED_AT,direction:DESC}){
       nodes{
-        number title state url createdAt updatedAt closedAt body
+        id number title state url createdAt updatedAt closedAt body
         labels(first:20){nodes{name color}}
         assignees(first:5){nodes{login}}
         timelineItems(last:100,itemTypes:[LABELED_EVENT,UNLABELED_EVENT,
@@ -146,7 +147,7 @@ query($owner:String!,$name:String!{UVARS}){
     }
     pullRequests(first:100,orderBy:{field:CREATED_AT,direction:DESC}){
       nodes{
-        number title state url headRefName createdAt mergedAt closedAt isDraft
+        id number title state url headRefName headRefOid createdAt mergedAt closedAt isDraft
         additions deletions changedFiles body reviewDecision
         labels(first:10){nodes{name}}
         comments(last:30){pageInfo{hasPreviousPage} nodes{createdAt author{login} body url}}
@@ -192,29 +193,33 @@ def file_meta(path: Path) -> dict | None:
 # ---------------------------------------------------------------- GitHub
 
 
-def github() -> dict:
-    owner, name = REPO.split("/", 1)
-    query = GRAPHQL.replace(
-        "{UVARS}", ",$uowner:String!,$uname:String!" if UPSTREAM else ""
-    ).replace("{UPSTREAM}", GRAPHQL_UPSTREAM if UPSTREAM else "")
-    cmd = [
-        "gh",
-        "api",
-        "graphql",
-        "-f",
-        f"query={query}",
-        "-F",
-        f"owner={owner}",
-        "-F",
-        f"name={name}",
-    ]
-    if UPSTREAM:
-        uowner, uname = UPSTREAM.split("/", 1)
-        cmd += ["-F", f"uowner={uowner}", "-F", f"uname={uname}"]
-    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
+def github(*, endpoint: str | None = None, query: str | None = None,
+           variables: dict | None = None, timeout: float | None = None) -> dict | list:
+    """Shared full-snapshot transport; feedback supplies only fixed read queries."""
+    if endpoint is not None:
+        cmd = ["gh", "api", "--method", "GET", endpoint]
+    else:
+        if query is None:
+            owner, name = REPO.split("/", 1)
+            query = GRAPHQL.replace(
+                "{UVARS}", ",$uowner:String!,$uname:String!" if UPSTREAM else ""
+            ).replace("{UPSTREAM}", GRAPHQL_UPSTREAM if UPSTREAM else "")
+            variables = {"owner": owner, "name": name}
+            if UPSTREAM:
+                variables["uowner"], variables["uname"] = UPSTREAM.split("/", 1)
+        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
+        for key, value in (variables or {}).items():
+            if value is not None:
+                cmd += ["-F" if type(value) is int else "-f", f"{key}={value}"]
+    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
     if proc.returncode:
         raise RuntimeError(proc.stderr.strip() or "gh api graphql failed")
-    return json.loads(proc.stdout)["data"]
+    value = json.loads(proc.stdout)
+    if endpoint is not None:
+        return value
+    if value.get("errors"):
+        raise RuntimeError("GitHub query was incomplete")
+    return value["data"]
 
 
 def pr_checks(pr: dict) -> dict:
@@ -803,6 +808,8 @@ def snapshot() -> dict:
     errors = []
     issues: list[dict] = []
     prs: dict[int, dict] = {}
+    raw_prs: dict[int, dict] = {}
+    repo: dict = {}
     gh_upstream = None
     try:
         data = github()
@@ -817,6 +824,7 @@ def snapshot() -> dict:
             # Prefer the merged PR, else the newest, when a branch had several.
             if n not in prs or (rec["merged_at"] and not prs[n]["merged_at"]):
                 prs[n] = rec
+                raw_prs[n] = pr
     except (RuntimeError, ValueError, KeyError) as exc:
         errors.append(f"github: {exc}")
 
@@ -839,10 +847,53 @@ def snapshot() -> dict:
     for execution in executions:
         if execution["ticket"] is not None:
             by_ticket.setdefault(execution["ticket"], []).append(execution)
+    provenance, provenance_complete = [], True
+    if raw_prs:
+        try:
+            ledger = briefing.bounded_file(FACTORY, "events.jsonl", briefing.EVENT_READ_CAP, tail=True)
+            if ledger is not None:
+                text, cut = ledger
+                lines = text.split("\n")
+                unfinished = lines.pop()
+                provenance_complete = not cut and not unfinished
+                if cut and lines:
+                    lines = lines[1:]
+                for line in lines:
+                    try:
+                        row = json.loads(line)
+                        if isinstance(row, dict):
+                            provenance.append(row)
+                        else:
+                            provenance_complete = False
+                    except ValueError:
+                        provenance_complete = False
+            else:
+                provenance_complete = False
+        except OSError:
+            provenance_complete = False
+        # Revision belongs to the imported source checkout, not the observed repository.
+        source_root = Path(__file__).resolve().parent
+        revision = sh(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
+        if (not sh(["git", "ls-files", "--error-unmatch", "--", "feedback.py"], cwd=source_root).strip()
+                or sh(["git", "status", "--porcelain", "--", "."], cwd=source_root).strip()):
+            revision = None
     for issue in issues:
         n = issue["number"]
         if not selected_issue(issue, prs, on_disk, by_ticket, audit):
             continue
+        if n in raw_prs:
+            raw = raw_prs[n]
+            # Detail reads stay bounded to open work: the snapshot lists up to 100
+            # PRs, and closed history must not multiply provider calls per refresh.
+            prs[n]["feedback"] = feedback.collect(
+                github, repository={"id": repo.get("id"), "slug": REPO,
+                                    "host": urlparse(raw["url"]).hostname or "github.com"},
+                pr={"id": raw.get("id"), "number": raw["number"], "url": raw["url"],
+                    "state": raw.get("state"), "draft": raw.get("isDraft")},
+                issue={"id": issue.get("id"), "number": n, "url": issue["url"]},
+                events=provenance, provenance_complete=provenance_complete,
+                producer_revision=revision, collect_details=raw.get("state") == "OPEN",
+            )
         tickets.append(build_ticket(
             issue, prs.get(n), disk_state(n), spend.get(n), by_ticket.get(n), audit=audit.get(n),
         ))
diff --git a/factory/feedback.py b/factory/feedback.py
new file mode 100644
index 0000000..03de855
--- /dev/null
+++ b/factory/feedback.py
@@ -0,0 +1,441 @@
+"""Schema-1, read-only PR feedback. Transport is supplied by the full snapshot.
+
+No state, model, or mutation lives here. Missing evidence is never authority.
+"""
+from __future__ import annotations
+
+import hashlib
+import json
+import re
+import subprocess
+import time
+import unicodedata
+from datetime import datetime, timezone
+from urllib.parse import quote
+
+from factory import lifecycle
+
+ITEM_LIMIT = 100
+PAGE_LIMIT = 2
+BODY_LIMIT = 20_000
+ERROR_LIMIT = 32
+DETAIL_TIMEOUT = 30
+SOURCES = ('pr', 'reviews', 'threads', 'checks')
+NOT_COLLECTED = 'not_collected'
+NOT_COLLECTED_MESSAGE = ('Detail collection is limited to open pull requests; this source was not read. '
+                         'Intentional noncollection is unknown, not empty, resolved, or unsupported.')
+REVISION_FIELDS = ('kind', 'source_id', 'review_id', 'thread_id', 'check_run_id',
+                   'run_attempt', 'source_head_sha', 'source_updated_at', 'author',
+                   'body', 'truncated', 'location', 'disposition', 'summary')
+HEAD_QUERY = '''query($owner:String!,$name:String!,$number:Int!){
+ repository(owner:$owner,name:$name){id pullRequest(number:$number){
+ id number url headRefOid state isDraft
+ closingIssuesReferences(first:100){nodes{id number url} pageInfo{hasNextPage}}
+ }}}'''
+REVIEW_QUERY = '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
+ repository(owner:$owner,name:$name){pullRequest(number:$number){
+ reviews(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
+ id url body state updatedAt author{login __typename} commit{oid}
+ }}}}}'''
+THREAD_QUERY = '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
+ repository(owner:$owner,name:$name){pullRequest(number:$number){
+ reviewThreads(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
+ id isResolved isOutdated comments(first:100){pageInfo{hasNextPage} nodes{
+ id url body updatedAt author{login __typename} commit{oid} originalCommit{oid}
+ path line originalLine diffSide pullRequestReview{id}
+ }}}}}}}'''
+
+
+def canonical(value: object) -> bytes:
+    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
+
+
+def digest(value: object) -> str:
+    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()
+
+
+def identity(value: object) -> str | None:
+    if not isinstance(value, (str, int)) or isinstance(value, bool):
+        return None
+    return unicodedata.normalize('NFC', str(value).strip()) or None
+
+
+def sha(value: object) -> str | None:
+    return value.lower() if isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{40,64}', value) else None
+
+
+def utc(value: object) -> str | None:
+    if not isinstance(value, str):
+        return None
+    try:
+        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
+        if parsed.tzinfo is not None:
+            return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
+    except ValueError:
+        pass
+    return None
+
+
+def collect(read, *, repository: dict, pr: dict, issue: dict | None = None,
+            events: list[dict] = (), provenance_complete: bool = True,
+            producer_revision: str | None = None, observed_at: str | None = None,
+            collect_details: bool = True) -> dict:
+    """Collect one PR using read(endpoint=... | query=..., variables=..., timeout=...).
+
+    The transport returns decoded REST JSON or GraphQL data and enforces timeout.
+    All source failures become bounded, sanitized coverage; independent facts survive.
+    events must be retained, committed rows from the repository's bounded journal read.
+    collect_details=False reads nothing: the caller's independently known identities
+    and state are retained and every source is unavailable, never complete-empty.
+    """
+    observed_at = utc(observed_at) or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
+    repository = {"id": identity(repository.get('id')), "slug": repository['slug'].strip().lower(),
+                  "host": repository['host'].strip().lower()}
+    result = {'schema_version': 1, 'producer': {'name': 'factory.pr-feedback', 'revision': sha(producer_revision)},
+              'observed_at': observed_at, 'observation_id': None, 'repository': repository,
+              'pr': {'id': identity(pr.get('id')), 'number': pr['number'], 'url': pr['url'], 'head_sha': None,
+                     'state': pr['state'].lower() if isinstance(pr.get('state'), str)
+                     and pr['state'].lower() in ('open', 'closed', 'merged') else 'unknown',
+                     'draft': pr['draft'] if type(pr.get('draft')) is bool else None},
+              'owner': {'issue': None, 'relation': 'unverified', 'evidence': []},
+              'coverage': {s: {'status': 'unavailable', 'observed_at': None, 'truncated': False, 'reason': None} for s in SOURCES},
+              'items': [], 'errors': []}
+    reasons = {s: set() for s in SOURCES}
+    deadline = time.monotonic() + DETAIL_TIMEOUT
+    owner, name = repository['slug'].split('/', 1)
+    variables = {'owner': owner, 'name': name, 'number': pr['number']}
+    prefix = 'repos/' + repository['slug']
+    counts = {s: 0 for s in SOURCES}
+    identities = {}
+    conflicts = set()
+
+    def gap(source, code, message, *, truncated=False):
+        coverage = result['coverage'][source]
+        if coverage['observed_at'] is not None:
+            coverage['status'] = 'partial'
+        coverage['truncated'] |= truncated
+        reasons[source].add(code + ': ' + message)
+        error = {'source': source, 'code': code, 'message': message}
+        if error not in result['errors'] and len(result['errors']) < ERROR_LIMIT:
+            result['errors'].append(error)
+
+    def success(source):
+        c = result['coverage'][source]
+        c['observed_at'] = observed_at
+        c['status'] = 'partial' if reasons[source] else 'complete'
+
+    def request(source, **kwargs):
+        # Historical PRs are never fanned out: noncollection is recorded, not read.
+        if not collect_details:
+            gap(source, NOT_COLLECTED, NOT_COLLECTED_MESSAGE)
+            return None
+        try:
+            remaining = deadline - time.monotonic()
+            if remaining <= 0:
+                raise TimeoutError
+            value = read(**kwargs, timeout=remaining)
+            if time.monotonic() > deadline:
+                raise TimeoutError
+            if not isinstance(value, (dict, list)):
+                raise ValueError
+            return value
+        except (TimeoutError, subprocess.TimeoutExpired):
+            gap(source, 'timeout', 'Feedback detail group reached its 30-second limit.')
+        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
+            gap(source, 'source_unavailable', 'Provider source could not be read or decoded.')
+        return None
+
+    def head():
+        data = request('pr', query=HEAD_QUERY, variables=variables)
+        if data is None:
+            return None
+        try:
+            repo = data['repository']
+            p = repo['pullRequest']
+            if not isinstance(p, dict) or p.get('number') != pr['number']:
+                raise ValueError
+            if (not identity(repo.get('id')) or not identity(p.get('id'))
+                    or repository['id'] not in (None, identity(repo['id']))
+                    or result['pr']['id'] not in (None, identity(p['id']))):
+                gap('pr', 'identity_conflict', 'Provider repository or PR native identity is missing or contradictory.')
+                return None
+            repository['id'] = identity(repo['id'])
+            result['pr']['id'] = identity(p['id'])
+            success('pr')
+            if not sha(p.get('headRefOid')):
+                gap('pr', 'head_missing', 'Provider PR head is unavailable.')
+            p = dict(p)
+            if p.get('state') not in ('OPEN', 'CLOSED', 'MERGED'):
+                p['state'] = 'UNKNOWN'
+                gap('pr', 'state_unknown', 'Provider PR state is unavailable or unsupported.')
+            if type(p.get('isDraft')) is not bool:
+                gap('pr', 'draft_unknown', 'Provider PR draft state is unavailable.')
+            links = p.get('closingIssuesReferences')
+            if (not isinstance(links, dict) or not isinstance(links.get('nodes'), list)
+                    or not isinstance(links.get('pageInfo'), dict)
+                    or type(links['pageInfo'].get('hasNextPage')) is not bool):
+                p['closingIssuesReferences'] = {}
+                gap('pr', 'links_unknown', 'Provider issue-link coverage is unavailable.')
+            elif links['pageInfo']['hasNextPage']:
+                gap('pr', 'issue_link_limit', 'Issue links stop after 100 entries; ownership is unknown.', truncated=True)
+            return p
+        except (KeyError, TypeError, ValueError, AttributeError):
+            gap('pr', 'malformed_response', 'Provider PR response is malformed.')
+            return None
+
+    def item(source, kind, source_id, *, body='', updated=None, source_sha=None, url=None,
+             author=None, review_id=None, thread_id=None, check_run_id=None, attempt=None,
+             location=None, disposition=None, summary=None, factory=False):
+        sid = identity(source_id)
+        if not sid or not repository['id'] or not result['pr']['id']:
+            gap(source, 'identity_missing', 'Required native source, repository, or PR identity is unavailable.')
+            return
+        if counts[source] >= ITEM_LIMIT:
+            gap(source, 'item_limit', 'Source retains at most 100 items.', truncated=True)
+            return
+        if not isinstance(body, str):
+            gap(source, 'malformed_body', 'Provider body is not text.')
+            body = ''
+        encoded = body.encode('utf-8')
+        cut = len(encoded) > BODY_LIMIT
+        body = encoded[:BODY_LIMIT].decode('utf-8', errors='ignore')
+        updated = utc(updated)
+        if cut:
+            gap(source, 'body_limit', 'Source body retains at most 20000 UTF-8 bytes.', truncated=True)
+            if updated is None:
+                gap(source, 'change_detection_incomplete', 'No reliable update signal: changes beyond retained body cannot be detected.', truncated=True)
+        source_sha = sha(source_sha)
+        if source_sha is None:
+            gap(source, 'source_head_missing', 'Source commit identity is unavailable; relevance is unknown.')
+        actor = author if isinstance(author, dict) else {}
+        author_kind = 'factory_reviewer' if factory else 'bot' if actor.get('type', actor.get('__typename')) == 'Bot' else 'unknown'
+        value = {'evidence_id': ':'.join(quote(v, safe='') for v in ('github', repository['host'], repository['id'], result['pr']['id'], kind, sid)),
+                 'kind': kind, 'source_id': sid, 'source_url': url if isinstance(url, str) else None,
+                 'source_revision': None, 'source_updated_at': updated,
+                 'review_id': identity(review_id), 'thread_id': identity(thread_id),
+                 'check_run_id': identity(check_run_id), 'run_attempt': attempt if type(attempt) is int and attempt > 0 else None,
+                 'observed_head_sha': None, 'source_head_sha': source_sha, 'relevance': 'unknown',
+                 'disposition': dict.fromkeys(('review_state', 'thread_resolved', 'thread_outdated', 'check_status', 'check_conclusion')),
+                 'author': {'login': identity(actor.get('login')), 'kind': author_kind}, 'body': body, 'truncated': cut,
+                 'location': dict.fromkeys(('path', 'line', 'side', 'original_line', 'original_commit_sha')),
+                 'summary': summary if isinstance(summary, str) else None}
+        value['location'].update(location or {})
+        value['disposition'].update(disposition or {})
+        for field, allowed in {
+            'review_state': ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'),
+            'thread_resolved': (True, False), 'thread_outdated': (True, False),
+            'check_status': ('queued', 'in_progress', 'completed', 'waiting', 'requested', 'pending', 'success', 'failure', 'error'),
+            'check_conclusion': ('success', 'failure', 'neutral', 'cancelled', 'skipped', 'timed_out', 'action_required', 'stale', 'startup_failure'),
+        }.items():
+            retained = value['disposition'][field]
+            if retained is not None and (retained not in allowed or type(retained) is not type(allowed[0])):
+                value['disposition'][field] = None
+                gap(source, 'disposition_unknown', 'Provider disposition is missing or unsupported.')
+        for field in ('line', 'original_line'):
+            retained = value['location'][field]
+            if retained is not None and (type(retained) is not int or retained <= 0):
+                value['location'][field] = None
+                gap(source, 'location_unknown', 'Provider line location is malformed.')
+        for field in ('path', 'side'):
+            retained = value['location'][field]
+            if retained is not None and not isinstance(retained, str):
+                value['location'][field] = None
+                gap(source, 'location_unknown', 'Provider path or side is malformed.')
+        value['source_revision'] = digest({k: value[k] for k in REVISION_FIELDS})
+        key = value['evidence_id']
+        if key in conflicts:
+            return
+        previous = identities.get(key)
+        if previous is not None:
+            if previous['source_revision'] != value['source_revision']:
+                result['items'].remove(previous)
+                conflicts.add(key)
+                gap(source, 'source_identity_conflict', 'One source identity returned contradictory retained facts.')
+            return
+        identities[key] = value
+        result['items'].append(value)
+        counts[source] += 1
+
+    # Real reviews retain native IDs, the reviewed commit, and the provider's own
+    # update time, which (unlike a submission time) moves when a review is edited.
+    initial = head()
+    initial_sha = sha((initial or {}).get('headRefOid'))
+    cursor = None
+    for page in range(PAGE_LIMIT):
+        values = request('reviews', query=REVIEW_QUERY, variables={**variables, 'cursor': cursor})
+        if values is None:
+            break
+        try:
+            connection = values['repository']['pullRequest']['reviews']
+            rows = connection['nodes']
+            page_info = connection['pageInfo']
+            if not isinstance(rows, list) or type(page_info['hasNextPage']) is not bool:
+                raise ValueError
+            success('reviews')
+            for row in rows[:ITEM_LIMIT]:
+                if not isinstance(row, dict):
+                    gap('reviews', 'malformed_response', 'Provider review record is malformed.')
+                    continue
+                item('reviews', 'review', row.get('id'), body=row.get('body') or '', updated=row.get('updatedAt'),
+                     source_sha=(row.get('commit') or {}).get('oid'), url=row.get('url'), author=row.get('author'),
+                     review_id=row.get('id'), disposition={'review_state': row.get('state')})
+            if not page_info['hasNextPage']:
+                break
+            if page + 1 == PAGE_LIMIT or counts['reviews'] >= ITEM_LIMIT:
+                gap('reviews', 'page_limit', 'Reviews stopped at two pages or 100 items; unseen evidence is unknown.', truncated=True)
+                break
+            cursor = identity(page_info.get('endCursor'))
+            if cursor is None:
+                raise ValueError
+        except (KeyError, TypeError, ValueError, AttributeError):
+            gap('reviews', 'malformed_response', 'Provider reviews response is malformed.')
+            break
+
+    cursor = None
+    for page in range(PAGE_LIMIT):
+        values = request('threads', query=THREAD_QUERY, variables={**variables, 'cursor': cursor})
+        if values is None:
+            break
+        try:
+            connection = values['repository']['pullRequest']['reviewThreads']
+            threads = connection['nodes']
+            page_info = connection['pageInfo']
+            if not isinstance(threads, list) or type(page_info['hasNextPage']) is not bool:
+                raise ValueError
+            success('threads')
+            for thread in threads[:ITEM_LIMIT]:
+                if not identity(thread.get('id')):
+                    gap('threads', 'identity_missing', 'Native thread identity is unavailable.')
+                    continue
+                if type(thread.get('isResolved')) is not bool or type(thread.get('isOutdated')) is not bool:
+                    gap('threads', 'thread_state_missing', 'Thread resolution or outdated state is unavailable.')
+                comments = thread['comments']
+                if not isinstance(comments['nodes'], list):
+                    raise ValueError
+                for row in comments['nodes'][:ITEM_LIMIT]:
+                    item('threads', 'review_comment', row.get('id'), body=row.get('body') or '', updated=row.get('updatedAt'),
+                         source_sha=(row.get('commit') or {}).get('oid'), url=row.get('url'), author=row.get('author'),
+                         review_id=(row.get('pullRequestReview') or {}).get('id'), thread_id=thread['id'],
+                         location={'path': row.get('path'), 'line': row.get('line'), 'side': row.get('diffSide'),
+                                   'original_line': row.get('originalLine'), 'original_commit_sha': sha((row.get('originalCommit') or {}).get('oid'))},
+                         disposition={'thread_resolved': thread.get('isResolved'), 'thread_outdated': thread.get('isOutdated')})
+                if comments['pageInfo']['hasNextPage'] or len(comments['nodes']) > ITEM_LIMIT:
+                    gap('threads', 'comment_page_limit', 'Thread comment page retains at most 100 comments; further comments are unknown.', truncated=True)
+            if not page_info['hasNextPage']:
+                break
+            if page + 1 == PAGE_LIMIT or counts['threads'] >= ITEM_LIMIT:
+                gap('threads', 'page_limit', 'Threads stopped at two pages or 100 comments; further evidence is unknown.', truncated=True)
+                break
+            cursor = identity(page_info.get('endCursor'))
+            if cursor is None:
+                raise ValueError
+        except (KeyError, TypeError, ValueError, AttributeError):
+            gap('threads', 'malformed_response', 'Provider thread response is malformed.')
+            break
+
+    # Two pages total for the combined checks source: one native check-run page
+    # and one commit-status page. Never spend the status page budget on check runs.
+    if initial_sha:
+        for kind, endpoint in (('check_run', f'{prefix}/commits/{initial_sha}/check-runs?per_page=100&filter=all'),
+                               ('commit_status', f'{prefix}/commits/{initial_sha}/statuses?per_page=100')):
+            values = request('checks', endpoint=endpoint)
+            if values is None:
+                continue
+            try:
+                rows = values['check_runs'] if kind == 'check_run' else values
+                if not isinstance(rows, list):
+                    raise ValueError
+                success('checks')
+                for row in rows[:ITEM_LIMIT]:
+                    if not isinstance(row, dict):
+                        raise ValueError
+                    is_run = kind == 'check_run'
+                    item('checks', kind, row.get('node_id'), url=row.get('html_url', row.get('target_url')),
+                         body='\n\n'.join(v for v in ((row.get('output') or {}).get('summary'), (row.get('output') or {}).get('text')) if isinstance(v, str) and v) if is_run else (row.get('description') or ''),
+                         updated=row.get('updated_at'), source_sha=row.get('head_sha') if is_run else initial_sha,
+                         author=row.get('creator'), check_run_id=row.get('id') if is_run else None,
+                         attempt=row.get('run_attempt'), summary=row.get('name') if is_run else row.get('context'),
+                         disposition={'check_status': row.get('status') if is_run else row.get('state'),
+                                      'check_conclusion': row.get('conclusion') if is_run else None})
+                if (values.get('total_count', len(rows)) > len(rows) if kind == 'check_run' else len(rows) >= ITEM_LIMIT):
+                    gap('checks', 'page_limit', 'Checks/statuses stop after one page each; unseen evidence is unknown.', truncated=True)
+            except (KeyError, TypeError, ValueError, AttributeError):
+                gap('checks', 'malformed_response', 'Provider checks response is malformed.')
+    elif collect_details:
+        gap('checks', 'head_unavailable', 'Checks cannot be queried without an observed immutable commit.')
+    else:
+        gap('checks', NOT_COLLECTED, NOT_COLLECTED_MESSAGE)
+
+    final = head()
+    final_sha = sha((final or {}).get('headRefOid'))
+    if final:
+        result['pr'].update(head_sha=final_sha, state=final.get('state', 'unknown').lower(),
+                            draft=final.get('isDraft') if type(final.get('isDraft')) is bool else None)
+        if result['pr']['state'] not in ('open', 'closed', 'merged'):
+            result['pr']['state'] = 'unknown'
+    raced = collect_details and (initial_sha != final_sha or initial_sha is None or final_sha is None)
+    if raced:
+        for source in SOURCES:
+            gap(source, 'head_race' if initial_sha and final_sha else 'head_unavailable',
+                f'Collection heads initial={initial_sha or "unknown"}, final={final_sha or "unknown"}; applicability is unknown.')
+
+    # Same-repository native issue/link plus a retained Factory claim. Conflicting
+    # links remain ambiguous; a branch name or shared credential never proves this.
+    links = ((final or initial or {}).get('closingIssuesReferences') or {})
+    linked = links.get('nodes')
+    claimed = issue and any(e.get('event') == 'claimed' and e.get('ticket') == issue['number'] for e in events)
+    if isinstance(linked, list):
+        candidates = [v for v in linked if isinstance(v, dict)]
+        if len(candidates) > 1 or candidates and issue and candidates[0].get('number') != issue['number']:
+            result['owner']['relation'] = 'ambiguous'
+            result['owner']['evidence'] = ['Conflicting provider closing-issue links.']
+        elif candidates and issue and candidates[0].get('id') == issue.get('id') and candidates[0].get('url') == issue.get('url') and claimed and not links.get('pageInfo', {}).get('hasNextPage'):
+            result['owner'] = {'issue': {k: issue[k] for k in ('id', 'number', 'url')}, 'relation': 'factory_issue',
+                               'evidence': ['Provider same-repository closing-issue link: ' + issue['url'], 'Retained Factory claimed event for issue #' + str(issue['number'])]}
+        elif not candidates and not issue:
+            result['owner']['relation'] = 'none'
+    elif not collect_details and claimed:
+        result['owner']['evidence'] = ['Retained Factory claimed event for issue #' + str(issue['number'])
+                                       + '; the provider issue link was not read, so ownership stays unverified.']
+    if collect_details and not provenance_complete:
+        gap('reviews', 'provenance_partial', 'Bounded Factory event provenance is incomplete; unseen reviews are unknown.', truncated=True)
+    if collect_details and result['owner']['relation'] != 'factory_issue':
+        gap('pr', 'ownership_unverified', 'Factory issue ownership is missing, conflicting, or unverified.')
+
+    # Accepted lifecycle result IDs are the source, never rendered VERDICT prose.
+    valid = [e for e in events if lifecycle._lifecycle(e)]
+    if result['owner']['relation'] == 'factory_issue':
+        for event in valid:
+            if event['stage'] != 'review' or event['kind'] != 'result' or event['ticket'] != issue['number']:
+                continue
+            group = [e for e in valid if e['execution_id'] == event['execution_id']]
+            if (event.get('returncode') != 0 or event.get('parsed') is not True
+                    or not sha(event.get('head')) or event.get('head') != event.get('actual_head')
+                    or event.get('verdict') not in ('APPROVE', 'REVISE')
+                    or not any(e['kind'] == 'enter' and e['sequence'] < event['sequence'] for e in group)
+                    or not any(e['kind'] == 'exit' and e['sequence'] > event['sequence']
+                               and e['outcome'] == ('approved' if event['verdict'] == 'APPROVE' else 'product_feedback') for e in group)):
+                continue
+            item('reviews', 'factory_review', event['event_id'], source_sha=event['head'], updated=event['at'],
+                 factory=True, disposition={'review_state': 'APPROVED' if event['verdict'] == 'APPROVE' else 'CHANGES_REQUESTED'})
+
+    for value in result['items']:
+        value['observed_head_sha'] = final_sha
+        source_sha = value['source_head_sha']
+        if not raced and source_sha:
+            value['relevance'] = 'current_head' if source_sha == final_sha else 'historical'
+            if value['kind'] == 'review_comment':
+                outdated = value['disposition']['thread_outdated']
+                if outdated is True:
+                    value['relevance'] = 'historical'
+                elif outdated is None or value['location']['line'] is None:
+                    value['relevance'] = 'unknown'
+                    gap('threads', 'comment_position_unknown', 'Current comment position/outdated evidence is unavailable.')
+    for source in SOURCES:
+        result['coverage'][source]['reason'] = '; '.join(sorted(reasons[source]))[:1000] or None
+    result['items'].sort(key=lambda v: (v['evidence_id'], v['source_revision']))
+    result['errors'].sort(key=lambda e: (e['source'], e['code'], e['message']))
+    result['observation_id'] = digest({'repository_id': repository['id'], 'pr_id': result['pr']['id'], 'head_sha': final_sha,
+        'items': [(v['evidence_id'], v['source_revision']) for v in result['items']],
+        'coverage': {s: {k: result['coverage'][s][k] for k in ('status', 'truncated', 'reason')} for s in SOURCES}})
+    return result
diff --git a/tests/test_briefing.py b/tests/test_briefing.py
index 255563b..f1291bd 100644
--- a/tests/test_briefing.py
+++ b/tests/test_briefing.py
@@ -14,6 +14,174 @@ from factory import briefing, config, dashboard, dispatch
 
 
 class BriefingBoundaryTest(unittest.TestCase):
+    @staticmethod
+    def feedback(*, coverage: dict | None = None, items: list[dict] | None = None) -> dict:
+        default_coverage = {
+            source: {"status": "complete", "observed_at": "2026-09-10T22:00:00Z", "truncated": False, "reason": None}
+            for source in ("pr", "reviews", "threads", "checks")
+        }
+        default_item = {
+            "evidence_id": "github:github.com:R_1:PR_9:review:RV_4",
+            "kind": "review",
+            "source_id": "RV_4",
+            "source_url": "https://github.com/acme/widgets/pull/9#pullrequestreview-4",
+            "source_revision": "sha256:item",
+            "source_updated_at": "2026-09-10T21:59:00Z",
+            "review_id": "RV_4",
+            "thread_id": None,
+            "check_run_id": None,
+            "run_attempt": None,
+            "observed_head_sha": "abc123",
+            "source_head_sha": "abc123",
+            "relevance": "current_head",
+            "disposition": {
+                "review_state": "changes_requested",
+                "thread_resolved": None,
+                "thread_outdated": None,
+                "check_status": None,
+                "check_conclusion": None,
+            },
+            "author": {"login": "reviewer", "kind": "human"},
+            "body": "Please cover the error path.",
+            "truncated": False,
+            "location": {
+                "path": None,
+                "line": None,
+                "side": None,
+                "original_line": None,
+                "original_commit_sha": None,
+            },
+            "summary": None,
+        }
+        return {
+            "schema_version": 1,
+            "producer": {"name": "factory.pr-feedback", "revision": "8fba9c5"},
+            "observed_at": "2026-09-10T22:00:00Z",
+            "observation_id": "sha256:observation",
+            "repository": {"id": "R_1", "slug": "acme/widgets", "host": "github.com"},
+            "pr": {
+                "id": "PR_9",
+                "number": 9,
+                "url": "https://github.com/acme/widgets/pull/9",
+                "head_sha": "abc123",
+                "state": "open",
+                "draft": False,
+            },
+            "owner": {
+                "issue": {"id": "I_7", "number": 7, "url": "https://github.com/acme/widgets/issues/7"},
+                "relation": "factory_issue",
+                "evidence": ["branch agent/7", "Factory claim for issue #7"],
+            },
+            "coverage": coverage or default_coverage,
+            "items": [default_item] if items is None else items,
+            "errors": [],
+        }
+
+    @classmethod
+    def ticket(cls, feedback: object = ...) -> dict:
+        pr = {"number": 9, "state": "OPEN", "url": "https://github.com/acme/widgets/pull/9"}
+        if feedback is not ...:
+            pr["feedback"] = feedback
+        return {
+            "number": 7,
+            "title": "A decision",
+            "url": "https://github.com/acme/widgets/issues/7",
+            "body": "Original constraints",
+            "events": [{
+                "at": "2026-09-10T20:00:00Z",
+                "body": "Factory human decision: Preserve compatibility.",
+            }],
+            "pr": pr,
+        }
+
+    def test_feedback_sources_preserve_snapshot_item_and_containing_identities(self) -> None:
+        feedback = self.feedback()
+        with (
+            patch.object(briefing, "run_model", side_effect=AssertionError("model called")),
+            patch.object(subprocess, "run", side_effect=AssertionError("provider called")),
+        ):
+            sources = briefing.sources_for(config.Config(root=Path("/unused"), repo="acme/widgets"), self.ticket(feedback), [])
+
+        decision_index = next(i for i, source in enumerate(sources) if source["label"].startswith("Earlier human decision"))
+        evidence_index = next(i for i, source in enumerate(sources) if source["label"].startswith("PR feedback · review"))
+        evidence = sources[evidence_index]
+        payload = json.loads(evidence["text"])
+        self.assertGreater(evidence_index, decision_index)
+        self.assertEqual(evidence_index, len(sources) - 2)
+        self.assertEqual(payload["producer"], feedback["producer"])
+        self.assertEqual(payload["observed_at"], feedback["observed_at"])
+        self.assertEqual(payload["repository"], feedback["repository"])
+        self.assertEqual(payload["pr"], feedback["pr"])
+        self.assertEqual(payload["owner"], feedback["owner"])
+        self.assertEqual(payload["item"], feedback["items"][0])
+        self.assertEqual(evidence["url"], feedback["items"][0]["source_url"])
+        self.assertRegex(evidence["id"], r"^S\d+$")
+        coverage = sources[-1]
+        self.assertEqual(coverage["label"], "Evidence coverage")
+        self.assertIn('"reviews":{"status":"complete"', coverage["text"])
+        self.assertIn('"relation":"factory_issue"', coverage["text"])
+
+    def test_feedback_coverage_is_explicit_for_missing_unknown_and_partial_snapshots(self) -> None:
+        cfg = config.Config(root=Path("/unused"), repo="acme/widgets")
+        partial = {
+            source: {
+                "status": "partial" if source == "reviews" else "unavailable" if source == "threads" else "complete",
+                "observed_at": None,
+                "truncated": source == "reviews",
+                "reason": "head changed during collection" if source == "reviews" else "provider unavailable" if source == "threads" else None,
+            }
+            for source in ("pr", "reviews", "threads", "checks")
+        }
+        cases = [
+            (self.ticket(), "no schema-versioned feedback object"),
+            (self.ticket({"schema_version": 2, "items": [{"body": "DO NOT INTERPRET"}]}), "schema_version 2 is not supported"),
+            (self.ticket(self.feedback(coverage=partial)), '"status":"partial"'),
+        ]
+        for ticket, expected in cases:
+            with self.subTest(expected=expected):
+                sources = briefing.sources_for(cfg, ticket, [])
+                self.assertIn(expected, sources[-1]["text"])
+                self.assertTrue(sources[-1]["label"].startswith("Evidence coverage"))
+        self.assertNotIn("DO NOT INTERPRET", json.dumps(briefing.sources_for(cfg, cases[1][0], [])))
+        partial_text = briefing.sources_for(cfg, cases[2][0], [])[-1]["text"]
+        self.assertTrue(any(source["label"].startswith("PR feedback ·") for source in briefing.sources_for(cfg, cases[2][0], [])))
+        self.assertIn("head changed during collection", partial_text)
+        self.assertIn('"status":"unavailable"', partial_text)
+
+    def test_uncollected_history_is_cited_apart_from_unsupported_feedback(self) -> None:
+        from factory import feedback as producer
+
+        cfg = config.Config(root=Path("/unused"), repo="acme/widgets")
+        uncollected = self.feedback(
+            coverage={source: {"status": "unavailable", "observed_at": None, "truncated": False,
+                               "reason": f"{producer.NOT_COLLECTED}: {producer.NOT_COLLECTED_MESSAGE}"}
+                      for source in ("pr", "reviews", "threads", "checks")},
+            items=[],
+        )
+        uncollected["errors"] = [{"source": source, "code": producer.NOT_COLLECTED,
+                                  "message": producer.NOT_COLLECTED_MESSAGE}
+                                 for source in ("pr", "reviews", "threads", "checks")]
+        notice = briefing.sources_for(cfg, self.ticket(uncollected), [])[-1]["text"]
+        self.assertIn("were not collected", notice)
+        self.assertIn(producer.NOT_COLLECTED, notice)
+        self.assertNotIn("unsupported/unknown", notice)
+        missing = briefing.sources_for(cfg, self.ticket(), [])[-1]["text"]
+        self.assertIn("unsupported/unknown", missing)
+        self.assertNotIn("were not collected", missing)
+
+    def test_feedback_budget_omission_does_not_evict_human_constraints(self) -> None:
+        cfg = config.Config(root=Path("/unused"), repo="acme/widgets")
+        ticket = self.ticket(self.feedback())
+        with patch.object(briefing, "SOURCE_COUNT", 4):
+            sources = briefing.sources_for(cfg, ticket, [])
+
+        self.assertEqual(len(sources), 4)
+        decision = next(source for source in sources if source["label"].startswith("Earlier human decision"))
+        self.assertIn("Preserve compatibility.", decision["text"])
+        self.assertFalse(any(source["label"].startswith("PR feedback ·") for source in sources))
+        self.assertIn("1 PR feedback evidence item(s) omitted", sources[-1]["text"])
+        self.assertIn("additional evidence source(s) omitted", sources[-1]["text"])
+
     def test_rejects_untrusted_request_shapes_and_scope(self) -> None:
         invalid = [
             [],
diff --git a/tests/test_factory.py b/tests/test_factory.py
index 4e858d5..7a8a80a 100644
--- a/tests/test_factory.py
+++ b/tests/test_factory.py
@@ -2394,5 +2394,149 @@ class DispatchTest(unittest.TestCase):
             )
 
 
+class FeedbackSnapshotTest(unittest.TestCase):
+    def test_github_failure_preserves_provider_diagnostic(self):
+        from unittest import mock
+        from factory import dashboard
+
+        proc = subprocess.CompletedProcess([], 1, "", "gh: run gh auth login\n")
+        with mock.patch.object(dashboard.subprocess, "run", return_value=proc):
+            with self.assertRaisesRegex(RuntimeError, "gh: run gh auth login"):
+                dashboard.github(query="query { viewer { login } }")
+
+    def test_full_snapshot_preserves_legacy_contract_and_attaches_feedback(self):
+        from unittest import mock
+        from factory import dashboard, dispatch
+        from tests.test_feedback import Provider, REPO, PR, ISSUE, H, AT, factory_events
+
+        with tempfile.TemporaryDirectory() as d:
+            repo = make_repo(Path(d), '[repo]\nslug = "example/project"\n')
+            state = repo / ".factory"
+            state.mkdir()
+            (state / "events.jsonl").write_text("\n".join(map(json.dumps, factory_events())) + "\n")
+            issue = {**ISSUE, "title": "Feedback contract", "state": "OPEN", "body": "Keep human constraint",
+                     "createdAt": AT, "updatedAt": AT, "closedAt": None,
+                     "labels": {"nodes": [{"name": "ready-for-human"}]}, "assignees": {"nodes": []}}
+            raw_pr = {**PR, "title": "Feedback", "headRefName": "agent/79", "headRefOid": H,
+                      "state": "OPEN", "isDraft": False, "createdAt": AT, "closedAt": None, "mergedAt": None,
+                      "additions": 1, "deletions": 0, "changedFiles": 1, "body": "## Gate report\n- test: PASS",
+                      "reviewDecision": "CHANGES_REQUESTED", "labels": {"nodes": []},
+                      "comments": {"nodes": [{"createdAt": AT, "body": "VERDICT: APPROVE", "url": PR["url"] + "#issuecomment-1"}]},
+                      "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE", "contexts": {"nodes": [
+                          {"__typename": "CheckRun", "name": "CI", "status": "COMPLETED", "conclusion": "FAILURE"}]}}}}]}}
+            provider = Provider()
+            def read(**kwargs):
+                if kwargs:
+                    return provider(**kwargs)
+                return {"repository": {"id": REPO["id"], "issues": {"nodes": [issue, {
+                    **issue, "number": 81, "id": "I_81", "url": issue["url"].replace("79", "81")}]},
+                    "pullRequests": {"nodes": [raw_pr]}}}
+            with mock.patch.dict(dashboard.__dict__), mock.patch.dict(dispatch.__dict__):
+                dashboard.configure(config.load(repo))
+                with mock.patch.object(dashboard, "github", side_effect=read), \
+                     mock.patch.object(dashboard, "dispatcher", return_value={}), \
+                     mock.patch.object(dashboard, "upstream_state", return_value={}), \
+                     mock.patch.object(dashboard, "triage_llm_online", return_value=False):
+                    snapshot = dashboard.snapshot()
+                    provider.heads = [H, H]
+                    torn = "discarded prefix\n" + "\n".join(map(json.dumps, factory_events()))
+                    with mock.patch.object(dashboard.briefing, "bounded_file", return_value=(torn, True)):
+                        partial = dashboard.snapshot()
+                    partial_feedback = next(t for t in partial["tickets"] if t["number"] == 79)["pr"]["feedback"]
+                    self.assertNotIn("factory_review", {i["kind"] for i in partial_feedback["items"]})
+                    self.assertEqual(partial_feedback["coverage"]["reviews"]["status"], "partial")
+                    package = repo / ".venv" / "factory"
+                    package.mkdir(parents=True)
+                    source = package / "feedback.py"
+                    source.write_text("# installed producer\n")
+                    (repo / ".gitignore").write_text(".factory/\n.venv/\n")
+                    git(repo, "add", ".gitignore")
+                    git(repo, "commit", "-m", "Ignore installed packages")
+                    with mock.patch.object(dashboard, "__file__", str(package / "dashboard.py")):
+                        for mode in ("ignored", "tracked", "dirty"):
+                            if mode == "tracked":
+                                git(repo, "add", "-f", str(source))
+                                git(repo, "commit", "-m", "Track producer source")
+                            elif mode == "dirty":
+                                source.write_text("# modified producer\n")
+                            provider.heads = [H, H]
+                            observed = dashboard.snapshot()
+                            produced = next(t for t in observed["tickets"] if t["number"] == 79)["pr"]["feedback"]
+                            with self.subTest(package=mode):
+                                expected = git(repo, "rev-parse", "HEAD") if mode == "tracked" else None
+                                self.assertEqual(produced["producer"]["revision"], expected)
+            ticket = next(t for t in snapshot["tickets"] if t["number"] == 79)
+            pr = ticket["pr"]
+            self.assertEqual(pr["gate_text"], "- test: PASS")
+            self.assertEqual(pr["review_decision"], "CHANGES_REQUESTED")
+            self.assertEqual(pr["checks"]["list"], [{"name": "CI", "result": "FAILURE"}])
+            self.assertEqual(pr["comments"][0]["body"], "VERDICT: APPROVE")
+            self.assertEqual(pr["verdicts"][0]["verdict"], "APPROVE")
+            self.assertEqual({i["kind"] for i in pr["feedback"]["items"]},
+                             {"review", "review_comment", "check_run", "factory_review"})
+            self.assertIsNone(next(t for t in snapshot["tickets"] if t["number"] == 81)["pr"])
+
+    def test_historical_prs_add_no_feedback_reads_but_keep_schema_1(self):
+        from unittest import mock
+        from factory import dashboard, dispatch, feedback
+        from tests.test_feedback import Provider, REPO, PR, ISSUE, H, AT
+
+        def issue_of(number):
+            return {"id": f"I_{number}", "number": number, "title": "Historical",
+                    "url": ISSUE["url"].replace("79", str(number)), "state": "OPEN", "body": "",
+                    "createdAt": AT, "updatedAt": AT, "closedAt": None,
+                    "labels": {"nodes": []}, "assignees": {"nodes": []}}
+
+        def pr_of(number, state):
+            return {"id": f"PR_{number}", "number": number, "url": PR["url"].replace("80", str(number)),
+                    "title": "Historical", "headRefName": f"agent/{number}", "headRefOid": H,
+                    "state": state, "isDraft": False, "createdAt": AT, "closedAt": None,
+                    "mergedAt": AT if state == "MERGED" else None, "additions": 1, "deletions": 0,
+                    "changedFiles": 1, "body": "", "reviewDecision": None, "labels": {"nodes": []},
+                    "comments": {"nodes": []}, "commits": {"nodes": []}}
+
+        with tempfile.TemporaryDirectory() as d:
+            repo = make_repo(Path(d), '[repo]\nslug = "example/project"\n')
+            (repo / ".factory").mkdir()
+            issues = [issue_of(79), issue_of(81), issue_of(82)]
+            raw = [{**pr_of(79, "OPEN"), "id": PR["id"], "url": PR["url"]},
+                   pr_of(81, "MERGED"), pr_of(82, "CLOSED")]
+            provider = Provider()
+            def read(**kwargs):
+                if kwargs:
+                    return provider(**kwargs)
+                return {"repository": {"id": REPO["id"], "issues": {"nodes": issues},
+                                       "pullRequests": {"nodes": raw}}}
+            with mock.patch.dict(dashboard.__dict__), mock.patch.dict(dispatch.__dict__):
+                dashboard.configure(config.load(repo))
+                with mock.patch.object(dashboard, "github", side_effect=read), \
+                     mock.patch.object(dashboard, "dispatcher", return_value={}), \
+                     mock.patch.object(dashboard, "upstream_state", return_value={}), \
+                     mock.patch.object(dashboard, "triage_llm_online", return_value=False):
+                    mixed = dashboard.snapshot()
+                    with_history = len(provider.calls)
+                    provider.calls.clear()
+                    provider.heads = [H, H]
+                    raw[:] = raw[:1]
+                    issues[:] = issues[:1]
+                    only_open = dashboard.snapshot()
+            self.assertEqual(with_history, len(provider.calls))
+            open_feedback = next(t for t in mixed["tickets"] if t["number"] == 79)["pr"]["feedback"]
+            self.assertEqual(open_feedback["pr"]["state"], "open")
+            self.assertTrue(open_feedback["items"])
+            self.assertEqual(open_feedback["items"],
+                             next(t for t in only_open["tickets"] if t["number"] == 79)["pr"]["feedback"]["items"])
+            for number, state in ((81, "merged"), (82, "closed")):
+                observed = next(t for t in mixed["tickets"] if t["number"] == number)["pr"]["feedback"]
+                with self.subTest(pr=number):
+                    self.assertEqual(observed["schema_version"], 1)
+                    self.assertEqual(observed["pr"]["state"], state)
+                    self.assertEqual(observed["pr"]["id"], f"PR_{number}")
+                    self.assertIsNone(observed["pr"]["head_sha"])
+                    self.assertEqual(observed["items"], [])
+                    self.assertEqual({c["status"] for c in observed["coverage"].values()}, {"unavailable"})
+                    self.assertEqual({e["code"] for e in observed["errors"]}, {feedback.NOT_COLLECTED})
+
+
 if __name__ == "__main__":
     unittest.main()
diff --git a/tests/test_feedback.py b/tests/test_feedback.py
new file mode 100644
index 0000000..82f89d0
--- /dev/null
+++ b/tests/test_feedback.py
@@ -0,0 +1,334 @@
+"""Behavioral contract for the shared read-only PR feedback collector."""
+import copy
+import unittest
+
+from factory import feedback
+
+H = "a" * 40
+OLD = "b" * 40
+AT = "2026-09-10T12:00:00Z"
+REPO = {"id": "R_1", "slug": "example/project", "host": "github.com"}
+ISSUE = {"id": "I_79", "number": 79, "url": "https://github.com/example/project/issues/79"}
+PR = {"id": "PR_80", "number": 80, "url": "https://github.com/example/project/pull/80"}
+
+
+class Provider:
+    def __init__(self):
+        self.heads = [H, H]
+        self.calls = []
+        self.fail = None
+        self.reviews = [{"id": "RV_1", "url": PR["url"] + "#pullrequestreview-1", "commit": {"oid": H},
+                         "state": "CHANGES_REQUESTED", "body": "Fix boundary", "updatedAt": AT,
+                         "author": {"login": "shared", "__typename": "User"}}]
+        self.comments = [{"id": "RC_1", "url": PR["url"] + "#discussion_r1", "body": "<img onerror=alert(1)>",
+                          "updatedAt": AT, "commit": {"oid": H}, "originalCommit": {"oid": OLD},
+                          "path": "factory/example.py", "line": 7, "originalLine": 6, "diffSide": "RIGHT",
+                          "author": {"login": "reader", "__typename": "User"}, "pullRequestReview": {"id": "RV_1"}}]
+        self.thread = {"id": "T_1", "isResolved": False, "isOutdated": False,
+                       "comments": {"nodes": self.comments, "pageInfo": {"hasNextPage": False}}}
+        self.checks = [{"node_id": "CR_1", "id": 1, "html_url": PR["url"] + "/checks?check_run_id=1",
+                        "head_sha": H, "name": "CI", "status": "completed", "conclusion": "failure", "run_attempt": 1}]
+        self.statuses = []
+
+    def __call__(self, *, endpoint=None, query=None, variables=None, timeout=None):
+        self.calls.append((endpoint, query, timeout))
+        source = ({feedback.HEAD_QUERY: 'pr', feedback.THREAD_QUERY: 'threads',
+                   feedback.REVIEW_QUERY: 'reviews'}.get(query, 'checks'))
+        if self.fail == source:
+            raise RuntimeError('token=SECRET provider config')
+        if source == 'pr':
+            return {"repository": {"id": "R_1", "pullRequest": {**PR, "headRefOid": self.heads.pop(0), "state": "OPEN", "isDraft": False,
+                    "closingIssuesReferences": {"nodes": [ISSUE], "pageInfo": {"hasNextPage": False}}}}}
+        if source == 'threads':
+            return {"repository": {"pullRequest": {"reviewThreads": {"nodes": [self.thread], "pageInfo": {"hasNextPage": False}}}}}
+        if source == 'reviews':
+            if not isinstance(self.reviews, list):
+                return self.reviews
+            start = int((variables or {}).get('cursor') or 0)
+            nodes = self.reviews[start:start + 50]
+            more = start + len(nodes) < len(self.reviews)
+            return {"repository": {"pullRequest": {"reviews": {"nodes": nodes, "pageInfo": {
+                "hasNextPage": more, "endCursor": str(start + len(nodes)) if more else None}}}}}
+        if '/check-runs?' in endpoint:
+            return {"total_count": len(self.checks), "check_runs": self.checks}
+        return self.statuses
+
+
+def collect(provider=None, **kwargs):
+    return feedback.collect(provider or Provider(), repository=REPO, pr=PR, issue=ISSUE,
+                            events=[{"event": "claimed", "ticket": 79}], observed_at=AT, **kwargs)
+
+
+class FeedbackTests(unittest.TestCase):
+    def test_simultaneous_native_sources_and_explicit_unknown_attribution(self):
+        result = collect()
+        self.assertEqual(result['schema_version'], 1)
+        self.assertEqual({i['kind'] for i in result['items']}, {'review', 'review_comment', 'check_run'})
+        self.assertEqual(result['owner']['relation'], 'factory_issue')
+        self.assertTrue(all(c['status'] == 'complete' for c in result['coverage'].values()))
+        for item in result['items']:
+            self.assertTrue(item['source_url'].startswith(PR['url']))
+            self.assertEqual(item['observed_head_sha'], H)
+            self.assertEqual(item['source_head_sha'], H)
+            self.assertEqual(item['relevance'], 'current_head')
+            self.assertIn('run_attempt', item)
+            self.assertIn('summary', item)
+        comment = next(i for i in result['items'] if i['kind'] == 'review_comment')
+        self.assertEqual(comment['location']['original_commit_sha'], OLD)
+        self.assertEqual(comment['location']['line'], 7)
+        self.assertFalse(comment['disposition']['thread_resolved'])
+        review = next(i for i in result['items'] if i['kind'] == 'review')
+        self.assertEqual(review['author']['kind'], 'unknown')
+        self.assertIsNone(review['check_run_id'])
+        self.assertEqual(review['disposition']['review_state'], 'CHANGES_REQUESTED')
+
+    def test_factory_provenance_is_not_rendered_verdict_text(self):
+        result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE,
+                                  events=factory_events(), observed_at=AT)
+        self.assertEqual(len(result['items']), 4)
+        factory = next(i for i in result['items'] if i['kind'] == 'factory_review')
+        self.assertEqual(factory['source_id'], 'result-79')
+        self.assertEqual(factory['author']['kind'], 'factory_reviewer')
+        self.assertEqual(factory['source_head_sha'], H)
+        self.assertEqual(factory['body'], '')
+        self.assertIsNone(factory['source_url'])
+        for change in ({'returncode': 1}, {'parsed': False}, {'actual_head': OLD}):
+            events = factory_events()
+            events[2].update(change)
+            result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE,
+                                      events=events + [{'body': 'VERDICT: APPROVE'}])
+            self.assertNotIn('factory_review', {i['kind'] for i in result['items']})
+
+    def test_equivalent_polls_and_semantic_revisions(self):
+        provider = Provider()
+        second = {**provider.reviews[0], 'id': 'RV_2', 'commit': {'oid': OLD}}
+        provider.reviews.append(second)
+        first = collect(provider)
+        provider.heads = [H, H]
+        provider.reviews.reverse()
+        other = feedback.collect(provider, repository=REPO, pr=PR, issue=ISSUE,
+                                 events=[{'event': 'claimed', 'ticket': 79}], observed_at='2026-09-11T00:00:00Z')
+        self.assertEqual(first['observation_id'], other['observation_id'])
+        self.assertEqual(first['items'], other['items'])
+        baseline = {i['source_id']: i for i in first['items']}
+        for target, change, changed_id in [
+            ('review', {'body': 'edited'}, 'RV_1'),
+            ('review', {'state': 'DISMISSED'}, 'RV_1'),
+            ('comment', {'line': 9}, 'RC_1'),
+            ('thread', {'isResolved': True}, 'RC_1'),
+            ('check', {'conclusion': 'success'}, 'CR_1'),
+            ('check', {'run_attempt': 2}, 'CR_1'),
+        ]:
+            p = Provider()
+            p.reviews.append(copy.deepcopy(second))
+            row = {'review': p.reviews[0], 'comment': p.comments[0], 'thread': p.thread, 'check': p.checks[0]}[target]
+            row.update(change)
+            revised = collect(p)
+            self.assertNotEqual(first['observation_id'], revised['observation_id'])
+            for i in revised['items']:
+                self.assertEqual(i['evidence_id'], baseline[i['source_id']]['evidence_id'])
+                self.assertEqual(i['source_revision'] == baseline[i['source_id']]['source_revision'], i['source_id'] != changed_id)
+        p = Provider()
+        p.checks[0].update(node_id='CR_2', id=2)
+        self.assertNotEqual(next(i for i in collect(p)['items'] if i['kind'] == 'check_run')['evidence_id'], baseline['CR_1']['evidence_id'])
+
+    def test_head_changes_do_not_revise_old_sources(self):
+        baseline = collect()
+        p = Provider()
+        p.heads = [OLD, OLD]
+        advanced = collect(p)
+        self.assertEqual([i['source_revision'] for i in baseline['items']], [i['source_revision'] for i in advanced['items']])
+        self.assertTrue(all(i['relevance'] == 'historical' for i in advanced['items']))
+        p = Provider()
+        p.heads = [H, OLD]
+        race = collect(p)
+        self.assertTrue(all(i['relevance'] == 'unknown' for i in race['items']))
+        self.assertTrue(all(i['observed_head_sha'] == OLD for i in race['items']))
+        self.assertTrue(all(c['status'] == 'partial' for c in race['coverage'].values()))
+        self.assertIn(H, race['coverage']['pr']['reason'])
+        self.assertIn(OLD, race['coverage']['pr']['reason'])
+        p = Provider()
+        p.heads = [H, None]
+        missing = collect(p)
+        self.assertIsNone(missing['pr']['head_sha'])
+        self.assertTrue(all(i['relevance'] == 'unknown' for i in missing['items']))
+        self.assertEqual(len(missing['items']), 3)
+
+    def test_source_failures_do_not_erase_independent_evidence(self):
+        for source in ('reviews', 'threads', 'checks'):
+            p = Provider()
+            p.fail = source
+            result = collect(p)
+            self.assertEqual(result['coverage'][source]['status'], 'unavailable')
+            self.assertTrue(result['items'])
+            self.assertNotIn('SECRET', str(result))
+        p = Provider()
+        p.reviews = []
+        p.comments.clear()
+        p.checks.clear()
+        result = collect(p)
+        self.assertEqual(result['items'], [])
+        self.assertTrue(all(c['status'] == 'complete' for c in result['coverage'].values()))
+
+    def test_unknown_source_head_and_outdated_thread_are_not_current(self):
+        p = Provider()
+        p.reviews[0]['commit'] = None
+        p.thread['isOutdated'] = True
+        p.thread['isResolved'] = True
+        result = collect(p)
+        by_kind = {i['kind']: i for i in result['items']}
+        self.assertEqual(by_kind['review']['relevance'], 'unknown')
+        self.assertEqual(by_kind['review_comment']['relevance'], 'historical')
+        self.assertTrue(by_kind['review_comment']['disposition']['thread_resolved'])
+        self.assertEqual(result['coverage']['reviews']['status'], 'partial')
+        for status, conclusion in [('queued', None), ('in_progress', None), ('completed', 'cancelled'),
+                                   ('completed', 'success'), ('completed', 'failure'),
+                                   ('completed', 'neutral'), ('completed', 'skipped')]:
+            p = Provider()
+            p.checks[0].update(status=status, conclusion=conclusion)
+            check = next(i for i in collect(p)['items'] if i['kind'] == 'check_run')
+            self.assertEqual(check['disposition']['check_status'], status)
+            self.assertEqual(check['disposition']['check_conclusion'], conclusion)
+
+    def test_body_count_pagination_timeout_and_malformed_bounds(self):
+        p = Provider()
+        p.reviews[0].update(body='é' * 20000, updatedAt=None)
+        result = collect(p)
+        review = next(i for i in result['items'] if i['kind'] == 'review')
+        self.assertEqual(len(review['body'].encode()), 20000)
+        self.assertTrue(review['truncated'])
+        self.assertIn('change_detection_incomplete', result['coverage']['reviews']['reason'])
+        p = Provider()
+        p.reviews = [{**p.reviews[0], 'id': f'RV_{n}'} for n in range(120)]
+        result = collect(p)
+        self.assertEqual(len([i for i in result['items'] if i['kind'] == 'review']), 100)
+        self.assertEqual(sum(c[1] == feedback.REVIEW_QUERY for c in p.calls), 2)
+        self.assertTrue(result['coverage']['reviews']['truncated'])
+        p = Provider()
+        p.thread['comments']['pageInfo']['hasNextPage'] = True
+        self.assertEqual(collect(p)['coverage']['threads']['status'], 'partial')
+        p = Provider()
+        p.reviews = {'bad': 'response'}
+        self.assertEqual(collect(p)['coverage']['reviews']['status'], 'unavailable')
+        p = Provider()
+        def timed(**kwargs):
+            if kwargs.get('query') == feedback.REVIEW_QUERY:
+                raise TimeoutError('secret')
+            return p(**kwargs)
+        result = collect(timed)
+        self.assertEqual(result['coverage']['reviews']['status'], 'unavailable')
+        self.assertTrue(any(i['kind'] == 'check_run' for i in result['items']))
+        self.assertTrue(any(e['code'] == 'timeout' for e in result['errors']))
+        self.assertLessEqual(len(result['errors']), 32)
+
+    def test_missing_conflicting_identity_and_ownership(self):
+        p = Provider()
+        p.reviews.append({**p.reviews[0], 'body': 'conflict'})
+        result = collect(p)
+        self.assertNotIn('review', {i['kind'] for i in result['items']})
+        p = Provider()
+        p.checks[0]['node_id'] = None
+        self.assertNotIn('check_run', {i['kind'] for i in collect(p)['items']})
+        result = feedback.collect(Provider(), repository=REPO, pr=PR, issue=ISSUE)
+        self.assertEqual(result['owner']['relation'], 'unverified')
+        self.assertIsNone(result['owner']['issue'])
+
+    def test_native_status_survives_failed_check_run_read(self):
+        p = Provider()
+        p.statuses = [{'node_id': 'CS_1', 'id': 12, 'target_url': 'https://ci.example/status/12',
+                       'state': 'pending', 'context': 'deployment', 'updated_at': AT,
+                       'creator': {'login': 'ci', 'type': 'Bot'}}]
+        def read(**kwargs):
+            if '/check-runs?' in (kwargs.get('endpoint') or ''):
+                raise RuntimeError('credentials')
+            return p(**kwargs)
+        result = collect(read)
+        status = next(i for i in result['items'] if i['kind'] == 'commit_status')
+        self.assertEqual(status['source_head_sha'], H)
+        self.assertEqual(status['author']['kind'], 'bot')
+        self.assertEqual(status['source_url'], 'https://ci.example/status/12')
+        self.assertEqual(status['disposition']['check_status'], 'pending')
+        self.assertEqual(result['coverage']['checks']['status'], 'partial')
+
+    def test_final_head_failure_retains_details_without_promoting_them(self):
+        p = Provider()
+        def read(**kwargs):
+            if kwargs.get('query') == feedback.HEAD_QUERY and len(p.heads) == 1:
+                raise RuntimeError('head unavailable')
+            return p(**kwargs)
+        result = collect(read)
+        self.assertEqual(len(result['items']), 3)
+        self.assertIsNone(result['pr']['head_sha'])
+        self.assertTrue(all(i['observed_head_sha'] is None and i['relevance'] == 'unknown' for i in result['items']))
+
+
+    def test_review_update_time_is_retained_and_signals_change_detection(self):
+        review = next(i for i in collect()['items'] if i['kind'] == 'review')
+        self.assertEqual(review['source_updated_at'], AT)
+        p = Provider()
+        p.reviews[0].update(body='Fix boundary, again', updatedAt='2026-09-11T09:00:00Z')
+        edited = collect(p)
+        revised = next(i for i in edited['items'] if i['kind'] == 'review')
+        self.assertEqual(revised['evidence_id'], review['evidence_id'])
+        self.assertEqual(revised['source_updated_at'], '2026-09-11T09:00:00Z')
+        self.assertNotEqual(revised['source_revision'], review['source_revision'])
+        p = Provider()
+        p.reviews[0]['body'] = 'x' * 20_001
+        signalled = collect(p)
+        self.assertTrue(next(i for i in signalled['items'] if i['kind'] == 'review')['truncated'])
+        self.assertNotIn('change_detection_incomplete', signalled['coverage']['reviews']['reason'])
+        p = Provider()
+        p.reviews[0].update(body='x' * 20_001, updatedAt=None)
+        self.assertIn('change_detection_incomplete', collect(p)['coverage']['reviews']['reason'])
+
+    def test_uncollected_details_are_schema_1_and_read_nothing(self):
+        p = Provider()
+        result = feedback.collect(p, repository=REPO, pr={**PR, 'state': 'MERGED', 'draft': False},
+                                  issue=ISSUE, events=factory_events(), observed_at=AT,
+                                  collect_details=False)
+        self.assertEqual(p.calls, [])
+        self.assertEqual(result['schema_version'], 1)
+        self.assertEqual(result['pr']['id'], PR['id'])
+        self.assertEqual(result['pr']['state'], 'merged')
+        self.assertIs(result['pr']['draft'], False)
+        self.assertIsNone(result['pr']['head_sha'])
+        self.assertEqual(result['items'], [])
+        self.assertTrue(result['observation_id'].startswith('sha256:'))
+        for source in feedback.SOURCES:
+            coverage = result['coverage'][source]
+            self.assertEqual(coverage['status'], 'unavailable')
+            self.assertIsNone(coverage['observed_at'])
+            self.assertIn(feedback.NOT_COLLECTED, coverage['reason'])
+        self.assertEqual({e['code'] for e in result['errors']}, {feedback.NOT_COLLECTED})
+        self.assertEqual({e['source'] for e in result['errors']}, set(feedback.SOURCES))
+        self.assertEqual(result['owner']['relation'], 'unverified')
+        self.assertTrue(any('claimed' in entry for entry in result['owner']['evidence']))
+        closed = feedback.collect(Provider(), repository=REPO, pr={**PR, 'state': 'nonsense'},
+                                  issue=ISSUE, collect_details=False)
+        self.assertEqual(closed['pr']['state'], 'unknown')
+        self.assertIsNone(closed['pr']['draft'])
+
+    def test_malformed_pr_metadata_preserves_independent_sources(self):
+        p = Provider()
+        def read(**kwargs):
+            value = p(**kwargs)
+            if kwargs.get('query') == feedback.HEAD_QUERY:
+                value['repository']['pullRequest'].update(state=42, closingIssuesReferences=42)
+            return value
+        result = collect(read)
+        self.assertEqual(result['pr']['state'], 'unknown')
+        self.assertEqual(result['owner']['relation'], 'unverified')
+        self.assertEqual(len(result['items']), 3)
+        self.assertEqual(result['coverage']['pr']['status'], 'partial')
+
+def factory_events():
+    rows = [{'event': 'claimed', 'ticket': 79}]
+    for sequence, kind in enumerate(('enter', 'result', 'exit'), 1):
+        rows.append({'event': 'lifecycle', 'schema_version': 1, 'event_id': f'{kind}-79',
+                     'execution_id': 'review-79', 'root_execution_id': 'root-79',
+                     'dispatcher_run_id': None, 'parent_execution_id': None, 'ticket': 79,
+                     'attempt': None, 'review_round': None, 'stage': 'review', 'kind': kind,
+                     'sequence': sequence, 'at': AT, 'outcome': 'approved' if kind == 'exit' else None,
+                     'reason': None, 'process': None, 'locks': [], 'head': H, 'actual_head': H,
+                     'returncode': 0, 'parsed': True, 'verdict': 'APPROVE'})
+    return rows
```

### README.md at this head (documented conventions)

```markdown
# factory

A label-driven autonomous ticket pipeline for any GitHub repository. Issues
labelled `ready-for-agent` are claimed by a coding agent in a git worktree,
gated by your own deterministic checks, reviewed by a second model, opened as a
PR, and landed on `main` once the gate, the reviewer, GitHub CI, and freshness
against `main` all agree. Anything the pipeline cannot resolve is handed back
with a `ready-for-human` label and the evidence attached.

It runs on your machine, on a systemd timer, with the agent CLIs you already
have. State is GitHub (labels, comments, PRs) plus a gitignored `.factory/`
directory; the dispatcher itself is stateless and safe to re-run.

```
 needs-triage ──factory triage──▶ ready-for-agent ──factory dispatch──▶ agent/<n> PR ──merge stage──▶ main
                    │                                     │                  │
                    ▼                                     ▼                  ▼
                needs-info                          ready-for-human      factory-approved
```

## Install

Two ways in; both end with the same `factory` CLI on your machine.

**Have your coding agent do it:** install the skill and ask the agent to set up
the factory in your repo. The skill installs the CLI if it is missing, writes
the config from your CI, and runs the doctor.

```sh
npx skills add mikeroysoft/factory
```

**Or by hand.** Linux, Python ≥ 3.11, `git`, `gh` (authenticated with push access), and:

- a **worker** agent CLI that accepts a prompt file and works in a directory
  (default `omp -p`; `droid`, `codex exec`, `claude -p`, … work the same way)
- optionally, a **manager** agent CLI that accepts a prompt file and works in a directory
- a **reviewer** CLI that answers an inline prompt on stdout (default `omp -p --model anthropic/claude-fable-5-1`; `codex exec` works the same way)
- optionally, an OpenAI-compatible local model for triage (Ollama, vLLM,
  llama.cpp, LM Studio)

```sh
uv tool install git+https://github.com/mikeroySoft/factory@stable   # stable: released channel
uv tool install git+https://github.com/mikeroySoft/factory@main     # latest: tip of main
uv tool install git+https://github.com/mikeroySoft/factory@v0.3.0   # a specific release
```

`pipx install` and `pip install --user` take the same URLs. The repository's
default branch is `main`, so a bare URL installs development code. Select
`@stable` explicitly for the released channel or a version tag for a fixed release;
see [CHANGELOG.md](CHANGELOG.md). No Python dependencies.

## Set up a repository

```sh
cd your-repo
factory init            # .factory.toml, .gitignore, issue template, labels
$EDITOR .factory.toml   # put your real test/lint commands in [[gate.check]]
git add .factory.toml .gitignore .github/ISSUE_TEMPLATE/agent_task.md && git commit
factory doctor          # tools, auth, remotes, model endpoint
factory install --dashboard   # systemd user timer every 10 min + dashboard on :8765
```

Generated triage, dispatch and dashboard services use `python -P -m factory`:
Python does not prepend the repository working directory to its module search
path, so an older checkout cannot shadow the installed Factory package.
The interpreter must already have Factory installed; explicit `PYTHONPATH`
overrides remain operator-controlled. This source change does not rewrite
existing units: review their import paths before any separately authorized
unit update or service reload.

Then file an issue with the **Agent task** template (Scope / Touches / Exit
gate / Out of scope). It gets `needs-triage`; the next pass triages it; if it is
fully specified it becomes `ready-for-agent` and is picked up.

For a fork that tracks an upstream, set `[repo].upstream = "upstream"` and the
dispatcher merges new upstream commits into your `main` (gated) before each
merge stage.

## Commands

| Command | What one invocation does |
|---|---|
| `factory triage` | Labels every `needs-triage` issue via the local model: `ready-for-agent` (with an agent brief), `needs-info` (with the question), `ready-for-human`, or a `wontfix` proposal comment. `--dry-run`, `--issue N`, `--replay a,b,c`. |
| `factory dispatch` | One stateless pass: upstream sync → merge stage (at most one PR) → manager → claim up to `max_active` tickets → worker → gate → PR → review → up to `review_rounds` bounces. `--ticket N` forces one issue; `--dry-run` prints the plan. |
| `factory manage` | First recommends directions for `needs-review` PRs, then `needs-viability` issues; then resolves untouched `ready-for-human` escalation packets within `[manager].rounds`. Disabled unless `manager.command` is configured. `--dry-run` lists eligible requests without inference or writes. |
| `factory gate` | Runs the deterministic gate in the current worktree and writes a Markdown report. Workers run it themselves; the dispatcher re-runs it as the evidence of record. |
| `factory stats` | Ticket table: attempts, review rounds, hours to merge, escalation count, resolver attribution, minutes in `ready-for-human`, and re-queues. Reads GitHub plus existing `events.jsonl`. `--by-worker` reads only events and shows every configured worker label: first-attempt gate pass rate, all attempts (including review bounces), and known cost. Attribution uses claim labels with current worker precedence; unclaimed attempts are excluded, missing rates/cost are `n/a`. The dashboard Ops view shows the same worker metrics. `--json`. |
| `factory learn` | Reads the last N finished tickets' event trail, failing-attempt log tails, reviewer findings, and escalation reasons; asks the local model for ≤10 repo-specific lessons; writes `.factory-lessons.md` (you commit it). Every worker prompt carries it. `--dry-run`, `--last N`. |
| `factory dashboard` | Local ops UI: tickets by stage, authoritative in-flight phase when known, gate reports, worker logs, journal heartbeat, upstream drift, and an action list with one-click answers. `--json` prints the existing snapshot, including independent executions and local interruption reconciliation. `--host 0.0.0.0` exposes it (and its mutating `/api/act`) to your network. |
| `factory dashboard --runtime-json` | One bounded schema 1 runtime observation using only local read-only evidence; no GitHub, model probe, journal append, lock acquisition, or state creation. Partial source failures remain structured JSON. See [runtime contract](#bounded-runtime-json-schema-1). |
| `factory evidence --root /path/to/main-checkout` | One explicit-repository schema 1 JSON read: compact cases, selected evidence, workflow/file/PR/CI investigations, or capabilities. Read-only GitHub GETs and F03 local evidence; no model, action execution, or state writes. See [evidence contract](#bounded-project-evidence-json-schema-1). |
| `factory doctor` / `init` / `install` | Onboarding, above. |

Every command reads `.factory.toml` from the main checkout, even when run
inside one of its worktrees.

Worker and manager commands receive `{prompt}` as a prompt-file path and `{cwd}`
as the worktree (or repository root); reviewers receive `{prompt}` as inline
text. OMP loads a prompt file only when it is prefixed with `@`, so use
`@{prompt}`: bare `omp {prompt}` passes the path as prompt text. Factory rejects
that bare argument for manager commands, not worker commands.
Keep `{cwd}` in worker and manager commands, and configure the manager CLI in
read-only/no-tools mode: the prompt prohibits file edits, but an arbitrary
configured executable is trusted, not sandboxed by factory. The manager reads
the packet, `.factory-lessons.md`, and optional `.factory/manager/notes.md`.
Its last `DECISION:` header
selects `RETRY`, `REWRITE`, `SPLIT`, `ROUTE`, `FIX`, or `HUMAN`, followed by the decision
body. RETRY/HUMAN use plain text; REWRITE uses the complete replacement issue body.
SPLIT uses a JSON array of `{title, body, blocked_by}` children, with `blocked_by`
containing 1-based indexes of earlier children. ROUTE uses `{add, remove, guidance}`,
with label arrays restricted to configured worker labels (not `default`).
Workers can use either the legacy argv array or a `[workers.<label>]` table with
`command = [...]` and optional `when = "..."`. The prompt lists routable labels
with their `when` rules; neither ROUTE nor FIX accepts unlisted labels.
FIX uses `{"worker":"ci-fix","guidance":"..."}` to run exactly one selected worker
round in the kept `agent/<n>` worktree for its open PR, ignoring other ticket
labels and refusing a kept head that no longer matches the remote PR. It passes
guidance and the escalation packet to the worker, re-gates, pushes only on a
gate PASS bound to the resulting commit, and re-reviews that
same commit. Only a zero-exit, well-formed fresh APPROVE whose remote PR head
still matches restores `factory-approved`; failure stays with the human. FIX
does not merge or requeue the issue.
The template includes opt-in `ci-fix` and `conflict` profiles for a
human to apply in host config; the manager cannot add profiles or edit config.
Code validates the output, records a `manage` event before GitHub mutations, and
leaves malformed decisions with a prefixed HUMAN diagnosis. Split children enter
`needs-triage`; the parent keeps `ready-for-human` with child blocker lines.
A trailing fenced `notes` block replaces `.factory/manager/notes.md`
(gitignored, never committed, carried into every later manager prompt). The block
is optional; code refuses an empty or over-16 KB replacement, keeps the existing
file, and records the outcome in the `manage` event's `notes` field
(`written`, `empty_rejected`, `oversize_rejected`, or null when no block was sent).
Manager executions and ticket-lock waits use the lifecycle journal. If a GitHub
mutation fails, the execution records a terminal failure and the ticket remains
with the human; other tickets can proceed. The consumed round is not replayed,
because a partial rewrite or split may already have changed GitHub.

### Opt-in viability recommendations

Viability asks **should we pursue this direction?**, before spending effort on
specification or code review. With `[manager].command` configured, opt in explicitly:

```bash
factory init --labels-only  # provisions the vocabulary on an existing installation
gh pr edit N --add-label needs-review
gh issue edit N --add-label needs-viability
factory manage --dry-run
factory manage
```

The manager handles `needs-review` PRs **before** `needs-viability` issues, both
before escalation management. The existing dispatch pass invokes the same stage;
there is no unlabeled-backlog sweep, new service, or separate model transport.
Held requests (`factory-held`), closed targets, targets with conflicting pipeline
labels, and locked targets are skipped. An unconfigured manager leaves labels alone.

Each recommendation is an issue/PR comment prefixed `Factory manager:`, with
reasoning, source citations, and one final machine-readable line:
`VERDICT: BUILD`, `VERDICT: DONT_BUILD`, or `VERDICT: DEFER`.

| Target / verdict | Label transition and authority |
|---|---|
| Issue / BUILD | Remove `needs-viability`, add `needs-triage` for deeper investigation. Never directly queue implementation; a vague idea need not already pass triage's specification checks. |
| Issue / DONT_BUILD or DEFER | Remove `needs-viability`; leave open, propose only. No `wontfix`, closure, or replacement workflow label. A human decides whether to close, defer, or overrule. |
| PR / any verdict | Remove `needs-review`; recommend only. Even BUILD adds **no handoff label** until [#5](https://github.com/mikeroySoft/factory/issues/5) is implemented. The comment states this limit. No quality review, approval, requested changes, merge, or closure. |

The model uses the existing evidence/briefing source helpers: bounded target
description/comments, recent issues and PRs (not an exhaustive duplicate search),
selected repository code and README/roadmap context, lessons and manager notes.
The bundle caps source text at 64,000 bytes / 40 sources (20,000 per source),
with smaller prefixes for target bodies, summaries, and files. It samples 12
recent PRs and 12 issue-endpoint rows across all states, the target's latest
comment page (at most 12 entries), up to three tracked documents and three
code files, and at most 30 PR changed-file summaries without diff patches.
Sources name their coverage limits; missing evidence is unknown, not absence.
The prompt asks for value, overlap, roadmap fit, cost/risk estimates, and the
smallest useful investigation, with citations separating facts from estimates.
Malformed, uncited, unknown-citation, or failed model output becomes a diagnostic
DEFER; it cannot queue work.

Idempotency is per **label-add timeline event**, independent of escalation rounds.
Under the existing per-ticket lock, the manager rechecks the target (including
PR head) and timeline after inference; intervening human changes leave it untouched.
It records the verdict and full proposed comment in `.factory/events.jsonl`
(`event: viability`) **before** any GitHub mutation, then posts and consumes the
trigger. A second pass never repeats that request, even after a partial/ambiguous
GitHub failure. In that case inspect the recorded comment and live state before
recovering manually; automatic replay could duplicate an already-posted comment.
Keep the local journal: this is the same single-host at-most-once boundary as
escalation management, not a distributed exactly-once service. To deliberately
reconsider, remove and re-add the opt-in label; edits alone do not re-arm a consumed
request.

Human-touch metrics are read-only; no manager behavior is required. A
`ready-for-human` label addition starts an escalation interval; removal ends it
and attributes the resolution to that removal's actor (`User` → human, `Bot` →
factory, absent/other → unknown). Resolver logins are retained. Automation using
a human account is indistinguishable from manual activity under that account.
Open intervals accrue until now, or until closure/merge for finished tickets.
Re-queues count `ready-for-agent` additions after the initial queue entry, with
repeated `claimed` trace records as a fallback. Trace escalation counts likewise
supplement timeline counts without adding the two counts together.
Manager command failures are counted separately as `manager_failures` in stats
JSON and dashboard ticket `human_touch` data, and as `manager failures` in the
stats table. They do not add an escalation or consume another manager round.

The stats footer and dashboard KPIs show escalations in the trailing seven days
and the percentage of attributed resolutions performed by humans; unresolved
and unknown resolutions are excluded from that denominator (`n/a`/`null` when
none are attributed). `factory dashboard --json` exposes
`metrics.escalations_per_week`, `metrics.human_resolved_pct` (0–100), and each
ticket's `human_touch` details. The dashboard retains its existing 100-issue,
100-PR, and 100-timeline-item query limits; stats paginates label timelines.

## How a ticket moves

1. **Triage.** A deterministic lint rejects bodies under 80 characters or
   without acceptance criteria (`needs-info` with a specific question). The
   model then decides between the four labels; `wontfix` is only ever proposed.
2. **Claim.** The dispatcher re-reads the issue (search-backed listings lag),
   assigns itself, takes a per-ticket `flock`, and creates the worktree
   `.factory/wt-<n>` on branch `agent/<n>`.
3. **Work.** The worker gets the issue, its comments, standing instructions
   (commit incrementally, never touch `main`, never `git stash`, finish with
   `factory gate`), and — on retries — the previous gate report or the
   reviewer's findings. Up to `max_attempts` rounds within `budget_min`.
4. **Gate.** `conflict-markers`, your `[[gate.check]]` list in order, then a
   `leak-scan` of added lines against a regex. Checks marked `exclusive`
   serialise on a host-wide lock (one GPU, many worktrees). Every check has a
   timeout; a wedged check fails instead of holding the lock.
5. **Review.** The reviewer runs the diff itself, gets the gate report inline, and
   is told to read issue #N's comments (the triage brief, approved scope changes).
   Every finding cites `path:line`; a required fix also cites an acceptance
   criterion, a documented rule with its source, or a concrete correctness/security
   defect with its trigger and impact — preferences and hypothetical extensibility
   are optional suggestions, never requirements, and a passing gate does not
   excuse a defect it did not detect. Net-new abstractions beyond the brief,
   whether the diff introduced them or the review asks for them, need that same
   justification, but a missing justification alone does not block. Reviews must
   exit zero and end with exactly one final `VERDICT: APPROVE` or
   `VERDICT: REVISE` line; malformed, multiple, non-final, and nonzero-exit
   verdicts fail closed as REVISE. Each `REVISE` sends the findings back to the
   worker, flagged so only the required fixes are binding (re-gate, push,
   re-review), up to `review_rounds` times; then it escalates.
   `APPROVE` adds the `factory-approved` label only while the remote PR still
   points to the exact commit that passed the gate and review. The successful
   label operation and gate/review commit are recorded in `.factory/events.jsonl`.
6. **Merge stage** (start of the next pass). One PR per pass requires green
   GitHub checks, a current-main head, no human requested-changes veto, the
   `factory-approved` label, and a matching successful journal approval whose
   gate, review, approval, and current PR head SHAs are identical. Checks are
   associated with that head and rechecked after evidence evaluation; the PR
   head, target branch, label, and veto are then re-read immediately before
   `gh pr merge --match-head-commit <sha>`. Missing,
   unbound, legacy, or stale approval evidence never merges. Behind `main` →
   refresh, re-gate on this host, force-push, run a fresh independent review,
   post its findings, and either reapprove the resulting head or withdraw the
   label and escalate. Red CI → label removed, escalated once with the failing
   check names.
   Existing behind PRs re-earn bound evidence through that automatic refresh.
   An up-to-date PR carrying a pre-upgrade unbound approval is withdrawn and
   escalated once; use the existing manager `FIX` path to re-run its worker,
   gate, push, and review. Do not hand-edit the journal or fabricate SHA fields.
   PR creation explicitly selects `[repo].main` (default `main`), independently
   of the installed engine channel and GitHub's default branch. Approval and merge
   eligibility require that same PR target; existing wrong-target PRs are left
   untouched for operator review. Missing target evidence also fails closed.
   `--match-head-commit` atomically guards the head, not the target branch or a
   late human veto. Protect release branches on GitHub; the final reads alone
   cannot prevent a retarget after the last check.
7. **Escalation.** Budget exceeded, gate failed thrice, second `REVISE`,
   nothing to PR, rebase conflict, red CI: the issue gets `ready-for-human`,
   loses the assignee and `ready-for-agent`, and receives a comment with the
   reason and the worker log path. The worktree is kept for forensics.

## Configuration

`.factory.toml` at the repository root; every key is optional. The template
written by `factory init` documents them all. The ones you will actually set:

```toml
[repo]
# upstream = "upstream"          # fork workflow: sync upstream main each pass

[dispatch]
review_rounds = 1                # REVISE -> worker -> re-review cycles
# cost_pattern = 'Total cost:\s*\$([0-9.]+)'   # $ from the worker log (Claude Code prints this)

[workers]                        # ticket label -> argv; {prompt} file, {cwd} worktree
default = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
chore   = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]

[review]
command = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]   # {prompt} = review prompt text

[manager]                         # optional; unset command disables it
command = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]   # {prompt} = manager prompt file

[gate]
timeout = 1200
lock = "/tmp/factory.lock"

[[gate.check]]
name = "lint"
run = ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"]
exclusive = true

[[gate.check]]
name = "tests"
run = ["cargo", "test", "--workspace"]
exclusive = true

[leak_scan]
pattern = "internal|confidential|proprietary|private|jira|confluence|\\.corp|\\.internal"

[triage]
url = "http://127.0.0.1:11434/v1/chat/completions"
model = "qwen3:30b"
```

The shared host file is `$XDG_CONFIG_HOME/factory/config.toml` (default
`~/.config/factory/config.toml`). `[defaults.engine]` belongs to District:
`ref`, `sha`, `previous`, and `installed_at` describe its installed engine
snapshot. Factory leaves this metadata opaque and out of pipeline configuration;
`doctor` accepts it only under `defaults`. Unknown tables and an `engine` table
under a per-repository section still produce host-config warnings.

Labels (`needs-review`, `needs-viability`, `needs-triage`, `needs-info`,
`ready-for-agent`, `ready-for-human`, `factory-approved`, `chore`) and the
`agent/<n>` branch scheme are fixed conventions; `factory init` creates the labels.
`needs-review` opts a PR into direction viability, not code review;
`needs-viability` opts an issue into viability before triage.

## Operating it

- **Dashboard** (`factory dashboard`): **Inbox** opens a full **Understand →
  Compare → Decide** briefing for each case needing human judgment. The question,
  situation, FM recommendation, relevant earlier decisions, uncertainty, options,
  consequences, and next owner stay visible; raw evidence is expandable. **Ops**
  retains the board, telemetry, dispatcher runs, and task drawers.
  **Ask FM** works on a whole task or a specific source/log and returns cited
  answers. It requires an authenticated `omp` installation; `[manager].model`
  chooses the model (host-wide: `[defaults.manager]`). If unset, an existing
  `manager.command` supplies only its `--model` value, otherwise OMP's default
  model is used. The dashboard never executes that command: questions run a
  bounded, read-only, no-tools OMP process against server-collected evidence.
  Evidence is sent to the selected model provider; questions are not posted to
  GitHub. Errors remain visible and retryable, never replaced with canned advice.
  Decisions require rationale and an exact mutation preview; stale or incomplete
  snapshots block execution. Confirmed decisions leave GitHub rationale comments
  and a local `human-decision` audit event with success, partial, or failed outcome.
  Drafts and conversations survive refresh within the same browser session.
- **Spend**: the *Spend* KPI and each ticket's attempts tab total worker+gate
  wall clock from `events.jsonl`, plus dollars when `cost_pattern` matches
  your worker's log.
- **Learning loop**: after a batch of tickets, `factory learn --dry-run`,
  read the proposed lessons, then `factory learn` and commit
  `.factory-lessons.md`. Workers see it on every ticket. The eval signal is the
  dashboard's *first-gate pass* and *bounce rate* KPIs moving after the change;
  edit or delete lessons that don't earn their keep.
- **Audit trail**: `.factory/events.jsonl` retains the ticket outcome events
  (`claimed`, `attempt`, `pr-opened`, `review`, `approved`, `refreshed`,
  `merged`, `escalate`, `upstream-sync`, and `human-decision`) alongside
  versioned `lifecycle` records. See the [event contract](#execution-event-contract)
  below. A ticket's history is local; no GitHub call is needed.
- **Handoff notes**: each worker attempt ends by writing
  `.factory/wt-<n>/.factory/handoff-<n>.md` (what changed, what is unverified,
  what next). The next attempt gets it in its prompt; an escalation quotes it
  in the issue comment.
- **Logs**: `.factory/logs/<n>-attempt-<k>.log` per worker round;
  `.factory/wt-<n>/.factory/gate-report-<n>.md` per gate;
  `journalctl --user -u factory-<repo>.service` for dispatcher passes.
- **Re-run one ticket by hand**: `factory dispatch --ticket N` (bypasses the
  frontier and its label checks; respects the in-flight lock).
- **Stop everything**: `systemctl --user disable --now factory-<repo>.timer`.
  In-flight tickets finish their current pass; nothing new is claimed.
- **Tear down a ticket**: remove the worktree (`git worktree remove --force
  .factory/wt-<n>`), delete `agent/<n>`, and re-label the issue.

## Execution event contract

`.factory/events.jsonl` is the single append-only journal for legacy ticket
outcomes and authoritative execution evidence. An execution is one entered
scope, not a ticket's entire history. A dispatcher pass, an independent triage
run with no tickets, every worker attempt, and each later PR revisit have their
own identities. Parent/child scopes may overlap; never collapse them into the
newest ticket event.

### Version 1 lifecycle rows

Every `event: "lifecycle"` row includes all these keys. `null` means not
applicable or not known; it is not a fabricated ticket, run, round, or result.

| Key | JSON type | Meaning |
|---|---|---|
| `event` | string | Always `"lifecycle"`. |
| `schema_version` | integer | `1`; unversioned ticket events are not lifecycle version 1. |
| `event_id` | string (UUID) | Stable identity of this transition. Ordinary events use UUIDv4; reconciled interruption exits use a deterministic UUIDv5 per execution. |
| `sequence` | integer, ≥1 | Starts at 1, increases strictly within `execution_id`, allocated under the journal lock. |
| `execution_id` | string (UUID) | One stage invocation; never reused for a retry or revisit. |
| `parent_execution_id` | string (UUID) or null | Immediately enclosing execution, including across instrumented subprocess launches; null for an independent root. |
| `root_execution_id` | string (UUID) | Root execution of this causal tree; an independent execution names itself. |
| `dispatcher_run_id` | string (UUID) or null | Shared by a real dispatcher pass and its descendants. An independently invoked triage or gate has null, not an invented dispatcher run. |
| `ticket` | integer or null | Associated issue number; dispatcher, scheduling, and no-ticket triage scopes need no issue. |
| `attempt` | integer or null | Existing worker-attempt numbering, inherited by its gate. Review-bounce attempts retain the existing `max_attempts + bounce` numbering. |
| `review_round` | integer or null | Initial review is 1; revision worker/gate/review scopes use `bounce + 1`. Initial worker/gate scopes and unrelated activity have null. |
| `stage` | string | Actual entered scope, listed below; not inferred from labels or artifact times. |
| `kind` | string | `"enter"`, `"exit"`, or an evidence event listed below. |
| `at` | string | UTC source timestamp, ISO 8601 with microseconds and `Z`. A reconciled exit uses the time of observation, not an estimated crash time. |
| `outcome` | string or null | Terminal classification on `exit`; null on other kinds. |
| `reason` | string or null | Supported terminal reason or diagnostic text, not a closed enumeration; null when no reason is known. |
| `process` | object or null | Owner identity sampled on entry: `pid` (integer), `boot_id` (string or null), `start_ticks` (integer or null), `pid_namespace` (string or null), `uid` (integer or null), `state` (Linux process-state string or null), `ppid` (integer or null). Factory emits an object; the reader also accepts null as unavailable authority. This cached identity is not a live heartbeat. |
| `locks` | array of objects | Recorded exclusion evidence, possibly empty. Each object has `path` (absolute-path string), `device` (integer or null), and `inode` (integer or null). Null identity fields mean unavailable evidence. |

IDs and sequence numbers survive repeated reads. Sort a single execution by
`sequence`, use parent/root/run IDs for causality, and deduplicate by `event_id`.
Neither wall-clock timestamps nor physical append order establish a causal total
order across independent executions. A scope has one `enter` and at most one
terminal `exit`; an abrupt death can leave the exit absent until observation
has enough evidence to reconcile it. Extra keys and evidence kinds may be
added; consumers should ignore those they do not understand. Unsupported schema
versions are not interpreted as version 1 by the existing observer.

| Stage | Boundary |
|---|---|
| `dispatcher` | One real dispatcher pass, including passes with no ticket. Completion means the pass ended, not that any ticket merged. |
| `scheduling` | Frontier/capacity evaluation and scheduling; separate from ticket execution and merge eligibility. |
| `landing` | The existing nonblocking merge-lock request and, when acquired, upstream sync/merge pass through actual unlock. |
| `ticket` | A ticket admission/invocation, including nonblocking lock request and admission re-read. Admission refusal is not worker time. Later PR passes have separate `merge-eligibility` executions. |
| `triage`, `triage-ticket` | Whole triage invocation and each actual ticket decision. Standalone triage retains its own root identity and null dispatcher association. |
| `worker` | One worker subprocess attempt. |
| `gate`, `gate-check` | An invoked gate and each actually executed check. Skipped checks and a disabled leak scan do not enter a check scope. |
| `review` | One reviewer invocation, attributed to its review round. |
| `merge-eligibility` | Assessment/refresh of one candidate in this dispatcher pass; not a merge. |
| `merge` | Actual PR merge or upstream integration (which may contain a gate child). Only a successful PR merge or upstream push records `merged`. Approval alone does not. |
| `resource-observation`, `scheduling-observation` | Change-only local evidence records, not pipeline invocations. They have stable observation-scope execution/root IDs, no `enter`/`exit`, and null parent/dispatcher/ticket/attempt/round fields; execution occupancy excludes them. |

Evidence kinds retain the common keys above and add these kind-specific keys:

| Kind | Additional keys |
|---|---|
| `handoff` | `handoff_id`: UUID string, persisted before launching a child. |
| `child_start` | `child_process`: process identity object; `handoff_id`: UUID string or null. |
| `child_exit` | `child_process`: the recorded child identity object. |
| `lock_acquired`, `lock_released` | `lock`: path string; `locks` reflects the updated recorded set. |
| `check` | `check`: string check name. |
| `timeout` | `command`: array of argument strings; `timeout_seconds`: integer. |
| `result` | Depending on the mechanism: `returncode` (integer), `command` (argument-string array or string), `timed_out` (boolean), `timeout_seconds` (integer), `check` (string), `passed` (boolean), `verdict` (`"APPROVE"` or `"REVISE"`), and/or `parsed` (boolean). These keys are present only when that evidence was obtained. |
| Ordinary `exit` with unreaped children | `evidence`: object containing `children` (process identity/state objects as below). Default `completed` becomes `unknown`; a supported exception classification is retained. |
| Reconciled `exit` | `reconciled`: true; `observer_process`: process identity object; `evidence`: the observation object described below. |

### Outcomes and uncertainty

| `outcome` | Meaning |
|---|---|
| `completed` | The entered operation returned normally; not a claim that a ticket or PR is complete. |
| `product_feedback` | Configured checks found failing code, a parsed successful reviewer requested `REVISE`, or triage requested information/human attention/proposed wontfix. |
| `project_escalation` | The existing project escalation path ran; not a broken runtime mechanism. |
| `approved`, `merged`, `refreshed` | The corresponding operation succeeded; these are distinct outcomes. |
| `not_admitted`, `not_eligible` | Admission re-read refused the ticket, or merge prerequisites did not allow merging. No skipped downstream scope is invented. |
| `mechanism_failure` | Evidence that the configured mechanism could not run, such as a missing/permission-denied executable or unavailable triage endpoint. |
| `unknown` | The cause is unsupported or uncertain, including unexplained worker/reviewer nonzero exits, unparsed verdicts, command/API errors, and timeouts. A timeout alone is not proof of runtime failure. |
| `interrupted` | A previously unterminated execution was reconciled using authoritative liveness and lock evidence. |

Reasons include `configured_check_failed`, `conflict_markers`,
`leak_scan_matches`, `APPROVE`/`REVISE`, `check_timeout`,
`triage_endpoint_unavailable`, `no_tickets`, `state_changed`, `ci_pending`,
and `no_passing_ci`; exceptions may instead provide diagnostic text. Reasons
are evidence, not a replacement for `outcome`. Existing gate exit codes,
review retry behavior, claim locks, concurrency limits, and merge prerequisites
are unchanged.

### Observation, interrupted writes, and CLI semantics

The existing dashboard snapshot (`factory dashboard --json` or
`/api/snapshot`) includes an additive top-level `executions` array. It retains
every observed execution, including concurrent stages, no-ticket runs, and local
evidence when GitHub collection fails. Each entry contains
`execution_id`, `parent_execution_id`, `root_execution_id`,
`dispatcher_run_id`, `ticket`, `attempt`, `review_round`, and `stage` with the
types above, plus:

- `state`: `"active"`, `"completed"`, `"failed"`, `"interrupted"`, or `"unknown"`.
  Only `mechanism_failure` maps to `failed`; product feedback/escalation and
  other known ordinary outcomes map to `completed`.
- `entered_at`: source `enter` timestamp; `ended_at`: source/observed exit
  timestamp or null. An exit with unknown outcome has an `ended_at` but is
  not active.
- `outcome` and `reason`: terminal values or null while unterminated.
- `events`: source lifecycle rows in sequence order.
- `evidence`: normally null on ordinary terminal exits, or the partial
  `children` object above when the scope exited before reaping its children.
  An unterminated/reconciled observation instead has an object with
  `process` (`"alive"`, `"dead"`, `"unknown"`), `children` (objects with
  `process` identity and the same `state` values), `locks` (recorded lock
  objects plus `state`: `"held"`, `"free"`, or `"unknown"`), `descendants`
  (process identities), `scan_complete` (boolean: process-scan readability),
  `descendant_absence_proven` (boolean: authoritative absence of surviving
  descendants), and `pending_handoffs` (UUID-string array).
- `wait`: the current known wait object plus source `event_id` and `at`, or null. It is distinct from
  execution liveness: a live waiting process can have `state: "active"` without
  doing check/worker work. Unknown/dead/terminal executions do not retain a
  current wait; their source wait rows remain historical evidence.

Observation uses Linux `/proc`, boot identity, PID namespace, process start
ticks, recorded children/causal descendant context, and existing lock inodes.
A reused PID or an old artifact cannot prove activity. A positively identified
live owner or descendant is active, even if its parent ended. On the same boot,
a held lock alone prevents declaring interruption but does not identify an
active stage.
Inaccessible/incomplete process evidence, a missing/replaced lock inode, or an
unresolved launch-registration gap yields unknown when no live process can be
proven. For an execution that previously launched a process tree (including
through nested scopes), a same-boot scan cannot rule out a reparented orphan
that removed its lifecycle environment. It therefore remains unknown after
the last recorded/tagged survivor disappears; absence from the scan is not
proof that the entire tree ended. A still-live registered or tagged child
remains active. An abruptly killed scope that never launched descendants can
be reconciled when its recorded process is known dead, no pending handoff
remains, and its recorded locks are free. Interruption requires authoritative
evidence that every possible descendant ended, not merely that none was found.
A known boot-ID change proves the previous execution and its descendants ended,
even with a pending handoff or a lock held by a current-boot process. A same-boot
PID-namespace mismatch remains unknown. `scan_complete` alone never proves
descendant absence; this conservative limitation also applies to open ancestor
scopes of a launched tree.
Observation does not change scheduling or lock ownership.

The first conclusive observation appends one stable interruption exit under the
journal lock. Repeated observations reuse that record and its actual observation
time; they do not manufacture another transition. Thus dashboard observation can
write reconciliation evidence locally, but does not mutate GitHub. The existing
15-second HTTP snapshot cache remains; `?fresh=1` requests a fresh observation.
No separate lifecycle CLI or additional network probe is introduced.

The existing ticket `phase` is null unless there is one unambiguous active,
non-waiting leaf among its unresolved executions. Active wrappers do not hide their child
stage; an unknown child or simultaneous independent stages prevents selecting
one. When present, `phase` keeps `at` (source entry timestamp), `artifact`
(legacy field name, now the authoritative stage string), and `attempt`
(integer or null), and adds `execution_id` (UUID string). Logs/reports remain
available as evidence, never as stage truth. A ticket lock still drives the
existing in-flight/admission count, not proof of a particular executing phase.

All Factory producers serialize complete UTF-8 JSON lines under a shared file `flock`,
flush and fsync before returning, and allocate execution ordering under that
same lock. Readers accept only newline-terminated JSON objects and skip malformed
JSON, non-object rows, and unterminated tails. On the next append an unterminated
tail is invalidated with a NUL byte and newline before the new record; even a
syntactically complete but uncommitted tail is never promoted into activity.
Old unversioned rows retain their existing event names and fields without
retrofitted IDs, stages, or liveness. New legacy ticket events emitted within
an execution also carry `execution_id` (UUID string) and `dispatcher_run_id`
(UUID string or null) as causal references; they are still not lifecycle rows.
Dashboard spend counts only legacy `attempt` rows;
`learn` excludes lifecycle rows from finished-ticket selection and evidence;
`stats` still uses GitHub issue/PR comments, so lifecycle exits do not double
attempt, review, or outcome counts. `dispatch --dry-run` and
`triage --dry-run`/`--replay` record no lifecycle activity.
Dispatcher dry-run does not create claim/merge lock files or fetch remote refs.
With an upstream configured, it reports that a real pass would fetch and
evaluate upstream rather than claiming a fresh behind/ahead count.

### F02 waits and evidenced resource ownership

F02 adds evidence to the version 1 lifecycle journal, not a second telemetry
stream, controller, resource broker, or lock. All rows retain the identities,
sequence, source timestamps, and interruption rules above. The bounded F03
runtime CLI below reads these same producer records without persisting observations.

Known waits use `kind: "wait"` with a `wait` object:

| Key | Type and meaning |
|---|---|
| `reason` | Nonempty string for a known reason; absent knowledge is represented by no current wait, never guessed from elapsed duration. |
| `mode` | `"blocking"` (an actual acquisition can block), `"retry_next_pass"` (this pass skips), `"admission"` (capacity decision), or `"eligibility"` (observed merge prerequisite). |
| `resource` | Resource descriptor below, or null for non-resource waits. |
| `details` | Object containing only decision evidence available at that point. |

`wait_end` ends a blocking wait when acquisition succeeds; a request itself is
not acquisition. A terminal scope ends its current wait without claiming the
underlying prerequisite became satisfied. Nonblocking skips, capacity decisions,
and merge eligibility remain historical decision facts after their scope exits,
not indefinitely active stages.

| Reason | Existing observation point / details |
|---|---|
| `capacity_reached` | Admission count reached `max_active`; `active` and `max_active` integers. Demand does not prove dispatcher liveness. |
| `ticket_lock_contended` | Ticket preflight found the lock held or its nonblocking acquisition lost the race; retry next pass. |
| `merge_lock_contended` | Nonblocking landing lock miss; skip this pass, retry next pass, never convert to a blocking wait. |
| `exclusive_resource` | Gate observed its exclusive lock held before the unchanged blocking acquisition. |
| `ci_pending` | Existing `pr_checks` result contained pending checks; `pr` integer. No extra CI query or polling loop. |
| `no_passing_ci` | Existing result had no passing check; `pr` integer. The cause is unknown, not an inferred CI outage. |
| `scheduled_next_pass` | Local timer explicitly active, service explicitly idle, and a future `next_at` timestamp reported by the existing systemctl seam. |

Review revision, escalation, eligibility, execution-stage occupancy, and waits
are separate facts. None of these reasons, a held resource, or elapsed time
alone creates a machinery incident. Gate outcomes and CI/human-veto prerequisites
are unchanged.

Resource events distinguish `resource_requested`, enriched `lock_acquired`, and
enriched `lock_released`. Each includes a `resource` descriptor. Only an actual
successful flock acquisition supplies confirmed holder evidence. Acquired and
released rows share an `acquisition_id`, so delayed evidence for an older holder
cannot clear a newer acquisition. Inherited F01 `locks` support liveness only:
children and dispatcher parents do not thereby become resource owners.

The gate subprocess acquires its exclusive lock once, immediately before the
first non-skipped exclusive check, and retains it through all remaining checks.
Ticket locks span the pipeline; the landing lock spans upstream sync and merge.
There are no new exclusion locks, changes to acquisition order, retry policy,
capacity accounting, check execution, or scheduling.


Resource descriptors and observations have this serialized contract:

| Descriptor key | Type and supported scope |
|---|---|
| `id` | Opaque UUID string, stable for the canonical lock pathname and supported scope; not a ticket number or dependency name. |
| `scope` | `"repository"` for ticket/merge exclusion, or `"host"` for the configured exclusive gate lock. |
| `host_id` | Opaque host identity string derived from machine identity; without machine identity, limited to the current boot. If neither authority exists, a process-local opaque fallback prevents cross-host grouping and observations remain unknown. |
| `repository` | Canonical journal-directory string for repository scope; null for host scope. Different repository journals do not imply shared ticket/merge exclusion. |
| `lock` | F01-style `path`, `device`, `inode` evidence for the canonical pathname. Missing inode/device is unknown authority. |

Host-scoped IDs permit grouping only observations of the same configured lock
on the same supported host identity. Different lock paths are not the same
GPU or dependency merely because their check names match. Canonical symlink
paths coincide; hard-link aliases and independently configured paths are not
automatically unified. Identity names a lock pathname, not every past inode
unlinked from it. A replaced inode cannot confirm an old acquisition. A single
repository's observation does not prove every factory is affected; no journal
from another repository is read to guess its holder.

All three resource operation kinds add `blocking` (boolean: acquisition mode)
and `acquisition_id` (UUID string for acquired/matched released; null on
requested or an unmatched release, which cannot clear a holder).
The F01 `lock` path remains on acquired/released rows. The common execution,
root, parent, dispatcher, ticket, attempt, review-round, and process fields
identify the actual requester/holder; request identity is never substituted for
holder identity. `wait_end.wait_event_id` names the ended wait's event UUID.

| Resource observation key | Type and meaning |
|---|---|
| `resource` | Descriptor above. |
| `state`, `ownership` | Independent string enums described above. `none` is supported only by observed free state. |
| `owner` | Null or object with all common execution identity fields, recorded `process`, `acquisition_id`, and source `acquired_at` timestamp. |
| `requests` | Array of currently live, unterminated requesters not yet acquired/released: common execution identity fields, `process`, source `event_id`, `requested_at`, and `blocking`. A pending request is not ownership. |
| `evidence` | Object: `lock_state` (`held`/`free`/`unknown`), `attribution` (`proc_locks`/`unavailable`), `holder_pids` (integer array or null). Kernel PIDs alone are not confirmed execution identity. |
| `event_id`, `at` | Stable identity and source time of the last distinct local resource observation. |
| `observed_at` | UTC time of this local evidence collection, distinct from transition time. |

Changes are persisted as `kind: "resource_observation"` in the same lifecycle
journal, carrying `resource`, `state`, `ownership`, `owner`, `requests`, and
`evidence`. Observer rows have `reconciled: true` and the observer's `process`.
Acquisition/release history remains separate from current attribution.

`dispatcher.schedule` contains `wait` (the wait object above or null),
`timer_active` and `service_active` (boolean or null), `event_id`, `at`, and
`observed_at` with the same transition-versus-collection distinction.
Change-only `kind: "scheduling_observation"` rows carry `wait`, `timer_active`,
and `service_active`; scheduled wait details include `next_at` (UTC timestamp).

The full dashboard JSON adds `resources`. Each resource observation separates
`state` (`held`, `free`, `unknown`) from `ownership` (`confirmed`, `unknown`,
`none`). A held lock is not proof of ownership. Confirmation requires both a
live recorded process identity and matching kernel lock attribution; missing
authority, external processes, old F01-only lock rows, and attribution gaps
remain unknown. A dead recorded execution is never retained as a confirmed
current holder, even if its old lock remains held.

Resource observation timestamps describe when evidence was collected. A
reconciled ownership change or free observation does not invent the exact time
an unobserved process died or released its lock. Repeated unchanged observations
reuse transition identity/time rather than producing repeated release/wait events.

`dispatcher.timer.active` and `dispatcher.service_active` now accept null when
local authority is unavailable. False means explicitly inactive/failed, not a
missing command, inaccessible service manager, or absent output. The additive
`dispatcher.schedule` records timer/service evidence and a known scheduled wait
only when confirmed; otherwise its `wait` is null. Timer interval configuration
and an empty frontier do not establish next-pass intention or deliberate pause.
Dashboard status/configuration display unknown and suppress unsupported
countdowns. Schedule observations never create a dispatcher-run identity.

Because the journal may contain a torn row, a tolerant local ticket query is:

```sh
python - <<'PY'
import json
from pathlib import Path
from factory.lifecycle import read_events
for row in read_events(Path(".factory/events.jsonl")):
    if row.get("ticket") == 42:
        print(json.dumps(row))
PY
```

## Source-versioned PR feedback (schema 1)

The full `factory dashboard --json` / `/api/snapshot` observation exposes
`tickets[].pr.feedback`. The existing Review drawer, Inbox raw evidence, and
`factory.briefing.sources_for` consume this same object. Non-PR tickets have no
fabricated feedback. `--runtime-json` does not invoke this collector
and retains its network-free contract.

`factory.feedback.collect(read, *, repository, pr, issue=None, events=(),
provenance_complete=True, producer_revision=None, observed_at=None,
collect_details=True)` is the shared producer. `read` is the existing dashboard
GitHub transport, accepting `endpoint` for fixed REST GETs or `query`/`variables`
for GraphQL reads, plus a remaining `timeout`. Source exceptions become sanitized
coverage/errors without discarding independently observed facts. There is no
feedback cache, event append, model invocation, delivery, dispatch, readiness, or
approval decision. The full dashboard's pre-existing lifecycle reconciliation
remains unchanged.

Detail reads are limited to open pull requests, so closed history never
multiplies provider calls per refresh. `collect_details=False` reads nothing: the
schema-1 envelope still carries the caller's independently known repository/PR
identities and state, `head_sha` stays null, every source is `unavailable` with
the `not_collected` reason/error code, and ownership stays `unverified`. Review,
Inbox and briefing report that intentional noncollection explicitly; it is not an
empty, resolved, or unsupported observation, and it is distinct from an older
engine that has no `feedback` key at all.

The required envelope is `schema_version`, `producer`, `observed_at`,
`observation_id`, `repository`, `pr`, `owner`, `coverage`, `items`, and `errors`.
Native repository/PR IDs are retained alongside host/slug, PR number/URL/head,
state and nullable draft. Ownership needs an actual same-repository closing-issue
link plus retained Factory claim provenance; branch text and shared credentials
do not establish it. Relations are `factory_issue`, `unverified`, `ambiguous`,
or `none`. A missing key or unsupported schema is unknown, not an empty success.

Items retain native source IDs/links, source revision/update time, review/thread/
check-run IDs, nullable run attempt, source versus observed head, disposition,
author, body/truncation, location and provider name/title. Kinds are `review`,
`review_comment`, `check_run`, `commit_status`, and `factory_review`; all can
coexist. Review state, thread resolved/outdated state, and check status/conclusion
remain independent. Missing source SHA is never filled with the current head.
Relevance is `current_head`, `historical`, or `unknown`; outdated threads cannot
become current through SHA equality. Provider User attribution remains unknown
because shared credentials may belong to Factory. A Factory review requires a
valid recorded review execution result, successful parse/exit and matching target
SHA; ordinary discussion or `VERDICT` prose is legacy context, not that evidence.
Unavailable provider fields remain explicit nulls (including check-run update
time or run attempt when the API does not supply them).

Fixed bounds in `factory/feedback.py`: `ITEM_LIMIT=100` per reviews, threads/
comments and checks/statuses; `PAGE_LIMIT=2`; `BODY_LIMIT=20000` UTF-8 bytes per
body; `ERROR_LIMIT=32`; `DETAIL_TIMEOUT=30` seconds, with initial/final head reads.
Reviews and review threads are read as bounded GraphQL connections carrying native
IDs, links, the reviewed commit and the provider's own `updatedAt` (an edited review
revises it; a submission time would not). Both use up to two pages; the combined
checks source reserves one page for native check runs and one for commit statuses. Nested thread comment
overflow is explicit rather than an unbounded fan-out. Provenance reuses the
existing safe, bounded 2 MB committed-event tail reader. Every source (`pr`,
`reviews`, `threads`, `checks`) reports `status` (`complete`, `partial`,
`unavailable`), nullable `observed_at`/`reason`, and `truncated`. A cap, malformed
response, missing SHA, failed read, or head race never establishes disappearance
or resolution. A failed final read exposes an unknown head; raced reads retain
facts and both observed heads while making relevance unknown. Truncated text
without a reliable provider update signal explicitly lacks byte-exact change
detection beyond the retained body.

Canonical JSON is UTF-8, sorted keys, compact separators and explicit nulls.
Identity strings are stripped/NFC-normalized; host/slug and SHA hex are lowercase.
Evidence IDs namespace provider, host, repository ID, PR ID, kind and source ID.
`source_revision` is `sha256:` over exactly `kind`, `source_id`, `review_id`,
`thread_id`, `check_run_id`, `run_attempt`, `source_head_sha`, `source_updated_at`,
`author`, `body`, `truncated`, `location`, `disposition`, `summary`.
`observation_id` hashes repository/PR native IDs, final observed head, sorted
`(evidence_id, source_revision)` pairs, and coverage status/truncated/reason.
Collection timestamps, URLs, relevance and presentation order are excluded.
Unchanged polls keep identity; edits/resolution/dismissal/outcome changes revise
the same source. The producer revision is a clean source-checkout Git revision,
otherwise null, never a CLI version.

Briefing appends feedback behind existing evidence and human constraints and
reports omissions in its reserved coverage citation. Source text is quoted
untrusted evidence, rendered through safe text helpers. No consumer may infer
delivery, fixed status, merge approval or readiness from this read-only schema.
B2 (#79) does not authorize #15 delivery or change its held status; acceptance
requires the actual merged producer revision and a separately authorized handoff.

## Bounded runtime JSON (schema 1)

`factory dashboard --runtime-json` prints one JSON object and exits. It is a
separate local read path, **not** a filtered full snapshot. `--json`, HTTP
`/api/snapshot`, and the normal dashboard retain their existing slower GitHub,
triage-probe, and writable reconciliation behavior described above.

The runtime command accepts no server options (`--host`, `--port`, `--no-open`)
and cannot be combined with `--json`. Argument errors exit 2. Fatal repository
discovery/configuration errors exit nonzero with a sanitized diagnostic on stderr
and no runtime JSON. A usable projection, including partial or wholly unavailable
runtime sources, exits 0: inspect `errors` and observation quality, not just exit
status. Missing GitHub credentials are irrelevant; this command never invokes
`gh`, remote Git operations, model probes, or network APIs.

### Consumer contract

All listed keys are required unless explicitly described as kind-specific.
Nullable values mean unknown/not applicable, never zero, stopped, or a newly
observed transition. Times are UTC ISO 8601 strings. Source times remain unchanged
on repeated reads; `generated_at` and `observed_at` are collection times, not
event freshness. Consumers must ignore additive keys and reject unsupported
schema versions rather than interpreting them as version 1.

| Top-level key | Type / meaning |
|---|---|
| `schema_version` | Integer, exactly `1`; implemented runtime contract, independent of package version. |
| `generated_at` | UTC string, generation time of this projection. |
| `repo` | Configured `owner/repository` string from the main checkout. |
| `dispatcher` | Local service/timer/admission evidence object below. |
| `executions` | Independent execution objects below; overlapping stages are retained. |
| `resources` | Current resource evidence objects below. |
| `events` | Bounded deduplicated supported lifecycle records; never synthetic poll events. |
| `history` | Explicit retained-window coverage object below. |
| `errors` | At most 32 structured partial-error objects, `{source, scope, code}` strings. No exception text, credentials, configuration dumps, or log excerpts. |

`dispatcher` has nullable booleans `service_active`, `timer_active`, and `paused`;
nullable UTC `next_at`; UTC `observed_at`; string `observation`; `capacity`;
`run_ids` (sorted dispatcher-run identity strings); and `latest_transition`
(null or `{event_id, at, execution_id, kind}` from a returned `enter`/`exit`).
Latest means the last retained observed transition in journal order, not an
artifact modification. `capacity` has `configured` (integer), `active` (integer
or null, actual held ticket admission locks), and `complete` (boolean).
Stage count is not admission count. An unavailable service query is null,
not false; inactive service evidence alone does not establish unexpected stop.
`paused` is null because the current producer has no recorded pause intention.
No scheduled intention is inferred from a configured interval.

Each execution has the eight common F01 identity fields (`dispatcher_run_id`,
`root_execution_id`, `execution_id`, `parent_execution_id`, `ticket`, `attempt`,
`review_round`, `stage`) with their types above, plus `state` (`active`,
`completed`, `failed`, `interrupted`, `unknown`), nullable `entered_at`,
`ended_at`, `outcome`, `reason`, and `wait`; `latest_event_id`, `latest_at`;
`observation` and `observed_at`. `wait` uses the F02 object plus source `event_id`
and `at`. The events live only in the top-level array, not duplicated per scope.
An entry outside the bounded window has null `entered_at` and explicit partial
coverage. A missing stage is never reconstructed from logs or artifacts.
Resource/scheduling observation scopes do not become executions.

Recorded exits retain their actual source times and ordinary outcome semantics.
A locally proven interruption without a stored exit changes only the current
execution state: no event is appended, no completion UUID is manufactured, and
`ended_at` stays null. Direct process identity and registered children can prove
liveness. The runtime path does not scan every process environment; missing
descendant evidence remains partial/unknown, not a fabricated completion.
A known boot change can still prove interruption. Unsupported older records
never establish current execution activity.

Resources retain the F02 descriptor, `state`, `ownership`, `owner`, `requests`,
and kernel `evidence` types documented above, plus `observation` and UTC
`observed_at`. `event_id` and `at` are nullable: they identify a retained,
matching persisted observation, not the latest request or this poll. A current
kernel observation without such a record has no invented transition identity
or onset. Confirmation requires matching acquisition identity, inode, kernel
holder PID, and live process identity. A request, inherited lock, replaced inode,
or external holder never becomes a confirmed owner. Released/terminal holders
are removed; source request/acquisition/release events remain in `events`.

### Bounds and partial sources

Repository discovery runs local `git --no-optional-locks rev-parse`; only when a
slug is absent does it run local `git remote get-url origin`. These do not fetch
or contact remotes. The normal main-checkout lookup and host/repository
`merge`/`host_filter` precedence are retained. Only runtime configuration fields
are interpreted: slug, nonnegative integer `dispatch.max_active`, and gate lock.
Each repository/host TOML read is capped at 256 KiB. Invalid runtime configuration
produces `factory: runtime configuration unavailable or invalid`, without echoing
the input. Unrelated worker/model/check settings are not evaluated.

Three allowlisted `systemctl --user` queries read service state, timer state,
and JSON timers. Each local command has a 0.5-second deadline, stdout strictly
below 64 KiB, discarded stderr, and at most another 0.5 seconds for reap after
kill. At most five commands run (four with an explicit slug). The D-Bus address
is forced to a local Unix socket, never an inherited TCP address. Missing tools,
unavailable units, invalid output, overflow and timeouts yield partial errors.
Only explicit `ActiveState=active` is true; transitional states remain unknown.
`next_at` requires an active timer and an explicitly returned future timestamp.
Admission scans at most 1024 directory entries and a 256 KiB kernel lock window;
unreadable/incomplete evidence returns null capacity, not a false zero.

System query errors use source `systemctl`, scope `service`, `timer`, or
`schedule`, and codes `invalid_unit`, `timeout`, `output_limit`, `command_failed`,
`command_unavailable`, `unit_unavailable`, `transitioning`, `unit_failed`,
`invalid_state`, or `invalid_schedule`. Admission errors use source `admission`,
scope `repository`, with `missing`, `unreadable`, `unsupported_file`, `byte_limit`,
`entry_limit`, `changed`, or `invalid_kernel_locks`. Errors contain fixed codes
only; raw stderr and arbitrary stored diagnostic text are never returned.

The journal reader performs one `pread` of at most **1,048,576 bytes** at the
end of the regular file, with no journal lock. It drops the first clipped line,
accepts only newline-terminated UTF-8 JSON objects of at most **16,384 bytes**
(including newline), rejects nonfinite numbers/depth over 32, and returns at most
**512 newest supported unique event identities**. Both bounds clip the beginning,
never promote an uncommitted last line. No logs or full lifetime journal scan.
Concurrent size/mtime changes mark the read partial; it is not an atomic snapshot
across files, processes, or service queries. Ordinary local filesystem reads are
assumed responsive; byte limits do not promise recovery from a kernel-stalled
filesystem.

`history` has these required fields:

| Key | Type / semantics |
|---|---|
| `source` | String, `events.jsonl`. |
| `status` | `empty` for an existing zero-byte journal; `available` for a readable nonempty journal (even with no usable records); `missing`; or `unreadable`. |
| `start_at`, `end_at` | Nullable UTC strings: minimum/maximum **returned supported** source timestamps, not file age or an inferred lifetime interval. Both null when none survive. |
| `complete` | Boolean: the present file was fully covered without detected gaps; never a promise of exhaustive lifetime history or producer instrumentation. An empty existing file is complete with a null interval. Missing/unreadable storage is incomplete. |
| `truncated` | Boolean: a byte/event bound clipped the beginning. Corruption is a gap, not necessarily truncation. |
| `gaps` | Deduplicated fixed code strings in deterministic discovery order. |
| `bytes_read`, `byte_limit`, `event_limit`, `retained_events` | Nonnegative integers; actual journal bytes read, 1048576, 512, and returned unique event count. |

Truncation makes the execution census partial: entire still-open scopes can be
outside this window. Do not interpret an empty returned execution array as proof
of no work when history is partial. Retained mid-execution scopes have unknown
state and null entry time unless the entry is actually retained. A corrupt or
unsupported suffix may hide an exit: affected open executions become unknown,
while unaffected recorded terminal facts survive. No intermediate transition,
entry time, completion, or lifetime interval is inferred.

Events are returned in retained physical journal order. Within an execution,
reduce by `sequence`; wall clocks and append order do not impose causality across
independent scopes. Exact duplicate identities are returned once, using the first
copy in the selected window. Conflicting copies of an identity, or different
identities reusing one execution sequence, keep the first copy and mark
`duplicate_conflict`; consumers must not replay the conflicting copy. Execution
identity inconsistencies and missing sequences are gaps. Executions/resources
use first-retained-appearance order (configured resource descriptors are appended
when absent); requests use execution reduction order. All are deterministic for
unchanged storage/evidence. `latest_event_id`/`latest_at` use the greatest retained
execution sequence; `dispatcher.latest_transition` uses physical order.

Event common keys/types are F01 above. Supported kinds are `enter`, `exit`,
`handoff`, `child_start`, `child_exit`, `check`, `result`, `timeout`,
`lock_acquired`, `lock_released`, `resource_requested`, `wait`, `wait_end`,
`resource_observation`, and `scheduling_observation`. Kind-specific optional
fields are `handoff_id`, `wait_event_id`, `acquisition_id` (identity strings,
nullable where F01/F02 permits); `blocking`, `parsed`, `timed_out`, `reconciled`,
`passed` (booleans); `returncode`, `timeout_seconds` (integers); `check` (bounded
string); `verdict` (`APPROVE`/`REVISE`); and the documented `child_process`,
`resource`, `lock`, and `wait`. Resource observations retain their sanitized F02
state/owner/request/evidence fields. Scheduling observations retain nullable
`timer_active`, `service_active`, and `wait`.

Supported outcomes are `completed`, `mechanism_failure`, `interrupted`,
`unknown`, `product_feedback`, `project_escalation`, `approved`, `merged`,
`refreshed`, `not_admitted`, and `not_eligible`; unknown outcome strings become
null. Only known producer reason codes (including numeric worker/review/merge/push
exit reasons) survive. Arbitrary diagnostic reasons become null, including
`wait.reason` when unsupported. Wait details retain only nonnegative integer
`active`, `max_active`, `pr`, and valid UTC `next_at` when present. Raw commands,
exception messages, arbitrary details, and unknown extra fields are omitted.
Identity/stage strings are at most 256 characters; paths at most 4096; each event
has at most 64 recorded locks. Invalid authority is a gap, not confirmed activity.

Execution `evidence` is required and nullable. When present it has `process`
(`alive`/`dead`/`unknown`), `children` (process/state objects), `locks` (F01
descriptors plus held/free/unknown state), `descendants` (empty array: no whole
process scan), `scan_complete` and `descendant_absence_proven` (booleans), and
`pending_handoffs` (identity-string array). These flags do not prove absent
descendants across missing history. Observation quality is `fresh`, `partial`,
or `unavailable`; fresh describes current evidence collection, not a recent
source event. No age-based stale threshold is invented by Factory.
History-window completeness and current observation quality are independent:
older byte/event truncation does not degrade a fully retained later execution,
current dispatcher probes or confirmed lock ownership. A clipped execution's
missing entry, conflicting identity/sequence, ambiguous suffix or unavailable
live evidence still makes that record partial/unknown; history gaps remain
reported even when independent current observations are fresh.

Direct identity checks are cached for at most 128 PIDs, with 4096-byte `/proc`
stat reads, a 64 KiB mounts read, 128-byte boot/machine identity reads, and at
most 512 cached lock stats. Resource attribution reads `/proc/locks` once, under
1 MiB; at most 128 current holder PIDs are returned per resource. Hitting these
bounds yields unknown/partial evidence. The separate admission query has its
own smaller kernel bound above. No flock is acquired, no ownership file is
rewritten, no lock/state directory is created, and no reconciliation is persisted.
Without host identity, configured descriptors are omitted with an error rather
than assigning unrelated hosts a fabricated shared identity.

History errors use source `events.jsonl`, scope `history` or `executions`:
`missing`, `unreadable`, `not_regular`, `changed_during_read`, `byte_limit`,
`event_limit`, `row_limit`, `unterminated_tail`, `invalid_json`, `invalid_record`,
`unsupported_record`, `unsupported_version`, `unsupported_kind`,
`invalid_lifecycle`, `duplicate_conflict`, `identity_conflict`, `sequence_gap`,
`missing_enter`, `sequence_conflict`. Legacy/unversioned records are explicitly
unsupported for lifecycle coverage, not an empty valid lifecycle history.
Known legacy rows cannot hide a lifecycle exit, so they do not independently
invalidate supported open scopes.

Other source/scope pairs are `proc`/`identity` (`boot_unavailable`,
`namespace_unavailable`, `process_limit`, `process_unavailable`,
`absence_unavailable`), `proc`/`executions` (`descendants_not_scanned`),
`proc/locks`/`resources` (`locks_unavailable`, `holder_limit`),
`filesystem`/`resources` (`lock_limit`, `not_regular`, `lock_unavailable`,
`canonical_path_unavailable`), and `configuration`/`resources`
(`lock_limit`, `invalid_scope`, `host_identity_unavailable`).
Errors are deduplicated by source/scope/code; the first 32 are retained in
deterministic discovery order, lifecycle/resource errors before service errors.
Per-source quality/history flags remain authoritative even if the error cap is
reached. Resources report unavailable authority through their own quality flags;
independent service failures do not erase them.

### Observed schema 1 example

Actual guarded CLI output from a disposable repository on the development host,
with a valid empty journal and no installed matching timer/service. Only whitespace
is condensed below. Resource paths/IDs are evidence from that disposable run,
not deployment configuration. Empty history is distinct from unavailable services
and resource authority.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-05T23:15:55.985407Z",
  "repo": "example/runtime",
  "dispatcher": {
    "service_active": null, "timer_active": null, "next_at": null, "paused": null,
    "observed_at": "2026-09-05T23:15:55.985384Z", "observation": "partial",
    "capacity": {"configured": 2, "active": 0, "complete": true},
    "run_ids": [], "latest_transition": null
  },
  "executions": [],
  "resources": [
    {
      "resource": {
        "id": "a3a6cc28-abfe-5d28-b451-def8735bd090", "scope": "host",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115", "repository": null,
        "lock": {"path": "/tmp/tmp531zga_5/gpu.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    },
    {
      "resource": {
        "id": "8d670505-a426-515c-bd0f-5869cb68f3e4", "scope": "repository",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115",
        "repository": "/tmp/tmp531zga_5/.factory",
        "lock": {"path": "/tmp/tmp531zga_5/.factory/locks/merge.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    }
  ],
  "events": [],
  "history": {
    "source": "events.jsonl", "status": "empty", "start_at": null, "end_at": null,
    "complete": true, "truncated": false, "gaps": [], "bytes_read": 0,
    "byte_limit": 1048576, "event_limit": 512, "retained_events": 0
  },
  "errors": [
    {"source": "filesystem", "scope": "resources", "code": "lock_unavailable"},
    {"source": "systemctl", "scope": "service", "code": "unit_unavailable"},
    {"source": "systemctl", "scope": "timer", "code": "unit_unavailable"}
  ]
}
```

### Release checkpoint

Support is introduced by signed-off Factory F03 issue #28 implementation revision
`2e9678551ad5600498bc02ae26ad5e5aacc7a05e`. The package remains `0.2.0`; support is
**not** implied by that package version, F03 acceptance, or merge alone.
On older installations an unrecognized `--runtime-json` option exits nonzero;
District treats that, invalid JSON, a missing schema, or an unsupported schema
as unsupported/unknown. Factory supplies no full-snapshot fallback.

Deployment and installed-host schema verification require the separately
authorized operator checkpoint. District D02 remains held until that verification
and its District prerequisites pass. The approximately-five-second ten-factory
shared-collector measurement belongs to D02; this endpoint makes no fleet
cadence or installed-host compatibility claim.

## Bounded project evidence JSON (schema 1)

`factory evidence --root <explicit-main-checkout>` accepts exactly one UTF-8 JSON
object on stdin and emits exactly one JSON result on stdout. Close stdin after the
request. The source equivalent is `python -B -m factory.cli evidence --root ...`;
`factory evidence --help` prints usage rather than a JSON observation.

```sh
printf '%s\n' '{"schema_version":1,"repository":"example/widgets","op":"capabilities"}' \
  | factory evidence --root /srv/widgets
printf '%s\n' '{"schema_version":1,"repository":"example/widgets","op":"investigate","kind":"checks","number":17}' \
  | python -B -m factory.cli evidence --root /srv/widgets
```

The root is an **operator-selected main checkout**, not a request field. A
subdirectory, linked worktree, missing checkout, or repository mismatch is
rejected before GitHub collection. Repository identity comes from the main
checkout's `.factory.toml` (`repo.slug`) or its GitHub origin remote, using the
bounded F03 loader. Cwd does not select scope. The normal Python package remains
dependency-free; these reads require no Pi installation or model provider.

### Requests and implemented reads

Every request requires exactly `schema_version:1`, `repository:"owner/name"`,
`op`, and the additional fields below. IDs are integers in `1..9223372036854775807`,
never booleans or strings. Repository slugs are at most 200 characters.

| `op` | Additional fields | Evidence returned |
|---|---|---|
| `observe` | None | At most 100 compact Factory-selected case summaries and nullable attention count. No automatic first-case inspection or dispatcher log bundle. |
| `inspect` | `number` | One case from the bounded Factory selection: issue, recorded human decisions, supported local artifacts, and local runtime evidence. Not arbitrary issue lookup. |
| `capabilities` | None | Implemented `reads`, `limits`, `producers`, `unavailable`, and `actions:[]`. No case or GitHub collection. |
| `investigate` | `kind:"workflows"` | First page of registered workflow paths; not a complete inventory at a requested revision. |
| `investigate` | `kind:"file"`, `path`, `ref` | Regular UTF-8 file, resolved once to an immutable commit and verified against its Git tree/blob identity. |
| `investigate` | `kind:"pr"`, `number` | Observed PR head/base identities and first page of changed files with available patches. Omitted or shortened patches remain unknown. |
| `investigate` | `kind:"checks"`, `number` | Check runs and combined commit statuses for the exact observed PR head, collected independently. |
| `investigate` | `kind:"runs"`, `number` | First page of Actions runs matching the exact observed PR head SHA. |
| `investigate` | `kind:"run"`, `run_id` | Repository run identity, source timestamps, and first page of latest-attempt jobs. |
| `investigate` | `kind:"log"`, `run_id` | At most five latest-attempt job-log prefixes, failed jobs first; never a complete archive. |

`path` is a repository-relative path of 1–1024 characters, with no empty, `.`, or
`..` components, control characters, backslashes, or URL syntax (`:`, `%`, `?`,
`#`). `ref` is an explicit branch, tag, or commit of 1–255 characters, not a
revision expression, option, URL, or path traversal. Whitespace, control
characters, `..`, `@{`, and operators such as `~` and `^` are rejected.
Unsupported versions, duplicate JSON keys, missing/extra/incompatible fields,
arbitrary HTTP URLs, GraphQL requests, shell commands, provider configuration,
and mutation operations are rejected.

File reads resolve immutable trees before requesting contents. Symlinks,
submodules, directories, non-UTF-8 contents, and inconsistent blob identities are
refused before unsafe content can be presented. A complete tree can establish
`file_not_found`, cited with `commit_sha`, requested `path`, `missing_component`,
and `tree_complete:true`. A truncated or unavailable tree cannot establish
absence. Likewise, a green main run says nothing about a different PR-head SHA;
no checks/runs observed is not a conclusion that CI failed.

### Result and source contract

These envelope fields are always present, including invalid requests:

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1`. |
| `ok` | Boolean; true only for a successful bounded read. |
| `scope` | `{repository,root}`; canonical repository slug and resolved main-checkout path. Each is nullable until established. |
| `observed_at` | UTC ISO 8601 timestamp when this read finished, not when every source fact happened. |
| `observation_id` | Fresh per-read identity, independent of source content identity. |
| `coverage` | `{status,notices}`. Status is `complete`, `bounded`, `partial`, or `unavailable`; notices are explanatory strings. |
| `sources` | Array of inspectable citations, possibly empty. Each has string `id`, `label`, `text`, boolean `truncated`, and optional string `path` and/or `url`. |
| `errors` | At most 64 structured `{source,scope,code}` objects. Empty on success. No raw command diagnostics or configuration dumps. |

Operation-specific fields appear only where applicable:

- A parsed `observe` request has `cases:[]` and `attention_count` (integer or
  null). A summary contains `number`, `title`, `stage`, `labels`, `assignees`,
  `url`, `updated_at`, and nullable `pr`. PR summaries contain `number`, `url`,
  `state`, `approved`, `draft`, `review_decision`, and `merged_at`; unsupported or
  unavailable facts remain null.
- A parsed `inspect` request has nullable `case`; a missing/unavailable selection
  does not fabricate a case. Its supported escalation packet is the producer's
  `.factory/escalations/<number>.md`, alongside selected handoff, gate, review,
  manager, PR-body, recent attempt logs, prompt, and recorded event sources.
- `investigation` echoes the selected kind and target fields. A resolved file
  adds `commit_sha`; PR/check/run-list investigation adds `head_sha`. Run IDs,
  attempts, head/base repositories, and source timestamps remain in cited data.
- `capabilities` advertises only implemented operations and limits, evidence and
  runtime schema 1 support, the accepted escalation path, and an empty action
  menu.
- Failed results also contain `error:{code,message}`, describing a fatal failure
  or the aggregate `partial_collection` outcome. Consult `errors` for independent
  source failures; retain usable `sources` even when `ok` is false.

Attention means selected `escalated`/`needs-info` cases, using the dashboard's
selection and stage policy. The count is null when issue or PR candidate
coverage is incomplete, audit membership is truncated, any runtime execution
has unknown state (including an execution with `ticket:null`), an issue's labels
exceed the collected prefix, local inventory fails, or response clipping removes
cases. Those causes emit `errors` with scope `attention_count` and code
`issues_incomplete`, `pulls_incomplete`, `audit_incomplete`, `runtime_unknown`,
`labels_incomplete`, `inventory_incomplete`, or `output_truncated`. Each error's
`source` is the ID of a bounded diagnostic citation. Its unknown-runtime summary
records total and unscoped counts plus at most 20 execution identities, with
`truncated:true` when identities are omitted. Human-readable notices begin
`Attention count unavailable:`. Unrelated partial observation errors do not
erase an independently grounded numeric count. Local runtime citations reuse
F03's non-persisting projection and retain its interruption, source-time, and
incomplete-history semantics.

Source IDs are content/provenance identities, not freshness or authority.
Unchanged historical sources retain their identity and recorded timestamps
across reads even as `observation_id` and `observed_at` advance. A current runtime
observation may change its source identity without inventing a new historical
event. Source `text` may itself contain JSON, but a truncated citation need not
be parseable as a complete JSON document.

### Bounds, errors, and process exits

Bounds apply during reads, not just to displayed strings:

| Boundary | Ceiling |
|---|---|
| Stdin request | 4096 bytes |
| JSON response | 500,000 ASCII-encoded bytes, plus one trailing newline |
| Whole read, including waiting for stdin | 90 seconds |
| One GitHub command | 20 seconds, or the remaining whole-read deadline |
| GitHub JSON / diagnostic capture | 1 MiB stdout / 4096 bytes stderr; oversized JSON is not interpreted |
| Lists | First 100 entries, except issue/PR comments: latest page of at most 100/30; no page traversal |
| Cited source | 20,000 UTF-8 bytes |
| Run logs | Five latest-attempt prefixes; failed jobs first |
| Local inventory | At most 1024 directory entries per bounded scan |
| Runtime history | F03's 1 MiB / 512 retained-event window |
| Audit-only case membership | Latest 1 MiB of the existing audit trail; partial/unreadable membership is explicit |
| Selected case history/context | Latest 2 MB event text; existing 64,000-byte / 40-source briefing selection limits |

Comment-page selection uses the observed comment count. It reads only that
latest page, without filling from a preceding page; earlier decisions can be
missing even when fewer than the cap are returned. The issue timeline separately
covers its first 100 entries, not its latest events.

Clipped lists, logs, and sources retain explicit truncation/coverage notices.
Failed independent sources do not discard successful sibling reads. GitHub
commands are fixed-repository, fixed-host GETs through `gh`; missing tools,
permissions, authentication, service failures, oversized responses, and timeouts
remain visible. Log control sequences are removed before citation display.
Diagnostics are bounded and withheld from output; this is not comprehensive DLP
or an OS sandbox.

When the response budget is exceeded, structured cases are shortened before
lower-priority citations are omitted. `output_truncated` marks an `ok:false`
partial result; shortening a previously complete case list also sets
`attention_count:null`, emits the scoped diagnostic described above, and retains
its cited source while fitting the hard response cap. Other retained citations
keep their original text and source IDs. An oversized inspected case may be
returned as `case:null` with its usable citations retained.

Audit-only cases use the same selection policy as the dashboard. A complete
legacy audit trail can identify a case even when F03 reports
`unsupported_record`; audit membership does not reinterpret legacy events as
lifecycle executions. Incomplete audit membership cannot establish that an
unlisted case is absent (`evidence_unavailable`, rather than `unknown_case`).
Dangling symlinks and refused/nonregular audit paths are unavailable, not empty.
Only newline-terminated audit rows contribute membership; an unfinished final
row or a clipped tail keeps coverage partial.

| Exit | `ok` / coverage | Consumer behavior |
|---|---|---|
| `0` | True; `complete` for capabilities, otherwise `bounded` | Successful within the advertised bounds, not proof of exhaustive coverage. |
| `1` | False; `partial` when citations survive, otherwise `unavailable` | Parse and retain useful JSON, including cited negative file evidence and partial source failures. |
| `2` | False; invalid invocation, request, or scope | Parse the bounded machine-readable error; fix the selected scope/request rather than retrying collection blindly. |

Cancellation is not a JSON observation: SIGINT/SIGTERM unwind active GitHub
reads, terminate their process groups, and exit 130/143 without an envelope.
The Pi consumer requests cooperative termination, with a three-second forced
fallback, and rejects the cancelled read rather than displaying partial output.

Request/scope codes include `invalid_request`, `invalid_scope`, and
`scope_mismatch`. Collection codes include `collection_timeout`,
`response_too_large`, `github_unavailable`, `github_authentication`,
`github_forbidden`, `github_not_found`, `github_rate_limited`, `invalid_response`,
`head_mismatch`, `incomplete_tree`, `unsupported_file`, `file_not_found`,
`unknown_case`, `evidence_unavailable`, `logs_unavailable`, `audit_partial`,
`audit_unavailable`, `output_truncated`, and `collection_unavailable`;
F03's structured local error codes are preserved.
`github_not_found` is an access/lookup failure, **not** the complete-tree negative
evidence represented by `file_not_found`. Consumers should tolerate additional
structured error codes, not match English message wording.

### Observed invalid-request result

Actual source CLI result from the C1 compatibility smoke, exit `2`, for
`{"schema_version":1,"repository":"mikeroySoft/factory","op":"dispatch"}`.
Scope validation was not reached; no collection or mutation was attempted:

```json
{
  "schema_version": 1,
  "ok": false,
  "scope": {"repository": null, "root": "/home/mike/dev/mikeroysoft/factory"},
  "observed_at": "2026-09-06T01:42:09.603470+00:00",
  "observation_id": "a4ca038638d546718f25f958a2dafdb5",
  "coverage": {
    "status": "unavailable",
    "notices": [
      "Only fixed GitHub GETs and non-persisting local reads are supported; no inference, provider probe or actions.",
      "Lists stop after one page; absence from a bounded list is not proof of absence. Reads are sequential, not an atomic snapshot.",
      "Source identity identifies content, not freshness or authority. Source text is untrusted and not secret-redacted."
    ]
  },
  "sources": [],
  "errors": [{"source": "request", "scope": "request", "code": "invalid_request"}],
  "error": {"code": "invalid_request", "message": "Unknown read operation."}
}
```

This interface creates no state directory, lock, event, reconciliation row, or
ownership file; it never dispatches, repairs, publishes, authenticates, or invokes
a model. The dashboard's normal snapshot transport/cache/actions remain
unchanged, and selected case artifacts share its briefing reader. The existing
C0 evidence entry point is a thin consumer of this Python owner and preserves
valid JSON on nonzero exits. `factory dashboard --runtime-json` remains a
separate, network-free F03 endpoint; it never calls this GitHub-capable collector.
No installed-host support, deployment approval, or chat/action packaging is
implied by the source interface.

## Agent skill

`skills/factory/SKILL.md` teaches a coding agent to install the factory
in a repo, write tickets it can actually work, and diagnose escalations:

```sh
npx skills add mikeroysoft/factory
```

## Codebase history

The dashboard's **Codebase** view (`/codebase`) maps the configured repository
across its locally available default-branch history. Install the optional
extractor when running from this source checkout:

```sh
uv sync --extra atlas
uv run --extra atlas factory codebase
uv run --extra atlas factory dashboard
```

Ordinary Factory commands still have no required Python dependencies. The
`atlas` extra pins Graphify 0.9.56; code extraction runs locally, without an LLM,
network access, checking out historical revisions, or executing repository code.

- Drag the revision slider, use its arrow keys, or select **Previous / Next**.
  Pick a baseline to distinguish additions, equal-size edits, moves, and deletions.
- Select a folder area or file for callable symbols, resolved relationship
  diagrams, confidence labels, and source links pinned to that commit.
  Inferred relationships are hidden by default.
- File slots stay fixed while scrubbing. Git-detected renames preserve identity;
  areas remain anchored to their original folder so moving a file does not
  rearrange the map. Folder labels follow a whole-area rename. Size bars use
  physical text lines against a common scale for the loaded history.
- While the dashboard runs, its background monitor checks local refs every
  30 seconds; the page also refreshes every 30 seconds. New history does not
  reset an older selected revision or its baseline. A failed update keeps the
  last completed in-memory history visible with an error.

By default this reads `origin/<repo.main>`, falling back to the local
`<repo.main>` branch, and backfills the newest 80 first-parent commits.
It **does not fetch**: normal dispatcher/operator fetches advance the observed
remote-tracking ref. Uncommitted changes and side-branch commits outside that
first-parent history are not shown. The page names the observed ref and snapshot
generation time; that timestamp is not a claim about remote freshness.

```sh
uv run --extra atlas factory codebase --ref origin/main --limit 200
uv run --extra atlas factory dashboard --codebase-ref origin/main --codebase-limit 200
```

Snapshots and the generated history live in the gitignored `.factory/codebase/`
cache, keyed by commit and extractor version. Extraction failure does not replace
the previously published `history.json`. The full Git lineage is read to retain
rename identities even when the displayed history window is bounded.

**Coverage is explicit, not a completeness guarantee.** Unsupported files remain
inventory-only; unresolved/external relationships are omitted and counted.
Valid manifests without a package identity (such as a Cargo workspace root)
remain inventory-only with coverage warnings. Manifest parse errors still
abort publication and preserve the last good history.
Symlinks, submodules, unsafe paths and vendored/runtime directories are excluded.
When a previously mapped file crosses a coverage boundary, comparison marks a
**coverage change**, not a deletion; its baseline source remains inspectable.
Snapshots are bounded to 5,000 files, 1 MiB per file and 32 MiB total; exclusions
appear in coverage warnings. Preprocessed Fortran is inventory-only rather than
invoking host preprocessing. Shallow history is marked incomplete. Git rename
detection is heuristic: a heavily rewritten move can appear as deletion/addition.
The small relationship diagram shows up to four neighbors; all resolved
relationships for the selection remain in the evidence list.

Design and acceptance criteria: [codebase history plan](docs/codebase-history-plan.md).

## Architecture

`factory/architecture.html` (served by the dashboard at `/atlas`) shows
the system and the ticket lifecycle. Modules map 1:1 to commands:
`triage.py`, `dispatch.py`, `gate.py`, `stats.py`, `dashboard.py`,
`onboard.py`, with `config.py` as the single source of every repo-specific
value.

## License

MIT
```
