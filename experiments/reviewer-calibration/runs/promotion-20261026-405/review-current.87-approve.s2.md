I found no important defects, so the diff can be approved. The gate report is the authority for build and test status. I did not run builds or tests.

**Spec (#87):**
- **Separate build identity: met.** `capabilities().producers.reader` (`factory/evidence.py:374-397`, `:420`) reports the engine's own revision and supported `evidence_schema`/`runtime_schema`. That revision comes from git run in the package directory, not from the `--root` checkout or the version string. The capabilities output has no service identity field, so nothing can be mixed up with it.
- **Unknown provenance: met.** The revision is reported only when it is a valid SHA, `evidence.py` is tracked, and `git status --porcelain -- .` shows nothing for the package directory. Otherwise the output is `revision: null, verified: false`.
- **Real CLI, verified and unknown cases, bounded output, no writes: met.**
  - `tests/test_evidence.py:275-312` runs `python -m factory.cli evidence` from a clean committed copy of the engine and checks `verified: true` with that commit's SHA.
  - After a local edit to the copy, it checks `verified: false` with a null revision.
  - Both runs use the existing audit hook that blocks writes.
  - `GIT_OPTIONAL_LOCKS=0` stops `git status` from writing the index.
  - The emitted revision is at most 64 characters.

**Standards:** The repo has no AGENTS.md or CONTRIBUTING.md. The docs don't require a CHANGELOG or README update for an added capabilities field. Most earlier features did add an `Unreleased` CHANGELOG entry, so its absence is listed only as a nit.

**Plan drift:** No plan was recorded, so I can't list files the plan didn't name. Both changed files, `factory/evidence.py` and `tests/test_evidence.py`, are the evidence/CLI area the issue's "Touches" section names. The issue asks for the actual CLI commands and their results to be recorded in the PR. The gate report doesn't include them, but `tests/test_evidence.py:275-312` exercises that CLI scenario. This gap doesn't reveal a required defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"bug","path":"factory/evidence.py","line":386,"summary":"reader_build adds its own subprocess.run git runner. It inherits stdin, captures stdout/stderr without a byte limit, and does not set GIT_TERMINAL_PROMPT=0. The existing fixed-query runner (runtime_local._command) does all of these: DEVNULL stdin, discarded stderr, COMMAND_BYTE_LIMIT, sanitized env. No concrete failure with these read-only git commands, but it is a second convention for local commands."},
{"severity":"nit","pass":"bug","path":"factory/evidence.py","line":397,"summary":"The literals evidence_schema:1/runtime_schema:1 now appear in both reader_build() and producers (line 420). Two copies of the supported-schema value can drift apart; one shared constant would remove the duplication."},
{"severity":"nit","pass":"spec","path":"tests/test_evidence.py","line":306,"summary":"assertNotEqual compares against self.head, an earlier commit, not the selected root's current HEAD (self.main). It always passes because the two repos differ. The assertEqual at line 305 already proves the revision does not come from --root, so this line adds no protection and its comment is misleading."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":420,"summary":"The new public capabilities field producers.reader (revision/verified/schemas) has no CHANGELOG Unreleased entry and no README evidence-contract note. Most earlier features added one; no documented rule requires it."}
]}
```
VERDICT: APPROVE
