"""Mutation testing of changed `factory/` files for the pr-tier gate.

By default, mutates the Python files under `factory/` changed in
`origin/main..HEAD`. ``--working-tree`` instead selects committed, staged, dirty,
and untracked changes against the `origin/main` tip, and fails incomplete
trials. The full discovered unittest suite runs against each mutant (related tests
first, stopping at the first failure), and every survivor prints as
`path:line: <mutation>`. The default report-only mode exits zero after a complete
trial even when mutants survive. ``--blocking`` exits nonzero for completed
survivors on changed lines. Tool and baseline failures always exit nonzero.
``--report`` mirrors stdout to a durable report with a terminal worker status.
Mutants are sampled deterministically (sorted, then shuffled with a fixed seed,
capped at CAP). One absolute deadline, `[gate].timeout` - 60, capped by
`FACTORY_WORKER_DEADLINE`, covers preparation, the baseline and every mutant.
Working-tree runs reserve 60 seconds of that worker deadline for the final commit,
handoff, and exit.

Each worker runs in its own copy of the tree because cosmic-ray mutates files on disk;
the worktree itself is never touched.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CAP = 200
SEED = 197
FINISH_RESERVE = 60
WORKERS = min(16, os.cpu_count() or 1)
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", ".factory", "__pycache__", ".ruff_cache")


def changed_lines(*, working_tree: bool = False) -> dict[str, set[int]]:
    """Changed `factory/*.py` files (added or modified) -> added or modified lines."""
    revision = "origin/main" if working_tree else "origin/main..HEAD"
    out = subprocess.run(
        ["git", "diff", "--src-prefix=a/", "--dst-prefix=b/", "--unified=0",
         "--no-renames", "--diff-filter=AM", revision, "--", "factory/*.py"],
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
    if working_tree:
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "--", "factory/*.py"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        for path in untracked:
            lines[path] = set(range(1, len(Path(path).read_text().splitlines()) + 1))
    return {path: changed for path, changed in lines.items() if changed}


def on_changed_line(mutation, lines: dict[str, set[int]]) -> bool:
    return not lines[str(mutation.module_path)].isdisjoint(
        range(mutation.start_pos[0], mutation.end_pos[0] + 1),
    )


def gate_timeout() -> int:
    try:
        return int(tomllib.loads(Path(".factory.toml").read_text()).get("gate", {}).get("timeout", 1200))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return 1200


def sample(lines: dict[str, set[int]], scratch: Path) -> tuple[list, int]:
    """Deterministic mutant sample: mutants on changed lines first, then the rest of each
    file; sorted, then shuffled with a fixed seed within each group, capped at CAP."""
    import cosmic_ray.commands
    from cosmic_ray.work_db import use_db

    with use_db(scratch / "session.sqlite") as db:
        cosmic_ray.commands.init([Path(p) for p in lines], db, {})
        items = list(db.work_items)
    source = {path: Path(path).read_text().splitlines() for path in lines}

    def key(m):
        return (str(m.module_path), m.start_pos, m.end_pos, m.operator_name, m.occurrence)

    def applicable(m):
        # cosmic-ray 8.7.0's ExceptionReplacer crashes on a dotted name (`config.ConfigError`)
        # in an except clause; its span is then not a bare identifier.
        if not m.operator_name.endswith("ExceptionReplacer"):
            return True
        (row, start), (_, end) = m.start_pos, m.end_pos
        return source[str(m.module_path)][row - 1][start:end].isidentifier()

    mutations = sorted((item.mutations[0] for item in items if applicable(item.mutations[0])), key=key)
    rng = random.Random(SEED)
    groups = [
        [m for m in mutations if on_changed_line(m, lines)],
        [m for m in mutations if not on_changed_line(m, lines)],
    ]
    for group in groups:
        rng.shuffle(group)
    return (groups[0] + groups[1])[:CAP], len(groups[0])


def test_command(copy: Path, module: str | None = None) -> str:
    command = [
        "env", "-C", str(copy), f"PYTHONPATH={copy}", "PYTHONDONTWRITEBYTECODE=1",
        sys.executable, "-m", "scripts.mutation", "--test-module", module or "",
    ]
    return shlex.join(command)


def run_tests(module: str) -> int:
    """Fail fast, but require the entire discovered suite to declare a survivor.

    The feature map only changes ordering, never which tests run. Keeping cases
    from each module/class together preserves unittest's shared fixture lifetime.
    """
    related = set()
    for line in Path("AGENTS.md").read_text().splitlines():
        if f"`{module}`" in line:
            related.update(re.findall(r"`tests/(\w+)\.py`", line))

    def cases(suite):
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                yield from cases(test)
            else:
                yield test

    def priority(test):
        name = type(test).__module__.rsplit(".", 1)[-1]
        return (0 if name.startswith("test_risk_") else 1 if name in related else 2, name)

    suite = unittest.TestSuite(sorted(
        cases(unittest.defaultTestLoader.discover("tests")), key=priority,
    ))
    return 0 if unittest.TextTestRunner(failfast=True, verbosity=0).run(suite).wasSuccessful() else 1


def describe(diff: str) -> str:
    lines = diff.splitlines()
    old = next((x[1:].strip() for x in lines if x.startswith("-") and not x.startswith("---")), "")
    new = next((x[1:].strip() for x in lines if x.startswith("+") and not x.startswith("+++")), "")
    return f"`{old[:80]}` -> `{new[:80]}`"


class _Tee:
    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        self.flush()
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocking", action="store_true")
    parser.add_argument("--working-tree", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--test-module", default=None)
    args = parser.parse_args(argv)
    if args.report is None:
        return _run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w") as report, contextlib.redirect_stdout(_Tee(sys.stdout, report)):
        try:
            status = _run(args)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            print(f"mutation check failed: {type(error).__name__}: {error}")
            status = 1
        print(f"worker mutation: {'PASS' if status == 0 else 'FAIL'}")
        return status


def _run(args: argparse.Namespace) -> int:
    if args.test_module is not None:
        return run_tests(args.test_module)
    deadline = time.monotonic() + max(1, gate_timeout() - 60)
    worker_deadline = os.environ.get("FACTORY_WORKER_DEADLINE")
    if worker_deadline is not None:
        reserve = FINISH_RESERVE if args.working_tree else 0
        deadline = min(deadline, float(worker_deadline) - reserve)
    started = time.monotonic()
    budget = max(0, deadline - started)
    lines = changed_lines(working_tree=args.working_tree)
    if not lines:
        print("no mutable files")
        return 0
    from attrs import evolve
    from cosmic_ray.mutating import mutate_and_test
    from cosmic_ray.work_item import TestOutcome
    root = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="factory-mutation-") as tmp:
        scratch = Path(tmp)
        mutations, hot = sample(lines, scratch)
        if args.blocking:
            mutations = mutations[:hot]

        def baseline_timed_out() -> int:
            print(f"baseline suite did not finish before the {budget:.0f}s deadline")
            print(f"incomplete: {len(mutations)} mutants not started and 0 cut short by the "
                  f"{budget:.0f}s deadline")
            return 1

        workers = max(1, min(WORKERS, len(mutations)))
        copies = [scratch / f"copy{i}" for i in range(workers)]
        for copy in copies:
            # Isolate Git metadata, but overlay current files rather than checking out HEAD:
            # baselines must include uncommitted edits/deletions and untracked files too.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return baseline_timed_out()
            try:
                subprocess.run(
                    ["git", "clone", "--quiet", "--shared", "--no-checkout", str(root), str(copy)],
                    check=True, timeout=remaining,
                )
            except subprocess.TimeoutExpired:
                return baseline_timed_out()
            shutil.copytree(root, copy, ignore=COPY_IGNORE, dirs_exist_ok=True)
        print(f"files: {', '.join(lines)}")
        print(f"mutants: {len(mutations)} sampled (seed {SEED}, cap {CAP}), "
              f"{min(hot, len(mutations))} on changed lines first, {workers} workers")

        # Baseline once, unmutated, in the first copy: proves the suite passes under this
        # harness and sizes the per-mutant timeout. unittest reports on stderr, so merge it.
        baseline_started = time.monotonic()

        remaining = deadline - baseline_started
        if remaining <= 0:
            return baseline_timed_out()
        try:
            proc = subprocess.run(shlex.split(test_command(copies[0])), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, check=False,
                                  timeout=remaining)
        except subprocess.TimeoutExpired:
            return baseline_timed_out()
        baseline_finished = time.monotonic()
        if baseline_finished >= deadline:
            return baseline_timed_out()
        baseline = baseline_finished - baseline_started
        if proc.returncode:
            print(f"baseline suite failed:\n{proc.stdout[-3000:]}")
            return 1
        timeout = max(60.0, 3 * baseline)
        print(f"baseline: {baseline:.0f}s, per-mutant timeout {timeout:.0f}s, "
              f"{deadline - baseline_finished:.0f}s left before the deadline")

        queue = collections.deque(mutations)  # popleft is atomic under the GIL
        results = []
        cut = []  # mutants whose run the deadline cut short: neither killed nor survived

        def worker(copy: Path) -> None:
            # Most mutants die before a full baseline-length run. Use the remaining
            # budget, but never count a deadline-cut run as killed or survived.
            while (left := deadline - time.monotonic()) > 0:
                try:
                    mutation = queue.popleft()
                except IndexError:
                    return
                local = evolve(mutation, module_path=copy / mutation.module_path)
                result = mutate_and_test(
                    [local], test_command(copy, str(mutation.module_path)), min(timeout, left),
                )
                if left < timeout and result.output == "timeout":
                    cut.append(mutation)
                else:
                    results.append((mutation, result))

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(worker, copies))

    survivors = [(m, r) for m, r in results if r.test_outcome == TestOutcome.SURVIVED]
    hot_survivors = [
        (mutation, result) for mutation, result in survivors
        if on_changed_line(mutation, lines)
    ]
    for mutation, result in sorted(survivors, key=lambda mr: (str(mr[0].module_path), mr[0].start_pos)):
        print(f"{mutation.module_path}:{mutation.start_pos[0]}: {mutation.operator_name} "
              f"{describe(result.diff or '')}")
    counts: dict[str, int] = {}
    for _, result in results:
        name = (result.test_outcome or result.worker_outcome).value
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"
    print(f"ran {len(results)} of {len(mutations)} mutants in {time.monotonic() - started:.0f}s: "
          f"{summary}; {len(survivors)} survived")
    if queue or cut:
        print(f"incomplete: {len(queue)} mutants not started and {len(cut)} cut short by the "
              f"{budget:.0f}s deadline")
    # cosmic-ray swallows its own failures (a mutation it could not write, a test command it
    # could not launch) into INCOMPETENT results. NO_TEST also leaves the sampled
    # mutation untested; neither is evidence of a killed or surviving mutant.
    broken = [
        (m, r) for m, r in results
        if r.test_outcome is None or r.test_outcome == TestOutcome.INCOMPETENT
    ]
    if broken:
        mutation, result = broken[0]
        print(f"mutation check broke on {len(broken)} mutants; first at "
              f"{mutation.module_path}:{mutation.start_pos[0]}:\n{(result.output or '')[-3000:]}")
        return 1
    if args.working_tree and (queue or cut):
        return 1
    if args.blocking and hot_survivors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
