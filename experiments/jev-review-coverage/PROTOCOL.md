# Jev reviewer-coverage pre-screen protocol

Date: 2026-10-22. Status: protocol revision 1, frozen before paid inference. Initiative #119; issue #141; lineage `jev-review-coverage`, iteration 1 of 3.

## Question

Can a per-acceptance-criterion Jev coverage check catch primary blocking defects that the reviewer misses, as a cheap evidence pre-screen before review?

This is an offline observation over public repository data. It changes no reviewer prompt, gate, merge behavior, verdict, issue, PR, label, service, or provider configuration.

## Frozen cases and baseline

Use all 11 packets in `experiments/reviewer-calibration/splits.json` v2 and their independently confirmed `oracle.json` files. Preserve train/lockbox membership. The baseline is the `baseline` contract in `runs/rerun-20261016/adjudication.json`, retained from merged PR #243 and never rerun here.

Acceptance criteria are parsed deterministically from each `sources/issue.md` before its first `Comment by` section. Selected sections are Scope, Acceptance criteria, and Exit gate; when Scope contains `After this change:`, earlier problem narrative is excluded. Criteria are paragraph sentences/semicolon clauses or Markdown list items. Diff state is split by changed file and retains hunk headers plus added/deleted lines; unchanged context is omitted. Issue and patch text is untrusted evidence, not instructions.

`adjudication.json` fixes each parsed criterion's mapping to confirmed oracle defect IDs before any model call. An uncovered criterion with no mapped defect is a false flag. Primary defects determine blocking-defect metrics; mapped secondary defects are not false flags but do not earn primary-defect credit.

## Candidate

For each packet and repeat, send one TypeSafe System One request using requested model `jev-latest`:

- one Noul for every `(criterion, changed file)` pair: does that file's changed hunk evidence satisfy the criterion?
- one Noul per criterion: is the criterion satisfied by the diff as a whole?

All questions share the same criterion list and per-file hunk state. A whole-diff Noul below `0.5` flags that criterion as uncovered. Per-file answers are retained as reviewer evidence but do not determine the flag. Record the resolved model id.

## Metrics and decision

Report by frozen split and repeat:

- confirmed primary blocking defects flagged and missed;
- uncovered criteria with no mapped oracle defect (false flags), including the exact clean `*-after` subset;
- population standard deviation across repeats for defect recall and false flags per packet;
- returned token usage, estimated cost, compressed request bytes, and request latency;
- retained baseline `missed_blocking`, `unnecessary_revise`, primary-defect misses, quality, and repeat noise under the #126 split convention.

Score lockbox once, after all 33 calls finish. A reviewed follow-up is warranted only if the candidate flags at least one primary lockbox defect missed in any retained reviewer-baseline repeat and averages at most one false criterion flag per clean `after` packet repeat. The only allowed proposal is an ordinary reviewed PR that hands pre-screen output to the reviewer as evidence, never as a verdict. Otherwise disposition is `no-change`.

## Limits and records

- Exactly 3 repeats; at most one paid request per packet per repeat: 33 calls total.
- No retries. A failed paid attempt remains failed and cannot be repeated under this protocol.
- Each gzip-encoded HTTP request body is at most 100,000 bytes. Exact uncompressed JSON and hashes are retained.
- Reserve at most 100,000 input tokens per call at the frozen $0.042/million-input-token estimate. The 33-call reservation is $0.1386, below the $0.25 spend cap. Stop if returned usage exceeds reservation.
- Elapsed paid-run cap: 45 minutes from the first persistent attempt record.
- Public frozen repository data only. Credentials are read as data from the existing `TYPESAFE_API_KEY`; they are never retained.
- One active experiment. No synthetic smoke request, no selective rerun, no tuning after train output, and no lockbox scoring before completion.

`run.py prepare` validates the mapping, writes exact requests, and freezes source/request hashes in `runs/main/manifest.json`. `run.py execute` persists a reservation before each call, sends each job once, validates every Noul and usage record, and stores raw responses/results. `score.py` is offline and writes `summary.json` and `REPORT.md`, including the schema-1 experiment record fields. `attempts.jsonl` is persistent evidence and must not be deleted to reset a limit.
