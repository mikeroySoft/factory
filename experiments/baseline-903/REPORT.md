# #903: matched baseline attribution investigation

## Contract and provenance

Investigation only; no runtime/configuration/authentication changes. The target is
80% causal attribution, not a passing launch probe. Denominators: matched solo
1633.528 − 738.778 = **894.750 s** (the earlier #899 prose says 894.751, a
0.001 s rounding discrepancy); historical approximately **1797 s**. The binding
brief also quotes 2497 − 701 = 1796 s. Use 1797 s consistently for the acceptance
threshold, **1437.6 s**, without treating the 1 s historical rounding as a cause.

Source: `69b50b7de964e09ab429cb68b067299ad06c3cec`. No #899 attempt-two commits
were merged. Preserved evidence remains at the #899 worktree, with SHA256
inventory in `raw/preserved-899-sha256.txt`; historical selected receipts copied
verbatim to `raw/historical-executions-899.json`. These receipts do not include
baseline-specific start/end events. The prior synthetic run's completion JSON
did not capture its archived SHA. Its 909.443 s delta is not historical causal
attribution and must not be added to a platform cost as if independent.

The immutable #899 raw artifacts remain addressable without merging its commits:
`git show 9c675e435ec8825ce301e9af74ce31319c49279b:experiments/baseline-899/raw/solo-fixed.json`,
and the same prefix with `tank-provisioned-result.json`, `concurrent.json`,
`profile-899.py.txt`, or `historical-executions.json`. The earlier attempt's
evidence commits `cccd8b07`, `579a419f`, `759a0c99`, and `9c675e43` are provenance,
not fixes or completed acceptance.

## Safety and action log

1. Read ub-mode and repository operating contract; wrote immutable attempt plan.
2. Located preserved #899 artifacts rather than restarting discovery. Host is
   Ultron, 20 logical cores (16 performance, 4 efficiency), 128 GiB memory.
3. Hashed `/Users/mike/.config/factory/config.toml` without displaying contents.
   Initial digest: `e071ebe909c9b2c1fea62bad01545406307952ae664416029dea9361444f026b`.
   No config or auth files were opened for content inspection or edited.
4. Recovered Tank via existing trusted `rocm-tank` SSH alias at `100.98.172.98`;
   neither `tank` nor `tank.lan` resolves. Tank has 32 available cores; its config
   hash is `8c275d1b92b178dd38d8250a308861216d3867bc7dc32ee5db95999e79c83389`.
5. Verified actual Ultron runtime/test/script/lock blobs against the source tree
   (`raw/source-verification.json`). The retained shared clone's empty index
   makes ordinary diff status misleading; actual files, not index, were checked.
6. Launched matched parent-process profiles using `profile.py`, isolated HOME
   and XDG_CONFIG_HOME, explicit UV_PYTHON and shared UV_CACHE_DIR. This captures
   aggregate parent `_users` time and direct child launch/lifetime costs. It does
   **not** instrument grandchildren or separate interpreter startup from work
   performed inside a Python child. Inclusive profiles cannot be added together.
7. The first Tank profile is invalid: instrumentation passed a bytes executable
   into pathlib, causing TypeError at test 830 after 235.842 s. Raw artifacts are
   under `raw/tank-invalid/`. Corrected instrumentation decodes executable names
   with os.fsdecode; no consumer code/test was changed.

8. The second Tank profile is also invalid: `time.monotonic` was mocked with a
   finite sequence by a mutation-proof test; instrumentation consumed entries
   and raised StopIteration at test 1451 after 500.870 s. `raw/tank-fixed/`
   preserves that failure. The third script captures the real clock before
   tests can patch the time module. The first Ultron profile likewise failed on
   the bytes-path instrumentation at test 829, 644.729 s; see
   `raw/ultron-invalid/` and `raw/ultron-invalid.log`.

## Exact profile commands

Ultron (output redirected to `raw/solo.log`; scratch path saved locally):

```sh
scratch=$(mktemp -d /private/tmp/factory-903-home.XXXXXX)
mkdir "$scratch/config"
HOME="$scratch" XDG_CONFIG_HOME="$scratch/config" \
 UV_CACHE_DIR=/Users/mike/.cache/uv \
 UV_PYTHON=/Users/mike/.local/share/uv/tools/factory/bin/python \
 /private/tmp/factory-899-mutenv.iGXmKr/bin/python -I -B \
 experiments/baseline-903/profile.py \
 /private/tmp/factory-899-solo.CYBxyt/tree \
 /private/tmp/factory-899-mutenv.iGXmKr/lib/python3.12/site-packages \
 "$PWD/experiments/baseline-903/raw/solo"
```

