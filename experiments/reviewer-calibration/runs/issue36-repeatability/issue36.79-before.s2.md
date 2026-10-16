# Review: #79 B2 — source-versioned PR feedback

Conventions checked: README.md only (no AGENTS.md/CONTRIBUTING.md at this head). Gate (conflict-markers/test/leak-scan) PASS is taken as given.

## Required fixes

### R1. `producer.revision` can report a foreign repository's HEAD or crash the full snapshot
`factory/dashboard.py` (snapshot, provenance block):
```
source_root = Path(__file__).resolve().parent
revision = sh(["git", "rev-parse", "HEAD"], cwd=source_root).strip()
if sh(["git", "status", "--porcelain", "--", "."], cwd=source_root).strip():
    revision = None
```
Runs `git rev-parse HEAD` from the installed package directory with no check that the package is tracked by that repository.
- Trigger A: Factory installed into a `.venv` inside any git repository (README's own `uv sync` flow, or a user project venv). `git rev-parse HEAD` succeeds and returns the *enclosing* project's HEAD; `.venv` is gitignored so `status --porcelain -- .` is empty and the value is kept. Result: `producer.revision` is a valid-looking 40-hex SHA that is not the Factory source revision.
- Trigger B: `uv tool install` / `pipx` location outside any git repo. `git rev-parse` exits nonzero. Whether this aborts `snapshot()` depends on `sh()`'s failure semantics, which are outside the diff `[INFERENCE]`; there is no `try`/fallback here, and this runs before every ticket is built.
- Rule: issue §Normative schema 1, `producer.revision`: "a verifiable source/build revision when available, otherwise null". A SHA from an unrelated repository is not verifiable, and it is also what `feedback.sha()` hashes into nothing — the collector cannot detect the mismatch.
- Fix: require `git rev-parse --show-toplevel` to equal `source_root.parent` and `git ls-files --error-unmatch factory/feedback.py` to succeed (both under the same cwd); on any failure or mismatch set `revision = None`. Wrap in the same `except` discipline as the journal read.

### R2. Exit gate 4 regression boundary for `--runtime-json` is not in the diff
Issue §Exit gate 4: "Add focused regression boundaries: … `factory dashboard --runtime-json` performs no feedback/GitHub read and its schema stays unchanged". The diff adds `tests/test_feedback.py`, `tests/test_factory.py::FeedbackSnapshotTest`, and three briefing tests; none exercises `--runtime-json`, and nothing asserts `feedback.collect` is not reached on that path (`tests/test_factory.py` diff, `tests/test_feedback.py` diff). The implementation itself only touches `snapshot()` (`factory/dashboard.py` diff), so the behavior is probably correct, but the acceptance criterion names the test. If an existing pre-diff test already proves zero subprocess/`gh` invocation on that path, cite it in the handoff; otherwise add one that patches `feedback.collect` and `dashboard.github` with `side_effect=AssertionError` and runs the runtime projection.

## Optional suggestions

- `factory/dashboard.py` `github()`: `raise RuntimeError("GitHub read failed")` replaces `proc.stderr.strip()`. The feedback collector already sanitizes independently (`feedback.py` `request()` maps every exception to fixed text), so this change isn't needed for the credential rule, and it removes the auth/rate-limit diagnostics operators previously saw in `snapshot()["errors"]` ("github: …"). Consider keeping stderr for the legacy no-argument call and sanitizing only when `endpoint`/`query` is supplied.
- `factory/dashboard.py` snapshot: `repo: dict = {}` is declared but no assignment appears in the diff; if the unchanged lines don't set `repo = data["repository"]`, the new `id` in `GRAPHQL` is unused and `repository.id` is only ever populated by `HEAD_QUERY` (which works, via `repository['id'] not in (None, …)`). Verify; drop the dead query field if so.
- Full-snapshot cost: `feedback.collect` issues up to 6 `gh` subprocesses per PR ticket, serial, with a 30 s detail budget each (`factory/feedback.py` `request()`/`DETAIL_TIMEOUT`; `factory/dashboard.py` loop over `issues`). With 20 open PR tickets a snapshot is ~2 min worst-case against a 15 s HTTP cache. The spec mandates per-PR reads, so not blocking, but record measured snapshot latency in the handoff.
- `factory/feedback.py`: `from factory import lifecycle` then `lifecycle._lifecycle(e)` — private symbol coupling across modules. Either expose it or accept the coupling explicitly in a comment.
- `factory/feedback.py` `item()`: on `source_identity_conflict` the earlier item is removed but `counts[source]` is not decremented; the retained count can under-fill by one relative to `ITEM_LIMIT`. Harmless, but `truncated` reasoning slightly over-reports.
- `factory/feedback.py` checks loop: `row.get('html_url', row.get('target_url'))` returns `None` when `html_url` is present-but-null; use `row.get('html_url') or row.get('target_url')`.
- `factory/feedback.py` ownership: `claimed` matches `e.get('event') == 'claimed' and e.get('ticket') == issue['number']`. The only test fixture for this is hand-written (`tests/test_feedback.py` `factory_events()`); confirm the real dispatcher `claimed` row uses key `ticket`, otherwise every PR is `unverified` in production.
- `factory/dashboard.html` `taskFacts`: relabeling the local gate facts as "Legacy local gate …" and the verdict chips to a neutral `chk` class goes beyond "task facts … coverage presentation only" in §Touches; the gate report is not part of the feedback contract. Not a rule violation, but it is unrequested editorializing of unrelated facts.
- `tests/test_briefing.py` fixture: `review_state: "changes_requested"` (lowercase) and owner evidence `"branch agent/7"` contradict the schema the producer emits (uppercase provider values; branch text is explicitly non-evidence). Tests pass because `sources_for` doesn't interpret these, but the fixture will mislead the next reader; mirror a real `collect()` output.
- Exit gate 4's `<img onerror=…>` literal-render assertion has no automated check in the diff; the browser smoke (gate 5) is the stated evidence — ensure the handoff records it.

VERDICT: REVISE
