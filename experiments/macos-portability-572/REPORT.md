# Ultron one-time portability inventory — #572

## Result

One top-level run of the unchanged configured test gate at **origin/main `3c2c99140113aeae25fffc24b7666c9015428a21`**, exit **1**. No production, test, gate, install, or host configuration changes.

| Measurement | Result |
| --- | --- |
| Host | Ultron; macOS 27.2, build 26B123, arm64 |
| Python | CPython 3.14.8, selected by uv from Homebrew |
| uv | 0.12.23 (Homebrew, aarch64-apple-darwin) |
| Start / finish (UTC) | 2026-11-06T07:36:48.442531+00:00 / 2026-11-06T08:03:48.953797+00:00 |
| unittest duration | 1618.330 seconds |
| Total test methods run | 1522 |
| Passed methods | **1426** |
| Unsuccessful methods | **85**: 73 have failure reports, 16 have error reports, 4 have both |
| unittest failure / error reports | **78 failed, 18 errored** (includes subtests and cleanup errors) |
| Skipped methods | **11**, from unchanged existing suite behavior |

Counts are not interchangeable: `1522 - 78 - 18 - 11 = 1415` is **not** the number of passed methods. A subtest failure and a cleanup error can belong to one method. Deduplicating the 96 outer reports yields 85 unsuccessful methods; `1522 - 85 - 11 = 1426`. Failure-only methods: 69; error-only: 12; both: 4. The exact extracted IDs, subtest labels, outcome types, area assignments and original log-line anchors are in [failures.json](failures.json).

The suite itself launches nested suites in `test_tempdir_hygiene`; those outputs are retained verbatim but excluded from outer totals. No manual rerun, isolated test rerun, skip, xfail, expectedFailure, or gate relaxation was used for this inventory.

## Checkout and command

Cloned `https://github.com/mikeroySoft/factory.git`, main only, into `/private/tmp/factory-572-z2uygc2o/checkout`, outside both the live engine and `/Users/mike/factories/benito-app`. Recorded origin/main and checkout HEAD were the SHA above; final `git status --porcelain=v1 --untracked-files=all` was empty. The checkout was never edited.

The command was read from that checkout's `.factory.toml` `test` check, including its existing `--no-managed-python` option:

```sh
uv run --frozen --no-managed-python python -m unittest discover -s tests
```

Process-local isolation, not repository edits: `XDG_CONFIG_HOME=/private/tmp/factory-572-z2uygc2o/xdg`, `UV_CACHE_DIR=/private/tmp/factory-572-z2uygc2o/uv-cache`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `PYTHONUNBUFFERED=1`; inherited `FACTORY_LIFECYCLE_CONTEXT` removed. HOME, native TMPDIR, platform and PATH were not replaced. The stock fixtures additionally isolate their own host configuration.

## Complete evidence