Tank preparation uses `git archive 69b50b7d | tar -xf - -C "$base/tree"`,
a shared no-checkout clone's `.git`, and `git read-tree 69b50b7d` (scratch only).
The final script is copied through SSH stdin to `/tmp/factory-903/profile-clock.py`.

```sh
ssh -o BatchMode=yes -o HostKeyAlias=rocm-tank mike@100.98.172.98 '
 base=$(cat /tmp/factory-903/latest)
 cd "$base/tree"
 HOME="$base/home" XDG_CONFIG_HOME="$base/home/config" \
 UV_CACHE_DIR=/home/mike/.cache/uv \
 UV_PYTHON=/home/mike/.local/share/uv/tools/factory/bin/python \
 /home/mike/.local/share/uv/tools/factory/bin/python -I -B \
 /tmp/factory-903/profile-clock.py "$base/tree" \
 /home/mike/.local/share/uv/tools/factory/lib/python3.12/site-packages \
 "$base/results-clock"'
```

## Resource and log observations

`raw/solo-telemetry.txt` records `memory_pressure -Q`, `sysctl vm.swapusage`,
`vm_stat`, `iostat -w 10 -c 7`, timestamped Factory/Benito log tails and lock
mtimes. At 01:42:47–01:43:48 UTC on November 24: 89–92% CPU idle in the six
interval samples, disk0 1.53–5.83 MB/s; zero swap, unchanged compressions
3788682 and decompressions 1080347, 95% system-wide memory free. The first
cumulative-since-boot iostat row is not an interval measurement.

Benito's passes through 17:34:29 PST show active tickets 0 and empty frontier.
Factory's tail shows this worker and held/skipped other tickets. Persistent lock
mtimes do not prove lock-held intervals. No Benito gate overlap is established.
Historical #899 receipts establish #888 mutation and #890 full-suite overlap
11:59:56.970–12:46:05.337 UTC, 2768.367 s; this is check overlap, not a measured
baseline-only overlap. Historical memory and lock-delay shares remain unknown.

## Passing matched-source profiles

| Host | Wall s | unittest s | Tests | Skips | Result |
|---|---:|---:|---:|---:|---|
| Ultron | 1662.389399 | 1661.590 | 2272 | 12 | PASS |
| Tank | 665.283540 | 664.592 | 2272 | 4 | PASS |

Raw files: `raw/solo/`, `raw/solo.log`, `raw/tank/`. Both use the corrected
clock-capturing profiler; Python 3.12.13 on Ultron and 3.12.14 on Tank. Current
wall difference is **997.105859 s**, not a replacement for the binding brief's
894.750 s earlier pair. Tank was not proven machine-idle; its initial load
average was 12.30 on 32 cores. These are single runs with platform-specific skips.

| Parent-process observation | Ultron | Tank | Difference s |
|---|---:|---:|---:|
| `_users` inclusive seconds | 96.958 (549 calls) | 32.465 (552 calls) | 64.493 |
| Python launches | 1362 | 1364 | — |
| Python Popen-constructor seconds | 3.838574 | 0.342803 | 3.495771 |
| Python inclusive lifetime seconds | 1200.429415 | 685.973100 | 514.456315 |
| uv launches | 11 | 11 | — |
| uv Popen-constructor seconds | 0.050949 | 0.002805 | 0.048144 |
| uv inclusive lifetime seconds | 3.569507 | 1.690413 | 1.879094 |
| git launches | 21943 | 22133 | — |
| git Popen-constructor seconds | 66.835362 | 4.995889 | 61.839473 |
| git inclusive lifetime seconds | 423.786034 | 37.222877 | 386.563157 |

Ultron additionally made 514 lsof launches (72.522813 s inclusive lifetime)
and 551 ps launches (24.674915 s); Tank made no lsof launches and 37 ps
launches (0.406150 s). These corroborate aggregate scratch-check cost rather
than extrapolating the earlier loaded 325–540 ms launch probes. The measured
parent `_users` delta is only **7.21% of 894.750 s** and **3.59% of 1797 s**.
It does not include scratch checks inside child interpreters.

The constructor timer measures Popen setup/spawn, **not Python initialization**.
Child lifetimes include imports, application work, sleeps, possible concurrent
children, and time until observed wait completion. They are not exclusive wall
partitions. Do not add the lifetime rows, `_users`, subprocess.run, selector
poll time, or constructor time: these overlap. No historical causal share is
assigned from this table.

