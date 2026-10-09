"""Report-only mutation testing of changed `factory/` files (pr-tier gate check).

Mutates the Python files under `factory/` that changed in `origin/main...HEAD`, runs
the full `python -m unittest discover -s tests` suite against each mutant, and prints
every survivor as `path:line: <mutation>`. Exit 0 whenever the trial ran, whatever
survived; nonzero only when the tool itself cannot run (no git, failing baseline,
cosmic-ray exception). Mutants are sampled deterministically (sorted, then shuffled
with a fixed seed, capped at CAP) and no mutant starts after a time budget derived from
TARGET, so the run always finishes inside `[gate].timeout`.

Each worker runs in its own copy of the tree because cosmic-ray mutates files on disk;
the worktree itself is never touched.
"""

from __future__ import annotations

import collections
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CAP = 200
SEED = 197
TARGET = 600  # seconds the whole check aims to finish in
WORKERS = min(16, os.cpu_count() or 1)
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", ".factory", "__pycache__", ".ruff_cache")


def changed_lines() -> dict[str, set[int]]:
    """Changed `factory/*.py` files (added or modified) -> line numbers added or modified."""
    out = subprocess.run(
        ["git", "diff", "--unified=0", "--diff-filter=AM", "origin/main...HEAD", "--", "factory/*.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    lines: dict[str, set[int]] = {}
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            lines[path] = set()
        elif line.startswith("@@"):
            start, _, count = line.split()[2].lstrip("+").partition(",")
            lines[path].update(range(int(start), int(start) + int(count or 1)))
    return lines


def gate_timeout() -> int:
    try:
        return int(tomllib.loads(Path(".factory.toml").read_text()).get("gate", {}).get("timeout", 1200))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return 1200


def sample(lines: dict[str, set[int]], scratch: Path) -> list:
    """Deterministic mutant sample: mutants on changed lines first, then the rest of each
    file; sorted, then shuffled with a fixed seed within each group, capped at CAP."""
    import cosmic_ray.commands
    from cosmic_ray.work_db import use_db

    with use_db(scratch / "session.sqlite") as db:
        cosmic_ray.commands.init([Path(p) for p in lines], db, {})
        items = list(db.work_items)

    def key(m):
        return (str(m.module_path), m.start_pos, m.end_pos, m.operator_name, m.occurrence)

    def hot(m):
        return not lines[str(m.module_path)].isdisjoint(range(m.start_pos[0], m.end_pos[0] + 1))

    mutations = sorted((item.mutations[0] for item in items), key=key)
    rng = random.Random(SEED)
    groups = [[m for m in mutations if hot(m)], [m for m in mutations if not hot(m)]]
    for group in groups:
        rng.shuffle(group)
    return (groups[0] + groups[1])[:CAP], len(groups[0])


def test_command(copy: Path) -> str:
    return f"env -C {copy} PYTHONPATH={copy} {sys.executable} -m unittest discover -s tests"


def describe(diff: str) -> str:
    lines = diff.splitlines()
    old = next((x[1:].strip() for x in lines if x.startswith("-") and not x.startswith("---")), "")
    new = next((x[1:].strip() for x in lines if x.startswith("+") and not x.startswith("+++")), "")
    return f"`{old[:80]}` -> `{new[:80]}`"


def main() -> int:
    lines = changed_lines()
    if not lines:
        print("no mutable files")
        return 0
    from attrs import evolve
    from cosmic_ray.mutating import mutate_and_test
    from cosmic_ray.work_item import TestOutcome

    gate = gate_timeout()
    root = Path.cwd()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="factory-mutation-") as tmp:
        scratch = Path(tmp)
        mutations, hot = sample(lines, scratch)
        workers = max(1, min(WORKERS, len(mutations)))
        copies = [scratch / f"copy{i}" for i in range(workers)]
        for copy in copies:
            shutil.copytree(root, copy, ignore=COPY_IGNORE)
        print(f"files: {', '.join(lines)}")
        print(f"mutants: {len(mutations)} sampled (seed {SEED}, cap {CAP}), "
              f"{min(hot, len(mutations))} on changed lines first, {workers} workers")

        # Baseline once, unmutated, in the first copy: proves the suite passes under this
        # harness and sizes the per-mutant timeout. unittest reports on stderr, so merge it.
        proc = subprocess.run(shlex.split(test_command(copies[0])), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=TARGET, check=False)
        baseline = time.monotonic() - started
        if proc.returncode:
            print(f"baseline suite failed:\n{proc.stdout[-3000:]}")
            return 1
        # No mutant starts after `budget`, so the run ends around TARGET seconds and a
        # hung mutant still cannot push it past the gate's check timeout.
        budget = min(max(TARGET - baseline, baseline), gate // 2)
        timeout = min(max(60.0, 3 * baseline), gate - budget - 60)
        print(f"baseline: {baseline:.0f}s, per-mutant timeout {timeout:.0f}s, budget {budget:.0f}s")

        queue = collections.deque(mutations)  # popleft is atomic under the GIL
        results = []

        def worker(copy: Path) -> None:
            while time.monotonic() - started < budget:
                try:
                    mutation = queue.popleft()
                except IndexError:
                    return
                local = evolve(mutation, module_path=copy / mutation.module_path)
                results.append((mutation, mutate_and_test([local], test_command(copy), timeout)))

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(worker, copies))

    survivors = [(m, r) for m, r in results if r.test_outcome == TestOutcome.SURVIVED]
    for mutation, result in sorted(survivors, key=lambda mr: (str(mr[0].module_path), mr[0].start_pos)):
        print(f"{mutation.module_path}:{mutation.start_pos[0]}: {mutation.operator_name} "
              f"{describe(result.diff or '')}")
    counts: dict[str, int] = {}
    for _, result in results:
        name = (result.test_outcome or result.worker_outcome).value
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"
    print(f"ran {len(results)} mutants in {time.monotonic() - started:.0f}s: {summary}; "
          f"{len(queue)} not run (time budget); {len(survivors)} survived")
    return 0


if __name__ == "__main__":
    sys.exit(main())  # an uncaught exception (tool failed to run) is the only nonzero exit
