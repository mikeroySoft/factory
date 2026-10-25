## Review: 76a1a7a..19bd468 — `evidence: expose verifiable reader build and schema identity`

Inputs: `./review.diff` and `./snapshot` were absent; reviewed `git diff 76a1a7a 19bd468` (merge-base origin/main..gated HEAD) in the checkout. No AGENTS.md/CONTRIBUTING.md in repo; conventions taken from README.md evidence contract and docs/c1-read-interface-*.md.

### Spec (issue #87)

- AC1 "build revision and supported schema separate from repository revision and service identity" — met. `factory/evidence.py:374-397` reports `producers.reader = {revision, verified, evidence_schema, runtime_schema}` from the engine's own checkout (`Path(__file__).resolve().parent`), not `--root`; repository identity stays in `scope`/`head_sha`, service state stays in `runtime_local` output.
- AC2 "unavailable provenance explicitly unknown; never fabricated from version string or checkout HEAD" — met. `evidence.py:392-396`: revision emitted only when `rev-parse HEAD` is a SHA **and** `ls-files --error-unmatch evidence.py` succeeds **and** `status --porcelain -- .` is empty; otherwise `revision: null, verified: false`. No fallback to a version string or `--root` HEAD. Consistent with README.md:824 ("clean source-checkout Git revision, otherwise null, never a CLI version").
- AC3 "exercise actual JSON CLI for verified and unavailable provenance; bounded output, zero operational writes" — met. `tests/test_evidence.py:292-311` runs `python -B -m factory.cli evidence` against a committed engine copy (verified, revision ≠ `self.head`) then a locally-modified copy (`False, None`). `GIT_OPTIONAL_LOCKS=0` (`evidence.py:382`) prevents index refresh writes; all git calls bounded by `COMMAND_SECONDS` and errors collapse to unknown (`evidence.py:385-390`).

### Standards

- Read-only/bounded conventions (README.md:108, docs/c1-read-interface-ticket.md:37-39) honoured: no network, no state creation, timeouts on every subprocess.
- Existing `producers.evidence_schema`/`runtime_schema` keys preserved (`evidence.py:420`); no consumers broken (none found outside evidence.py/tests).

### Plan drift

- Plan not recorded → no named-file baseline. Files changed: `factory/evidence.py`, `tests/test_evidence.py` only.
- Gate report: test PASS covers the CLI exercise (tests spawn the real CLI). Issue also asks to "record commands and observed results in the PR"; PR body not available here — unverified, not a code defect.

### Required fixes

None.

### Optional suggestions

1. **Duplicate logic.** `evidence.py:384-395` reimplements the exact three-command clean-checkout check already inline at `factory/dashboard.py:935-940` (rev-parse / ls-files --error-unmatch / status --porcelain). docs/c1-read-interface-ticket.md:41 asks for one owner of shared read logic. Extracting one helper (dashboard.py's `sh` has no timeout; the new one does) would let both use the bounded version. Not blocking: dashboard.py is an overlapping-writer surface and the issue scoped "no automatic scope changes".
2. **Test helper duplication drops checks.** `tests/test_evidence.py:275-283` `reader_from` copies `invoke` (`test_evidence.py:205-218`) but omits the `self.state()` before/after and `len(stdout) < 1_048_576` assertions that `invoke` applies. An `engine=` parameter on `invoke` would exercise AC3's "bounded output and zero operational writes" on the verified/unavailable runs too. The implementation satisfies the bound regardless (`bounded_output` at `evidence.py:1157`), and the first assertion block (`test_evidence.py:286`) goes through `invoke`.
3. **Docs.** README.md:1161 and :1216-1218 document `capabilities`/`producers` contents and don't mention `producers.reader`; CHANGELOG.md `## Unreleased` has no entry. Recent feature commits (76a1a7a) updated both. No written rule mandates it, so not blocking.
4. `evidence_schema`/`runtime_schema` now appear both in `producers` and `producers.reader` (`evidence.py:396-397`, `:420`). Fine for compatibility; drop the outer pair only if a later schema bump wants one source of truth.

VERDICT: APPROVE
