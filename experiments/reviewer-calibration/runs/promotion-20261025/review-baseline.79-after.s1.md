## Review: #79 B2 source-versioned PR feedback

Sources used: issue body + 2 comments (agent brief; escalation comment carries a truncated handoff example, no approved scope change recorded), `review.diff`, snapshot helpers (`dashboard.py:169 sh`, `dashboard.html:453-466 h`, `:938 pre`, `:1228 safeSourceURL`, `briefing.py:149 bounded_file`, `lifecycle.py:452 _lifecycle`, `test_runtime.py:34-48` network guard).

### Standards

No `AGENTS.md`/`CONTRIBUTING.md` in the snapshot; `docs/` holds plans only; `pyproject.toml:36-38` configures ruff line-length/target only; CI gate is `python -m unittest discover -s tests` (`.github/workflows/ci.yml:12`) — gate PASS.

- Existing patterns reused, not duplicated: transport via extended `github()` (`dashboard.py` diff 510-537), provenance via `briefing.bounded_file` (diff 565), lifecycle validation via `lifecycle._lifecycle` (diff 1023), DOM via `h`/`pre`/`safeSourceURL` (diff 312-317, 381). Body text reaches the DOM only as text nodes (`h` → `createTextNode`, `dashboard.html:464`).
- No documented-rule violations found.

### Spec (issue #79 normative schema + exit gate)

Satisfied, with diff evidence:
- Envelope keys/enums/nullability: `feedback.py` diff 711-719; `pr.state` normalized to the four allowed values (714-715, 991-992).
- `evidence_id` namespace provider/host/repo-id/PR-id/kind/source-id, missing identity → gap not invention: 807-809, 829.
- `source_revision` over exactly the listed fields: 644-646, 861; `observation_id` over native IDs, final head, sorted pairs, coverage status/truncated/reason, excluding `observed_at`: 1055-1057.
- Bounds as constants: 635-639; two pages per connection (881, 912), one check-run page + one status page (955-957), 20 KB body (816-818), ≤32 errors (736), 30 s group deadline (721, 750-755).
- Initial/final head reads, race → all coverage partial with both SHAs, relevance unknown, final head kept: 878, 986-997, 1039-1043.
- Outdated thread never current via SHA equality: 1044-1047. Missing source SHA never filled from head: 824-826, 972 (statuses use the queried SHA, which is the commit they are attached to — correct, not a fill).
- Factory review requires valid lifecycle result with `returncode==0`, `parsed`, `head==actual_head`, matching enter/exit: 1023-1037. Provenance-free `VERDICT` is excluded (test diff 1492-1497).
- One-source failure keeps independent facts (test 1554-1568); sanitized messages are static strings (760-762).
- Runtime-JSON path untouched (`dashboard.py:1228-1254` never calls `snapshot()`); existing `test_runtime.py` guard already asserts no `gh`/network on that path.
- Briefing: feedback appended after existing candidates (diff 156-215), omissions/shortening reported (219-247), coverage notice distinguishes missing / unsupported version / incomplete / not-collected (160-205). Earlier human decisions retained under budget pressure (test 1222-1233).
- HTML: Review drawer + Inbox raw evidence render links, source vs observed head, relevance, disposition, location, coverage, errors (diff 321-389, 393, 448); legacy verdicts relabelled non-authoritative (401-406, 425).
- README/CHANGELOG document schema location, bounds, unknown/partial, read-only authority (diff 9, 21-105).

### Plan drift

Plan status: not recorded → no plan file to diff against. Against the issue's **Touches** list, every changed file is named; `tests/test_feedback.py` is permitted conditionally ("only if smaller and clearer") — 334 lines, fixtures shared with `test_factory.py` (diff 1259, 1331); acceptable judgment.

Proof not present in the gate report: exit-gate 5 (real `python -m factory dashboard --json`, live server + browser inspection of Review drawer/Inbox, `sources_for` bundle) and exit-gate 7 (handoff with sanitized full example). The issue's escalation comment shows a truncated handoff example but no browser observation record. This is handoff evidence outside the diff; not a diff defect.

### Required fixes

None.

### Optional suggestions

1. **Scope narrowing not in the issue** — `dashboard.py` diff 607 `collect_details=raw.get("state") == "OPEN"` / `feedback.py` 745-748: closed and merged PRs never get detail reads. The issue defines `pr.state` incl. `closed|merged` and never authorizes skipping them; it does say "preserve full-snapshot cadence", which this serves (avoids up to 8 `gh` calls × 100 PRs per refresh). The returned object is honest (`not_collected`, all sources `unavailable`), so no false-empty. Flag to the #15 owner at handoff so the consumer does not expect merged-PR evidence.
2. **Per-refresh cost** — each open agent PR adds up to 8 `gh` subprocesses and a 30 s ceiling to synchronous `snapshot()` (`feedback.py` 721, 744-763). Spec-mandated, but worth stating in the handoff for dashboard latency expectations.
3. `briefing.py` diff 194: `feedback["errors"] or []` iterated without a list check while `items` is checked at 173; a non-list `errors` would raise. Unreachable from this producer; one `isinstance` if you want symmetric hardening.
4. `feedback.py` 866-871: on identity conflict the earlier item is removed but `counts[source]` is not decremented, so the cap can trigger one item early. Negligible; note only.

VERDICT: APPROVE
