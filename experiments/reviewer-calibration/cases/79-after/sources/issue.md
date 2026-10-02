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
