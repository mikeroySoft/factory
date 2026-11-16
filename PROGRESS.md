# PR694 macOS degraded-observability — progress log

Revision: e7c9698 (detached FETCH_HEAD). Logs: `../factory-pr694-native-20261107-logs/run2/`
(outside the repo so they survive checkout state; `logs/` in-repo is git-excluded and holds the
earlier, interrupted run's artifacts).

## Found on arrival
- Untracked `PROPOSALS.md` (P1: runtime test lines 147-149/152-153/160/282-283) and
  `tests/test_degraded_observability.py` from an earlier interrupted session. Its in-repo
  `logs/baseline-full-suite.log` is truncated (no summary), so a fresh baseline was run.

## Inspection
- `tests/test_lifecycle_platform.py` (15 tests): Darwin process identity via a scripted backend;
  passive projection must never flock; lsof blank lock field (`l `) ⇒ passive `unknown`, while
  `lifecycle._lock_state` (real flock probe = admission safety) still reports `held`.
- `tests/test_darwin_observer.py` (28 + orphan): native backend, ABI gate, caps, continuation hand.
- `runtime_events._Observation.lock_state`: on Darwin holders None ⇒ `("unknown", None)`, never
  `free`. `_resources`: ownership `none` only when state `free`. Already contract-compliant.
- `runtime_local.dispatcher`: capacity `active` from `/proc/locks`; unavailable ⇒ `active: None,
  complete: False` (unavailable is not zero). Compliant.
- `tests/test_runtime.py` 147-149/152-153/160/282-283 assert kernel attribution facts
  (`confirmed`, `active: 1`, `none`, `fresh`) that Darwin cannot prove passively ⇒ fail on this
  host by design. Forbidden to edit ⇒ PROPOSALS.md P1.
- `briefing.execution_evidence`: `lock_held` None (dashboard maps `unknown` resource ⇒ None) with
  no open execution returns `not_running`, letting a model claim `parked`: unknown coerced to free.
- `tests/test_runtime.py` actual failure mode on this host: resources keyed by canonical
  `/private/var/...` while the test indexes `/var/...` (KeyError 147, StopIteration 280/407).
  Diagnostic run with canonical `TMPDIR` leaves only the P1 premise lines failing
  (147 `'unknown' != 'confirmed'`, 282 `'unavailable' != 'fresh'`). Recorded as P0/P1 correction.
- `tests/test_dashboard_passive.py:167/169`, `tests/test_lifecycle_consumers.py:175`: same
  premise as P1 (passive "held"/"free" proof); fail identically with and without my change ⇒ P2/P3.
- `tests/test_evidence.py` ~35 failures: `/private/var` vs `/var` only ⇒ P0. Pre-existing.

## Decisions
- Runtime/lifecycle/observer source is already contract-compliant: Darwin passive `unknown` never
  becomes `free`/`none`; capacity unavailable ⇒ `active: None, complete: False`; admission uses the
  real flock probe (`lifecycle._lock_state`), independent of passive evidence. No change there.
- The one coercion found: `briefing.execution_evidence`. Fixed (commit afa783f): explicit
  `lock_held: None` ⇒ `uncertain`. Missing key keeps old behavior (test_briefing fixtures).
  Linux effect: a Linux ticket whose lock file is absent also has `lock_held: None` (dashboard
  maps `unknown` ⇒ None there too), so its briefing can only say `unknown`, not `parked`. Accepted:
  that is the same unknown-is-not-free rule; no Linux test pins the old behavior.
- Not done (forbidden or needs approval): every test edit in PROPOSALS.md.

## Validation
- Red→green: `tests/test_degraded_observability.py` failed `'not_running' != 'uncertain'`
  before, passes after. Same suites failing before/after the change (diffed failure lists).
- Mutation (configured: `scripts/mutation.py --equivalents mutation-equivalents.json`), log
  `run2/mutation.log`: aborts, exit 1, "baseline suite failed" at
  `test_binding_cli...test_active_contract_drift_and_explicit_new_slice` (`github_unavailable`,
  host env), before sampling any mutant. Fetched `origin/main` (read-only; HEAD unchanged) so
  the tool could compute its diff. Config not edited.
- Supplementary manual mutants on the changed lines (`run2/mutation-manual-briefing.log`,
  script `run2/mut_manual.sh`, unmutated harness green): 4/4 killed.
- Full suite, baseline (started at e7c9698, `run2/baseline-full.log`, 1797s): outer run
  1607 tests, failures=61, errors=17, skipped=11 (log also contains the nested
  `test_tempdir_hygiene` child run; that is why two `Ran 1607` lines appear).
- Full suite, final at afa783f (`run2/final-full.log`, ids in `run2/final-ids.json`, runner
  `run2/run_suite.py` = plain discover + id dump, nothing filtered): 1607 tests, failures=61,
  errors=18, skipped=11. Normalized-id diff against baseline:
  fixed `test_degraded_observability...uncertain_not_idle`; new
  `test_dashboard_passive...holds_journal_exclusive` ("journal holder did not acquire LOCK_EX",
  subprocess-startup timing under a 30-min loaded run): passes 3/3 in isolation, does not
  reach briefing.py ⇒ flake, not a regression. The error-count delta is subtest-row variance in
  the same tests.
- Guarantee suites (`run2/guarantee-suites.log`): test_darwin_observer 28/28 (both orphan tests
  included), test_lifecycle_platform 15/15, test_degraded_observability 3/3. No skips/xfails.
- Remaining failures are pre-existing host issues outside this contract (the /var symlink,
  gh/systemd/port availability), plus the P1–P3 premise lines that need human approval.
- Two-failure pivot rule: not triggered. Each failure was resolved or classified on its first
  diagnosis.

## Run 3 (PM rulings, Nov 7 2026 4:54 PM PST): logs in `../factory-pr694-native-20261107-logs/run3/`

Every command was run through `run3/stamp.sh`, which records rev, tree, `git status --short`,
the command and the exit code before and after.

### STOPPED: items (1) and (2), all approved test edits (fake-gh shebang, P0, temp-root, P1, P2)
The ruling says to use the documented manager AUTHORIZE / scoped-override mechanism and to stop
if it cannot enforce the intended scope. It cannot. Evidence:
- The documented mechanism is the `factory-add-only-override` **ticket label**
  (README.md:357-358, 736-740, 1056-1066; skills/factory/SKILL.md:104-108).
- `manage.py:510-517` AUTHORIZE posts a comment and adds that one label. Nothing about paths,
  files or hunks is recorded.
- The gate turns the label into the boolean `--add-only-override` (`gate.py:634`).
  `check_protected_paths` (`gate.py:279`) then accepts deleted lines in **every** add-only
  path (`tests/**`). It does the same for the bug-ticket function check (`gate.py:709`).
  So the grant covers all existing tests, not just the reviewed hunks.
- The mechanism also only works through a GitHub ticket and labels. That is outside this
  local-only, no-push workspace.
Nothing was forced another way. No test file was edited and gate config is unchanged. That
means the mutation run "at the new head" is not possible: there is no new head.
Needed to unblock: a scope-enforcing grant, or a PM ruling that accepts the label's
all-of-`tests/**` breadth for this change. Either one has to come with a ticket I can use.

Prepared for the unblock (verified, not applied):
- Fake-gh interpreter: with the test's restricted PATH (`<bin>:/usr/bin:/bin`), `python3`
  resolves to `/Applications/Xcode.app/.../python3` 3.9.6. The suite runs under Homebrew
  3.14. So `#!/usr/bin/env python3` is **not** the intended interpreter. The fix should write
  `'#!' + sys.executable` into the generated shebang (`tests/test_binding_cli.py:19/97`).

### Done
- (3) `run3/P3-replacement.diff` (unapplied). It first asserts cardinality 3 and the
  canonical path set {gpu.lock, .factory/locks/merge.lock, .factory/locks/9.lock}. On Darwin it
  then asserts that the 9.lock row is `state`/`ownership` unknown, `owner` None,
  `attribution` unavailable, `holder_pids` None and `observation` unavailable, and is not
  free/held/none. On other platforms it keeps the four original `held` lines, moved one level
  in under `else:` with their text unchanged. Shapes come from `run3/probe_p3.py` /
  `probe_p3.log`. Checked in a throwaway worktree (since removed): module 6/6 OK
  (`run3/p3-draft-scratch-check.log`). [INFERENCE] The cardinality 3 on Linux is not
  observed (no Linux run allowed).
- (5) `run3/receipt-corrections.md`.
- (6) `run3/journal-lock-comparison.log`: whole `PassiveDashboardTest` class, same order, at
  e7c96983 (separate worktree `/Users/mike/factory-pr694-baseline-e7c9698`) and at 1a37558.
  The results match: `holds_journal_exclusive` ok at both, and
  `reuses_passive_lock_evidence` FAIL at both at :167 (`None is not True`, the P2 premise).
  Both sides used `python3 -m unittest tests.test_dashboard_passive.PassiveDashboardTest`
  (Homebrew 3.14), so the comparison is like for like. The worktree was removed afterwards.
- (4) `run3/portability-contract.md`. Every top-level failing id is classified as CAP, FIX, DEF?
  or UND, with evidence from stamped per-module runs (`run3/remaining-modules.log`, configured
  `uv run … unittest discover -s tests -p …`). No group is waived. Open diagnoses: dashboard_port
  dual-stack, the test_decisions concurrent-apply ENOENT, HostConfig interpreter-path
  canonicalization (suspected defect), tempdir_hygiene and the journal flake.
- Process failure, recorded: the first per-module pass used `python3 -m unittest tests.<m>`.
  That has no project env and uses package imports, so it produced false failures (`tomlkit`,
  `test_mcp` import). I renamed it to `SUPERSEDED-…` and re-ran with the configured invocation.
  This failure had a different premise from the override blocker, so it does not trigger the
  two-failure pivot rule.
## Run 4 (PM rulings, Nov 7 2026 5:39 PM PST): logs in `../factory-pr694-native-20261107-logs/run4/`

All test/lint commands ran through `run4/stamp.sh` (rev, tree, `git status --short`, command, exit
before and after).

### A) P3: kept as a patch only
`run4/P3-replacement.diff` is a byte copy of `run3/P3-replacement.diff`. Not applied; waits for B review
and PM blob approval.

### B) Done: commit `19457e8` "gate: content-pinned --add-only-allow PATH=BLOB (local only)"
- `factory/gate.py`: repeatable `--add-only-allow PATH=BLOB` and required `--add-only-allow-base COMMIT`.
  - Parse at entry (`parse_add_only_allow`): path must be canonical (no `..`, `.`, empty segment,
    leading `/`, backslash), BLOB must be a full 40/64-hex id; a duplicate path or missing base is
    an argparse error.
  - `_verify_allows`, called from `check_protected_paths`, refuses unless:
    - COMMIT equals `merge-base(base, HEAD)`;
    - path matches `add_only_paths` and not `protected_paths` (case-sensitive);
    - the raw diff entry is `M` with an unchanged 100644/100755 mode (so delete, rename via
      `--no-renames`, type change, symlink and mode change are refused);
    - numstat is not binary;
    - the working tree path is a regular file (not a symlink);
    - both `HEAD:path` and the working-tree bytes (`git hash-object --no-filters`) equal BLOB.
  - Any refusal fails `protected-paths` (`add_only_paths` reason) for the whole run.
  - Unlisted add-only files keep full protection.
  - `check_existing_tests(base, exempt)` exempts only test functions whose path is in the allow map,
    and only after protected-paths verified it. Other files' functions still fail `protected-tests`.
- Not wired to the dispatcher, manager or labels, and not invoked on the current edits.
- Tests: `tests/test_add_only_allow.py`, 13 tests. Red first (`B-tdd-red.log`: unrecognized
  arguments), then green (`B-tdd-green.log`, `B-verify.log`: 23 OK together with
  `test_add_only_override`).
  - The exact change passes; without the allow it fails.
  - These fail: an unrelated edit, an unrelated additive function change (`protected-tests`), a
    one-byte drift, a stale blob, a base mismatch, a dirty-worktree substitution, an approved
    worktree over an unapproved commit, a symlinked worktree file, malformed or out-of-scope entries,
    and an out-of-scope test file.
  - These fail even when the blob matches: delete, rename, mode change, binary change, and a symlink
    in the commit.
- Mutation check in a scratch worktree, since removed. Disabling each guard (worktree hash, committed
  hash, base, symlink, structural, binary, exempt) made a named test fail. The scope guard survived
  the first round, so `test_allow_cannot_exempt_a_test_outside_add_only_scope` was added.
- Gate suites (`B-verify.log`): the only failures are the 3 known CAP ids from the run3 contract:
  the non-UTF-8 filename test and `gate_resources` ×2. ruff is clean. The `agents_map` test passes.
### C) Patches only (not applied): `run4/approved-patches/` 01–05, manifest `run4/blob-hashes.txt`
- Patches, in apply order:
  - 01 fake-gh shebang `'#!' + sys.executable`. At HEAD the shebang was `#!/usr/bin/python`.
  - 02 P0.
  - 03a–f temp-root fix: evidence_transport, factory (ConfigTest ×3 and doctor_json only), dispatch_lifecycle,
    mcp, prime_pilot, workspace.
  - 04 P1, which applies on top of 02.
  - 05 P2.
- Each file in the manifest has a HEAD blob and a cumulative post-edit blob. `git hash-object` and `--no-filters` agree.
- Patched module runs (`C-patched-modules.log`):
  - Pass: binding_cli, runtime, dispatch_lifecycle, mcp, prime_pilot, workspace, dashboard_passive.
  - The remaining failures are known or newly unmasked:
    - factory `selected_interpreter…` is a DEF (D item 3).
    - evidence_transport has 6 errors from the `/proc` helper (CAP).
    - NEW: evidence `complete_local_state…` and evidence_transport `complete_page…` fail with `ok:false`
      because of `lsof` `locks_unavailable`. This is the same passive-lock CAP premise as P1/P2, and the
      run3 temp-root guess was wrong. These two failures share one premise, so this item STOPPED under
      the two-failure rule.
  - Needed: a PM ruling like P1/P2 for these two tests.

### D) Drafts only (not applied)
- `run4/capability-diffs/`:
  - D1 native_capability: Darwin branch with a real flock child. It checks unavailable≠0, contention,
    `MAX_ACTIVE=1` admission and reopen.
  - D2a/D2b non-UTF-8: a portable git-index test, plus a Darwin native assertion of EILSEQ.
  - D3 dashboard_port 127.0.0.2: a portable fake-socket test, plus a Darwin native assertion of
    EADDRNOTAVAIL.
  - The Linux bodies are kept; the Linux branches were not run here [INFERENCE].
  - There is no skip or xfail (`skip-grep.txt`). Post-edit blobs are in NOTES.md.
- `run4/UND-findings.md` reclassifies:
  - dual-stack: DEF. With `SO_REUSEADDR`, binding 127.0.0.1 beside a `[::]`/`0.0.0.0` holder succeeds on
    macOS. Production fails safe because `ss` is missing. PM must decide whether the macOS port check is
    in scope.
  - decisions concurrent apply: Darwin `openat(O_CREAT)` race returns ENOENT. Factory does not tolerate
    it, so it is a DEF. A retry on ENOENT took failures from 81/200 to 0/200.
  - HostConfig interpreter: path-spelling DEF that is not macOS-specific. A canonical TMPDIR would hide it.
  - tempdir_hygiene: failures are propagated child failures only, with no leak.
- The brief's `-k` before `discover` form exits 2. The agents used `discover … -k`.

### E) Open
- The journal-lock cause stays OPEN (unproven).
- Historical commit provenance stays INFERRED.
