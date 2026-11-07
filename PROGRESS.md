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
