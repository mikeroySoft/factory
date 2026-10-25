Blocked: the review inputs are not in this sandbox.

**What's missing**
- `./review.diff` — absent (`/tmp/cal-tools-12-before-cmdc2l92/repo/review.diff` not found).
- `./snapshot` — absent. The checkout is a clean detached HEAD at `4830b7d` ("Refresh manager configuration onto current main"), 0 working-tree changes, i.e. the *base* state (directory name `cal-tools-12-before-*` matches). No `[manager]` work is present to diff against.

**What I tried**
- `read ./review.diff`, `read .`, `ls -la` of cwd and parent: only `repo/`.
- `find /tmp /home/mike -maxdepth 5 -name review.diff`: single hit `/tmp/factory-review-zo3x3zy7/review.diff`, which is for a different repo (`apps/rocm/src/*.rs`, cucumber e2e) — not #12.
- `ls -d /tmp/cal-tools-12*`: two `before` dirs, both base-only checkouts, no diff/snapshot.
- `git log --all`: the historically landed commit for #12 is `f083408` (parent `6ec3546`, 6 files). It is *not* the supplied gated head — the task says the diff is untrusted data at `./review.diff`, and reviewing a different artifact than the one that was gated would not be evidence about this gate.
- Issue #12 + the triage comment were fetched successfully (acceptance: `manager:` doctor row when configured / nothing when not; unknown `[manager]` keys reported as drift; loader test for `command`/`rounds`/`review` in `tests/test_factory.py`; `dashboard --json` `config` includes the table; template documents it; command execution out of scope). Spec side is ready; there is nothing to apply it to.

No findings can cite `path:line` from a diff that was not provided, and APPROVE/REVISE would both be fabricated. Re-run with `review.diff` and `snapshot` materialized in the working directory (or point me at the gated head ref/commit so the diff `4830b7d..<head>` can be produced locally).