- [Published summary on #572](https://github.com/mikeroySoft/factory/issues/572#issuecomment-6680417436); issue/comment bodies were read back through the authenticated API.
- [Hosted full log and before/after snapshots](https://gist.github.com/mikeroySoft/faf417ab4344ba6061ef41a06e20c633); hosted suite.log bytes were read back through `gh gist view --raw` and SHA-256 verified.
- [suite.log](suite.log): **491942 bytes**, SHA-256 **`a722bc9d542330634f1503dbfc6acc240b0a260dcfea0635c9184a29cd4cb7f9`**; complete combined stdout/stderr, including nested hygiene output.
- [metadata.json](metadata.json): exact argv, versions, start/end, exit, checkout status, digest and comparison results.
- [before.json](before.json), [after.json](after.json): launchd rows, protected-file digests/modes/mtime, installed-engine manifest/provenance and benito checkout state.
- [failures.json](failures.json): all 96 outer outcome reports, with complete outer tracebacks; hygiene bodies summarize their child exit while the unabridged child logs remain in suite.log.

The remote scratch checkout and evidence directory remain available at `/private/tmp/factory-572-z2uygc2o`. They were not installed or used to launch a service.

## Failure areas and linked tickets

Every outer failure/error has one primary area below; dependency overlaps are described after the table. Existing tickets were augmented with this SHA's names, tracebacks and explicit #571 macOS regression criteria instead of filing duplicates. Newly filed areas: **[#677](https://github.com/mikeroySoft/factory/issues/677)** and **[#678](https://github.com/mikeroySoft/factory/issues/678)**, both `needs-triage`.

| Area | FAIL reports | ERROR reports | Ticket |
| --- | ---: | ---: | --- |
| apfs-filenames | 0 | 2 | [#638](https://github.com/mikeroySoft/factory/issues/638) |
| audit-symlink-cascade | 0 | 2 | [#639](https://github.com/mikeroySoft/factory/issues/639) |
| binding-interpreter | 4 | 0 | [#640](https://github.com/mikeroySoft/factory/issues/640) |
| canonical-paths | 43 | 0 | [#641](https://github.com/mikeroySoft/factory/issues/641) |
| decision-concurrency | 0 | 1 | [#678](https://github.com/mikeroySoft/factory/issues/678) |
| evidence-proc-identity | 2 | 0 | [#626](https://github.com/mikeroySoft/factory/issues/626) |
| hygiene-cascade | 3 | 0 | [#626](https://github.com/mikeroySoft/factory/issues/626) |
| process-lock-identity | 25 | 6 | [#632](https://github.com/mikeroySoft/factory/issues/632) |
| socket-fixtures | 1 | 2 | [#637](https://github.com/mikeroySoft/factory/issues/637) |
| transport-proc-cleanup | 0 | 5 | [#677](https://github.com/mikeroySoft/factory/issues/677) |
| **Total** | **78** | **18** | |

- **Canonical paths (#641, 43 FAIL):** `/var/folders/...` fixture roots differ from `/private/var/folders/...` returned by resolve/git/getcwd; includes workspace's selected-repository refusal and the selected-interpreter diagnostic. Normalize identity comparison boundaries and fixture expectations, retaining rejection of genuinely different roots. The selected-interpreter branch is a source-supported suspected same-family cause, not independently reproduced.
- **Process/lock identity (#632, 25 FAIL + 6 ERROR):** absent `/proc`/machine identity produces unknown liveness and missing/duplicate owner rows. `lifecycle._resource` uses `_host_id() or _UNKNOWN_HOST` while `resources` compares to bare `_host_id()`, making local false when the probe returns None. Use consistent stable native host/boot/process identity. #641 may also affect lexical lock-path comparisons. No independent failure of fcntl was demonstrated.
- **Evidence identity (#626, 2 FAIL):** complete-state observe exits 1 with partial_collection and missing native boot/namespace/host identity. The transport method's assertion truncates output before the error list; its same-source attribution is inference from the shared observe path and existing #626 evidence.
- **Transport cleanup (#677, 5 ERROR across 4 methods):** marker_pids unconditionally enumerates absent `/proc`, including cleanup for non-forking cases. Replace the Linux-shaped fixture with portable test-owned process discovery; preserve descendant/SIGINT assertions. This is separate from production identity handling.
- **Socket fixtures/dual stack (#637, 1 FAIL + 2 ERROR):** `127.0.0.2` bind fails with Errno 49; dual-stack holder detection separately returns PASS instead of FAIL. Use a Darwin-valid unprivileged listener fixture and diagnose wildcard detection without host network changes.
- **APFS filenames (#638, 2 ERROR):** raw 0xff path components fail with Errno 92 before encoding assertions. Use filesystem-independent malformed-byte coverage on APFS, retaining native byte-path coverage where supported.
- **Binding fixture interpreter (#640, 4 FAIL):** fake gh uses `#!/usr/bin/python`; that executable is absent on Ultron. Use the selected test interpreter rather than relaxing production source admission.
- **Audit symlink cascade (#639, 2 ERROR):** the earlier canonical-scope assertion (#641) bypasses target.unlink, leaving a dangling `.factory` symlink; later mkdir calls raise FileExistsError. No evidence requires a case-insensitivity explanation. Verify after normalization and make fixture cleanup unconditional.
- **Concurrent decision lock creation (#678, 1 ERROR):** os.open with O_CREAT and dir_fd raises ENOENT before flock. Root cause unresolved; the nested suite did not reproduce it. Investigate directory-fd/concurrency behavior and create a controlled native reproducer before choosing a fix; do not hide it with blind retries.
- **Hygiene cascades (3 FAIL):** full-suite, killed-gate and smaller-suite wrappers fail at child exit status, before checking tempdir leftovers. These are not three new portability defects and do not establish a tempdir leak. #626 owns full-suite hygiene verification, with #632/#641 and other underlying fixes as dependencies.

Per-area measured evidence comments:
- canonical-paths: https://github.com/mikeroySoft/factory/issues/641#issuecomment-6680335014
- socket-fixtures: https://github.com/mikeroySoft/factory/issues/637#issuecomment-6680335394
- apfs-filenames: https://github.com/mikeroySoft/factory/issues/638#issuecomment-6680335781
- audit-symlink-cascade: https://github.com/mikeroySoft/factory/issues/639#issuecomment-6680336234
- binding-interpreter: https://github.com/mikeroySoft/factory/issues/640#issuecomment-6680336639
- evidence-proc-identity: https://github.com/mikeroySoft/factory/issues/626#issuecomment-6680337075
- process-lock-identity: https://github.com/mikeroySoft/factory/issues/632#issuecomment-6680367214
- Additional workspace/path outcome: https://github.com/mikeroySoft/factory/issues/641#issuecomment-6680367605

### Original renameat2 area

[#532](https://github.com/mikeroySoft/factory/issues/532) is already closed by PR #597 and its fix is present at this measured SHA. No renameat2 import failure occurred. The existing `test_rename_noreplace.RenameNoReplaceTest.test_decisions_imports_and_refuses_overwrite_when_libc_lacks_renameat2` substitutes a libc with neither primitive **before importing factory.decisions**, exercises link/unlink publication, duplicate conflict, unchanged original and temporary cleanup. It is discovered, not decorated to skip, and absent from the failure/error reports. The companion atomic-libc path test is also retained. Explicit #571 macOS regression acceptance is recorded on #532: https://github.com/mikeroySoft/factory/issues/532#issuecomment-6680341452.

No separate systemctl/systemd, cgroup, or fcntl failure was exposed by this run. This is not proof that the corresponding live macOS product paths work; pre-existing service-observation ticket #633 remains separate. The lone concurrent-lock timing observation is #678, not evidence that atomic rename or flock itself failed.

## Live-state invariants

Before capture: `2026-11-06T07:36:47.814504+00:00`. After capture: `2026-11-06T08:03:48.401629+00:00`. All eight comparison groups in metadata.json are true.

Requested `launchctl list | grep factory` rows, before **and** after:

```text
-	1	com.mikeroysoft.factory-benito-app
```

Capture implementation ran `launchctl list` and filtered lines containing `factory` in Python (equivalent to the requested grep); the pipeline text in the snapshot is its display label. Exit 0 and no stderr both times. No load/unload/start/stop/restart or plist write was performed.

The old District engine source path in the ticket is no longer present. The actual launcher resolves to `/Users/mike/.local/share/uv/tools/factory/bin/factory`; installed version is **0.4.7**, with unchanged PEP 610 VCS commit **`1ccadb38845d44ce4961659c32ef33530a34f507`** before/after. All **2323** captured engine-file SHA-256 entries match; `__pycache__` and `.pyc` are deliberately excluded. Launcher, active plist, saved plist and factory config all retain identical resolved path, digest, mode and mtime. No config contents were published, only hashes.

Benito checkout HEAD remains **`1442a3c40d3db506939d24b334e71cb9496b853c`**; tracked diff remains empty, status identical with the same three pre-existing untracked `.factory` runtime files. Those runtime files' contents were not snapshotted; no claim is made about incidental live journal writes. No command ran inside that checkout except read-only git inspection with `GIT_OPTIONAL_LOCKS=0`.

A later native `ps -axo pid=,ppid=,command=` inspection found no command referring to this scratch root. Static inspection of the transport fixture shows any spawned marker child self-exits after 120 seconds; the run finished after 1618 seconds. No blanket process cleanup or signal to a live service was issued.

## Action log and boundaries

1. Recorded attempt plan; read test-gate, fixture-isolation and existing ticket contracts.
2. Resolved verified SSH access; used Ultron's existing authenticated gh for requested publication, without exporting credentials.
3. Read current installed-engine provenance; cloned a separate origin/main checkout and captured baseline.
4. Ran the exact gate command once, retaining full stdout/stderr, versions, SHA and final live-state comparison.
5. Reconciled nested output and per-method counts; read sources to distinguish roots, cascades and unresolved concurrency.
6. Published and hash-verified the complete log/snapshots; filed #677/#678 and added evidence/regression criteria to existing per-area tickets and #532.
7. Committed measurement evidence only; the required local Linux gate and final handoff are recorded separately in `.factory/handoff-572.md`.

No gate or test was changed, no skip/xfail/expectedFailure added, and no production portability fix attempted. Regression implementation belongs to the linked tickets, not this inventory.
