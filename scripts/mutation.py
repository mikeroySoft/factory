"""Mutation testing of changed `factory/` files for the pr-tier gate.

Mutates the Python files under `factory/` that changed in `origin/main...HEAD`, runs
the full discovered unittest suite against each mutant (related tests first,
stopping at the first failure), and prints
every survivor as `path:line: <mutation>`. The default report-only mode exits zero
after a complete trial even when mutants survive. ``--blocking`` exits nonzero for
completed survivors on changed lines, except exact reviewed ``--equivalents`` matches.
Invalid or stale equivalent entries, tool and baseline failures always exit nonzero.
Mutants are sampled deterministically (sorted, then shuffled with a fixed
seed, capped at CAP). After the baseline, shrink the hot-first sample to full
worker waves that fit the remaining budget. One absolute deadline,
`[gate].timeout` - 60, covers preparation, the baseline and every mutant.
Sized-out and unreached mutants are not-run; cut-short trials remain incomplete.

Each worker runs in its own copy of the tree because cosmic-ray mutates files on disk;
the worktree itself is never touched.
"""

from __future__ import annotations

import argparse
import collections
import json
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
WORKERS = min(16, os.cpu_count() or 1)
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", ".factory", "__pycache__", ".ruff_cache")


def changed_lines() -> dict[str, set[int]]:
    """Changed `factory/*.py` files (added or modified) -> line numbers added or modified."""
    out = subprocess.run(
        ["git", "diff", "--unified=0", "--no-renames", "--diff-filter=AM", "origin/main...HEAD", "--", "factory/*.py"],
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
        "env", "-C", str(copy), f"PYTHONPATH={copy}", sys.executable,
        "-B", "-m", "scripts.mutation", "--test-module", module or "",
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


def load_equivalents(path: str | None) -> dict[tuple, dict]:
    """Load reviewed identities; never match the truncated display description."""
    if path is None:
        return {}
    entries = json.loads(Path(path).read_text())
    if not isinstance(entries, list):
        raise TypeError("expected an array of reviewed equivalents")
    reviewed = {}
    fields = ("file", "line", "operator", "original", "mutated")
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not {*fields, "argument", "reviewer"} <= entry.keys():
            raise ValueError(f"entry {index}: requires file, line, operator, original, mutated, argument, reviewer")
        for field in ("file", "operator", "argument", "reviewer"):
            value = entry[field]
            if not isinstance(value, str) or not value.strip() or value.splitlines() != [value]:
                raise ValueError(f"entry {index}: {field} must be a nonempty single line")
        file = Path(entry["file"])
        if file.is_absolute() or ".." in file.parts or file.as_posix() != entry["file"]:
            raise ValueError(f"entry {index}: file must be a repository-relative path")
        if type(entry["line"]) is not int or entry["line"] < 1:
            raise ValueError(f"entry {index}: line must be a positive integer")
        if any(not isinstance(entry[field], str) for field in ("original", "mutated")):
            raise ValueError(f"entry {index}: original and mutated must be exact source strings")
        if entry["original"] == entry["mutated"]:
            raise ValueError(f"entry {index}: original and mutated must differ")
        key = tuple(entry[field] for field in fields)
        if key in reviewed:
            raise ValueError(f"entry {index}: duplicate equivalent")
        reviewed[key] = entry
    return reviewed


def mutation_key(mutation, result) -> tuple:
    lines = (result.diff or "").splitlines()
    # Ignore unified-diff headers, preserving all changed source lines verbatim.
    start = next((i + 1 for i, line in enumerate(lines) if line.startswith("@@")), 0)
    changes = lines[start:]
    original = "\n".join(line[1:] for line in changes if line.startswith("-"))
    mutated = "\n".join(line[1:] for line in changes if line.startswith("+"))
    return (str(mutation.module_path), mutation.start_pos[0], mutation.operator_name,
            original, mutated)


def stale_equivalents(reviewed: dict[tuple, dict], disproven: set[tuple]) -> bool:
    # Sampling/deadlines leave entries untested, but source invalidation is deterministic.
    stale = reviewed.keys() & disproven
    sources = {}
    for key in reviewed:
        file, line, _, original, _ = key
        if file not in sources:
            try:
                sources[file] = Path(file).read_text().splitlines()
            except (OSError, UnicodeError):
                sources[file] = []
        expected = original.split("\n")
        if sources[file][line - 1:line - 1 + len(expected)] != expected:
            stale.add(key)
    for file, line, operator, original, mutated in sorted(stale):
        print(f"stale equivalent: {file}:{line}: {operator} {original!r} -> {mutated!r}")
    return bool(stale)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocking", action="store_true")
    parser.add_argument("--equivalents", metavar="FILE", help="reviewed equivalent-mutants JSON file")
    parser.add_argument("--test-module", default=None)
    args = parser.parse_args(argv)
    if args.test_module is not None:
        return run_tests(args.test_module)
    try:
        reviewed = load_equivalents(args.equivalents)
    except (OSError, ValueError, TypeError) as exc:
        print(f"invalid equivalents: {exc}")
        return 1
    if stale_equivalents(reviewed, set()):
        return 1
    lines = changed_lines()
    if not lines:
        print("no mutable files")
        return 0
    from attrs import evolve
    from cosmic_ray.mutating import mutate_and_test
    from cosmic_ray.work_item import TestOutcome
    deadline = time.monotonic() + max(1, gate_timeout() - 60)
    root = Path.cwd()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="factory-mutation-") as tmp:
        scratch = Path(tmp)
        mutations, hot = sample(lines, scratch)
        if args.blocking:
            mutations = mutations[:hot]

        def baseline_timed_out() -> int:
            print(f"baseline suite did not finish before the {deadline - started:.0f}s deadline")
            print(f"incomplete: {len(mutations)} mutants not started and 0 cut short by the "
                  f"{deadline - started:.0f}s deadline")
            print("mutation-budget: " + json.dumps({
                "status": "budget_limited", "budget_seconds": deadline - started,
                "workers": workers, "mutants_run": 0, "mutants_not_run": len(mutations),
            }))
            return 1

        workers = max(1, min(WORKERS, len(mutations)))
        copies = [scratch / f"copy{i}" for i in range(workers)]
        for copy in copies:
            # Isolate Git metadata, but overlay current files rather than checking out HEAD:
            # baselines must include uncommitted edits/deletions and untracked files too.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return baseline_timed_out()
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", "--no-checkout", str(root), str(copy)],
                check=True, timeout=remaining,
            )
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
        remaining = deadline - baseline_finished
        timeout = min(max(60.0, 3 * baseline), remaining / 4)
        print(f"baseline: {baseline:.0f}s, per-mutant timeout {timeout:.0f}s, "
              f"{remaining:.0f}s left before the deadline")

        sampled = len(mutations)
        # Full waves avoid admitting a partial final wave that cannot finish.
        capacity = workers * int((deadline - baseline_finished) / baseline) if baseline > 0 else sampled
        mutations = mutations[:capacity]
        sized_out = sampled - len(mutations)
        print(f"sample: {len(mutations)} of {sampled} fit at baseline {baseline:.0f}s, "
              f"{workers} workers, {deadline - started:.0f}s deadline; {sized_out} sized out")

        queue = collections.deque(mutations)  # popleft is atomic under the GIL
        results = []
        cut = []  # budget-limited runs: neither killed nor survived

        def worker(copy: Path) -> None:
            # A timeout below baseline cannot disprove a survivor or equivalent.
            while (left := deadline - time.monotonic()) > 0:
                try:
                    mutation = queue.popleft()
                except IndexError:
                    return
                local = evolve(mutation, module_path=copy / mutation.module_path)
                result = mutate_and_test(
                    [local], test_command(copy, str(mutation.module_path)), min(timeout, left),
                )
                if result.output == "timeout" and (
                    timeout < baseline or left < timeout or time.monotonic() >= deadline
                ):
                    cut.append(mutation)
                    if timeout >= baseline:
                        return
                else:
                    results.append((mutation, result))

        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(worker, copies))

    survivors = [(m, r) for m, r in results if r.test_outcome == TestOutcome.SURVIVED]
    accepted = {mutation_key(m, r) for m, r in survivors} & reviewed.keys()
    hot_survivors = [
        (mutation, result) for mutation, result in survivors
        if on_changed_line(mutation, lines) and mutation_key(mutation, result) not in accepted
    ]
    for mutation, result in sorted(survivors, key=lambda mr: (str(mr[0].module_path), mr[0].start_pos)):
        print(f"{mutation.module_path}:{mutation.start_pos[0]}: {mutation.operator_name} "
              f"{describe(result.diff or '')}")
        if (key := mutation_key(mutation, result)) in accepted:
            entry = reviewed[key]
            print(f"  accepted equivalent: {entry['argument']} (reviewer: {entry['reviewer']})")
    counts: dict[str, int] = {}
    for _, result in results:
        name = (result.test_outcome or result.worker_outcome).value
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "none"
    elapsed = time.monotonic() - started
    timeouts = sum(result.output == "timeout" for _, result in results)
    print(f"ran {len(results)} of {sampled} mutants in {elapsed:.0f}s: "
          f"{timeouts} timeouts (included in killed); {summary}; {len(survivors)} survived")
    not_run = sized_out + len(queue) + len(cut)
    if not_run:
        print(f"budget-limited: {not_run} of {sampled} not run "
              f"(baseline {baseline:.0f}s, {workers} workers, {deadline - started:.0f}s)")
    print("mutation-budget: " + json.dumps({
        "status": "budget_limited" if not_run else "completed",
        "baseline_seconds": baseline, "workers": workers,
        "budget_seconds": deadline - started,
        "mutants_run": len(results), "mutants_not_run": not_run,
    }))
    if queue or cut:
        print(f"incomplete: {len(queue)} mutants not started and {len(cut)} cut short by the "
              f"{deadline - started:.0f}s deadline")
    disproven = {
        mutation_key(m, r) for m, r in results if r.test_outcome == TestOutcome.KILLED
    }
    if hot <= len(mutations) and not queue and not cut and all(
        r.test_outcome in (TestOutcome.KILLED, TestOutcome.SURVIVED) for _, r in results
    ):
        # All changed-line candidates were sampled and tested. Enumeration also
        # proves absence on changed lines that generated no candidates.
        disproven.update(
            key for key in reviewed
            if key[1] in lines.get(key[0], ()) and key not in accepted
        )
    stale = stale_equivalents(reviewed, disproven)
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
    if stale or (args.blocking and hot_survivors):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
