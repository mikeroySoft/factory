**Standards.** No AGENTS.md/CONTRIBUTING.md in snapshot. `reader_build` reuses the existing `git -C`/`GIT_OPTIONAL_LOCKS=0`/`COMMAND_SECONDS` pattern and the existing `SHA` regex; only deviation is that `github_read` clamps each subprocess to `min(deadline, now+COMMAND_SECONDS)` (`snapshot/factory/evidence.py:300`) while the new helper uses a fixed 20 s per call (review.diff:22). README `capabilities` description (`README.md:1216-1218`) and CHANGELOG `Unreleased` (every feature there carries `(#N)`) weren't updated — convention, not documented rule.

**Spec (#87).**
- AC1: `producers.reader = {revision, verified, evidence_schema, runtime_schema}` (review.diff:31-32,44) is distinct from `scope.root`/service identity. Met.
- AC2: revision only when module is tracked, HEAD is a full SHA, and `factory/` subtree is clean; otherwise `revision:null, verified:false` (review.diff:27-31). Nothing derived from `factory.__version__` or `--root` HEAD; `ls-files --error-unmatch` guards the venv-inside-project-checkout case where `git -C site-packages/factory rev-parse HEAD` would otherwise return the operator repo's HEAD. Met.
- AC3: real CLI exercised for clean-committed (verified, revision ≠ `self.head`) and locally-modified (unverified) engines (review.diff:67-93). Bounded-output/no-write assertions come only from the first `invoke()` call; `reader_from` (review.diff:57-65) checks just the audit hook. Met, with a nit.

**Plan drift.** Plan status not recorded → no named file list to compare. Files changed: `factory/evidence.py`, `tests/test_evidence.py`; both inside the issue's "Touches" boundary. Planned proof: gate `test: PASS`; the issue's "record commands and observed results in the PR" isn't visible from the diff. No drift-exposed defect.

```json
{"verdict":"APPROVE","findings":[
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":22,"summary":"git helper uses fixed COMMAND_SECONDS per call (3 calls, up to 60 s) instead of clamping to the read deadline like github_read (evidence.py:300); if stdin arrives late the documented 90 s whole-read ceiling (README.md:1254) can be exceeded before the post-hoc check at evidence.py:1134. One-line fix: take `deadline` and use `timeout=max(0, min(deadline - time.monotonic(), COMMAND_SECONDS))`."},
{"severity":"nit","pass":"spec","path":"factory/evidence.py","line":44,"summary":"New `producers.reader` field not reflected in README capabilities description (README.md:1216-1218) or CHANGELOG Unreleased (repo convention: one entry per feature with `(#87)`)."},
{"severity":"nit","pass":"bug","path":"tests/test_evidence.py","line":61,"summary":"`reader_from` re-implements `invoke` without its <1 MiB stdout and root-state-unchanged assertions; AC3 bounded-output/zero-write proof for the engine scenario rests only on the Python audit hook, which cannot observe git subprocess writes. Letting `invoke` accept an env override would reuse the full checks."},
{"severity":"nit","pass":"bug","path":"tests/test_evidence.py","line":78,"summary":"copytree copies `factory/__pycache__` into the temp engine and `git add .` commits it (no .gitignore in the temp repo); harmless under `-B` but could be excluded with `ignore=shutil.ignore_patterns('__pycache__')`."}
]}
```
VERDICT: APPROVE