Parent cProfile shows subprocess.run inclusive 1219.953 s on Ultron versus
301.803 s on Tank. Poll time is nested under that, not another slowdown.
The three deliberate raw-interrupt windows total 180.852 versus 180.280 s;
their absolute 180 s is common-mode, not an explanation of the gap. Nested
tempdir-hygiene suite costs 156.441 versus 47.004 s, but its child work is
already inside the Python lifetime row. Main-thread cProfile does not profile
other Python threads or grandchildren. Full text and direct-child records are
preserved; `summarize.py` reproduces the basename aggregates.

The second Ultron instrumentation run is invalid too: 1451 tests, 1132.859 s,
StopIteration from the mocked clock (`raw/ultron-clock-invalid*`). Tracebacks
can display a later version of the script line after the on-disk script was
corrected while the old process was already running. Only the final two passing
profiles use the captured-clock implementation retained in `profile.py`.

## Prepared-copy I/O

`prepare.py` executes sixteen `git clone --quiet --shared --no-checkout`
operations followed by the producer's `shutil.copytree(..., ignore=COPY_IGNORE,
dirs_exist_ok=True)`. It does not perform sampling, proof exclusion, fingerprints
or baseline execution. Raw per-copy times: `raw/ultron-preparation.json` and
`raw/tank-preparation.json`.

| Host | 16 clones s | 16 copytrees s | Total s | Parent block reads/writes |
|---|---:|---:|---:|---|
| Ultron | 0.597010 | 2.877328 | 3.474338 | 0 / 0 |
| Tank | 0.100486 | 0.510927 | 0.611413 | 0 / 0 |

These are warm-cache measurements, not physical disk service-time measurements.
The producer starts `baseline_started` after all copies have been prepared
(`scripts/mutation.py:594–619` at the measured source). Their **direct share of
the reported baseline timer is zero**; no claim excludes secondary cache effects.
The 2.862925 s preparation difference affects the mutation deadline, not the
baseline gap. Parent cProfile copytree time *inside tests* is 0.954 s Ultron
versus 0.176 s Tank; it is distinct from producer preparation.

Commands (with the scratch HOME/XDG and cache settings above):

```sh
/private/tmp/factory-899-mutenv.iGXmKr/bin/python -I -B \
 experiments/baseline-903/prepare.py /private/tmp/factory-899-solo.CYBxyt/tree
# Tank, after copying prepare.py through the existing SSH connection:
/home/mike/.local/share/uv/tools/factory/bin/python -I -B \
 /tmp/factory-903/prepare.py /tmp/factory-903/matched.AaPsnt/tree
```

## Real full-suite overlap protocol and invalid first run

`overlap.py` launches the same profiled baseline, waits 337.038 s (the
historical check-entry offset), then launches a real `factory gate` on a separate
same-source clone. Only ruff is skipped; the configured full `test` suite is
not replaced by a sleep or mutant saturation. Each process has separate HOME
and XDG_CONFIG_HOME. The gate owns its separately provisioned `.venv`, so uv
cannot mutate the baseline interpreter environment. No host config or timeout
is changed.

The first load attempt failed before running tests: its scratch clone's local
origin could not be inferred as a GitHub slug. Thus `raw/overlap-invalid/`
is **invalid as overlap evidence**, despite a passing baseline (1664.256313 s,
2272 tests, 12 skips). `raw/overlap-invalid-telemetry.txt` belongs to that
failed-load window and must not be described as concurrent-suite telemetry.
It supplies a second passing isolated baseline, only 1.866914 s above the
first. The correction sets only the disposable clone's Git origin to the
existing repository URL, `https://github.com/mikeroySoft/factory.git`; no
committed/host TOML is edited.

```sh
base=$(mktemp -d /private/tmp/factory-903-overlap.XXXXXX)
git clone -q --shared --no-checkout /Users/mike/factories/factory "$base/gate"
git -C "$base/gate" checkout -q --detach 69b50b7d
git -C "$base/gate" remote set-url origin "$(git remote get-url origin)"
mkdir -p "$base/home/config"
HOME="$base/home" XDG_CONFIG_HOME="$base/home/config" \
 UV_CACHE_DIR=/Users/mike/.cache/uv UV_PROJECT_ENVIRONMENT="$base/gate/.venv" \
 uv sync --project "$base/gate" --frozen --no-managed-python \
 --python /Users/mike/.local/share/uv/tools/factory/bin/python
HOME="$base/home" XDG_CONFIG_HOME="$base/home/config" \
 /private/tmp/factory-899-mutenv.iGXmKr/bin/python \
 experiments/baseline-903/overlap.py /private/tmp/factory-899-solo.CYBxyt/tree \
 "$base/gate" "$base/results-fixed"
```

The actual retained scratch base is `/private/tmp/factory-903-overlap.A83aOt`.
This is a workload-type and entry-offset replay, not an exact replay of
historical #888/#890 sources, environments or baseline-specific intervals.

