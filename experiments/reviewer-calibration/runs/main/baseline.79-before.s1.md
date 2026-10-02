## Findings

**1. `github()` now fails the whole full snapshot on any GraphQL `errors` entry — regression for existing consumers.**
`factory/dashboard.py:220-221` raises `RuntimeError("GitHub query was incomplete")` whenever the response carries `errors`. The removed code (`-` side of the same hunk) returned `json.loads(proc.stdout)["data"]`, tolerating partial data (common: upstream repo NOT_FOUND, sub-field permission errors, rate-limit on one connection). `snapshot()` catches this at `factory/dashboard.py:828` and drops all issues/PRs. Issue §Scope: "Preserve the existing `pr.checks`, `pr.comments`, … and current snapshot fields for their existing consumers." The new transport changes legacy behavior outside the feedback path. Also `factory/dashboard.py:216` replaces the stderr diagnostic with a fixed string, so the existing "Snapshot collection errors" source loses its content.

**2. `producer.revision` can be an unrelated repository's HEAD.**
`factory/dashboard.py:875-878` runs `git rev-parse HEAD` / `git status --porcelain -- .` with `cwd` = the installed `factory/` package directory and no check that the Git toplevel actually contains the package. For a `uv tool`/pipx install located under any ancestor Git repo (e.g. a dotfiles repo at `$HOME`), this returns a clean, 40-hex, fully "verifiable" SHA that has nothing to do with Factory source. `factory/feedback.py:77` accepts it unconditionally. Issue §Normative schema 1: `revision` must be "a verifiable source/build revision when available, otherwise null". This yields false provenance, not null. Failure semantics of `sh()` on non-repo paths are not visible in the diff `[INFERENCE]`.

**3. Full-snapshot cadence not preserved.**
`factory/dashboard.py:883-893` invokes `feedback.collect` serially for every selected issue with a PR; each collection issues up to 8 `gh` subprocesses (2 head, ≤2 reviews, ≤2 threads, 2 checks: `factory/feedback.py:213,236,264-265,297`) with a 30 s group deadline. `snapshot()` backs the 15 s HTTP cache and `/api/snapshot`. Issue §Existing read-surface integration: "Preserve full-snapshot cadence". No batching or per-snapshot bound is introduced; one slow PR blocks the entire board for up to 30 s, N PRs for 30·N s.

**4. Exit gate 4 regression tests absent.**
`tests/test_factory.py:2397-2448` (new `FeedbackSnapshotTest`) and `tests/test_feedback.py` contain no assertion that `factory dashboard --runtime-json` performs no feedback/GitHub read and keeps its schema, and no test that `<img onerror=...>` body renders literally. Issue §Exit gate 4 names both explicitly. The `--runtime-json` path is untouched by the diff, but the required boundary test is not delivered.

**5. Dead code from incomplete wiring.**
`factory/dashboard.py:124` adds `id` to the repository GraphQL query and `factory/dashboard.py:812` declares `repo: dict = {}`, but nothing assigns `repo` from `data`; `repo.get("id")` at `factory/dashboard.py:886` is always `None`. Collection still recovers the ID via `HEAD_QUERY` (`factory/feedback.py:160-161`), so behavior is correct, but the query field and variable are dead. Non-blocking.

**6. Gate report relabeled "legacy".**
`factory/dashboard.html:1257` renders the deterministic gate as "Legacy local gate context/failures". README ("the dispatcher re-runs it as the evidence of record") and the issue both keep the gate as current authority; only verdict comments are legacy. Misleading presentation; non-blocking.

## Verified OK
- `source_revision` field set matches spec exactly (`factory/feedback.py:23-25,195`); `observation_id` excludes timestamps/URLs/order (`factory/feedback.py:402-404`).
- Head race → all sources partial, both SHAs in reason, relevance `unknown`, final head retained (`factory/feedback.py:321-329,383-387`).
- One failed source does not discard others; sanitized fixed error messages (`factory/feedback.py:118-126`).
- Factory review requires lifecycle `result` with `returncode==0`, `parsed`, `head==actual_head`, enter/exit framing (`factory/feedback.py:347-357`); prose `VERDICT` never becomes `factory_review`.
- Briefing appends feedback after existing candidates and reports omission/shortening (`factory/briefing.py:288-289,333-337,363-371`).
- HTML renders body via `pre()` and links via `safeSourceURL` (`factory/dashboard.html:1083,1084`).

VERDICT: REVISE
