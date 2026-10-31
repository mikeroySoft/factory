I found no blocking defects. The verdict is **APPROVE**, with four nits.

**Spec (#87):**
- **Criterion 1:** met. `capabilities.producers.reader` reports the reader's own build revision and a `verified` flag, plus schema support (`factory/evidence.py:374-397`, `:420`). The revision comes from the directory holding the `factory` package itself (`Path(__file__)…parent`, line 381), not from the selected `--root` checkout. Service identity is not mixed in.
- **Criterion 2:** met. The revision is reported only when it looks like a real commit hash, `evidence.py` is tracked, and the package directory is clean. Otherwise it is explicitly `null` with `verified: false` (`:395-396`). The version string is never used.
- **Criterion 3:** the real JSON CLI (`factory.cli evidence`) is run against a clean committed engine copy (verified, revision matches, and differs from the selected repo's HEAD). It is then run again after a local edit (unknown) (`tests/test_evidence.py:285-312`). Setting `GIT_OPTIONAL_LOCKS=0` stops `git status` from writing to the index (`:382`).

**Standards:** the snapshot has no `AGENTS.md` or `CONTRIBUTING.md`. The new code follows the existing pattern for reporting the revision of the code that is actually running (`dashboard.py:935-940`). The diff adds no new dependencies or abstractions beyond one function.

**Plan drift:** no plan was recorded, so there is nothing to compare against. Both changed files fall inside the issue's "Touches" scope. The gate report lists conflict-markers, test and leak-scan. The issue's "record commands in the PR" requirement can't be checked from the diff.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"tests/test_evidence.py","line":275,"summary":"reader_from skips the before/after self.state() comparison and the 1 MiB stdout bound that invoke() asserts (tests/test_evidence.py:216-218). So the verified and unavailable CLI runs only rely on the audit hook, not on direct proof of #87's 'preserve bounded output and zero operational writes'. Calling invoke() with an env override would cover this."},
{"severity":"nit","pass":"bug","path":"factory/evidence.py","line":381,"summary":"reader_build repeats the inline revision check already in dashboard.py:935-940 (rev-parse HEAD + ls-files --error-unmatch + status --porcelain -- .). Two copies of the same rule can drift, even though the issue asks to reuse existing contracts. Not blocking: the dashboard version is inline, not a reusable function."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":396,"summary":"reader.evidence_schema/runtime_schema are separate literals that repeat producers.evidence_schema/runtime_schema at line 420. The capabilities output now states schema support in two places that could disagree."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":420,"summary":"The new producers.reader field is not documented in the README evidence contract (README.md:1161, :1216 still describe capabilities without build identity), and there is no CHANGELOG 'Unreleased' entry, unlike the other recent (#N) features."}
]}
```
VERDICT: APPROVE
