"""Mutation testing of changed `factory/` files for the pr-tier gate.

Mutates the Python files under `factory/` that changed in `origin/main...HEAD`, runs
the full discovered unittest suite against each mutant (related tests first,
stopping at the first failure), and prints
every survivor as `path:line: <mutation>`. The default report-only mode exits zero
after a complete trial even when mutants survive. ``--blocking`` exits nonzero for
completed survivors on changed lines, except exact reviewed ``--equivalents`` matches.
Invalid or stale equivalent entries, tool and baseline failures always exit nonzero.
Mutants are sampled deterministically (sorted, then shuffled with a fixed
seed, capped at CAP). One absolute deadline, `[gate].timeout` - 60 (or, with an
explicit ``--duration SECONDS``, exactly SECONDS - 60, unclamped), covers preparation, the baseline and every mutant: each run is bounded by the time
left, and mutants not started or cut short are reported as incomplete.

Each worker runs in its own copy of the tree because cosmic-ray mutates files on disk;
the worktree itself is never touched.

When the gate sets ``FACTORY_MUTATION_PROOF`` (repositories that opted in with
``proof = "mutation-v1"``), the runner writes one JSON completion record there just
before exiting: mode, status, reason, counts, the exit code it returns and the scope
(resolved base, merge-base, head, fingerprint) of the lines it mutated. The gate also sets
``FACTORY_MUTATION_PROOF_BASE`` to its caller's resolved base commit, which then replaces
``origin/main``. Both variables are removed from this process's environment first, so
test descendants cannot see them. Applicability is factory.mutation_scope, shared with the
gate. A proof run also requires every tracked file to hold the pinned head's bytes: in the
checkout before and after enumeration, in each prepared copy before use, and in the
checkout and every copy again after the baseline and before any mutation (with base, head
and scope re-resolved). Any difference is an error record; nothing is mutated. Each
prepared copy also keeps only the paths the pinned head lists: right after it is copied,
and again (every copy) after the baseline and before any mutation, any other path
(untracked, ignored or staged-new in the checkout, or baseline output) is deleted from the
copy, never from the checkout, and the run continues. Runs
without a record path keep the historical changed_lines() and never import the helper.
"""

from __future__ import annotations

import argparse
import collections
import errno
import json
import math
import os
import random
import re
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

CAP = 200
BASE = "origin/main"  # without a gate-named base (report-only and unopted runs)
SEED = 197
RESERVE = 60  # seconds kept for cleanup after the one deadline
WORKERS = min(16, os.cpu_count() or 1)
COPY_IGNORE = shutil.ignore_patterns(".git", ".venv", ".factory", "__pycache__", ".ruff_cache")
PROOF_ENV = "FACTORY_MUTATION_PROOF"
PROOF_BASE_ENV = "FACTORY_MUTATION_PROOF_BASE"  # the gate's resolved caller base commit
PROOF_PROTOCOL = "factory-mutation-proof"
PROOF_VERSION = 1
PROOF_COUNTS = ("planned", "ran", "killed", "survived", "timeouts", "blocking_survivors",
                "not_started", "cut_short", "broken")


def proof_record(*, mode: str, status: str, reason: str, exit_code: int,
                 files: list[str] | tuple[str, ...] = (), scope: dict | None = None,
                 **counts: int) -> dict:
    """The runner's completion record; the gate re-checks every field against its exit
    and `scope` against its own factory.mutation_scope computation."""
    unknown = counts.keys() - set(PROOF_COUNTS)
    if unknown:
        raise TypeError(f"unknown proof counts: {sorted(unknown)}")
    return {
        "protocol": PROOF_PROTOCOL, "version": PROOF_VERSION, "mode": mode, "status": status,
        "reason": reason, "exit": exit_code, "files": sorted(files), "scope": scope,
        "counts": {name: int(counts.get(name, 0)) for name in PROOF_COUNTS},
    }


def write_proof(path: str, record: dict) -> None:
    target = Path(path)
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(record, sort_keys=True))
    os.replace(partial, target)  # never a half-written record at `path`


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


def scope_lines(base: str, computed: list) -> dict[str, set[int]]:
    """Proof runs only: changed_lines' rule as the shared, commit-resolved
    factory.mutation_scope Scope (also the gate's), appended to `computed` so the record
    describes exactly what was mutated. Imported here: unopted runs never need it."""
    from factory import mutation_scope

    scope = mutation_scope.compute(base)
    computed.append(scope)
    return {path: set(lines) for path, _, lines in scope.files}


def pinned_drift(scope, *directories: Path) -> list[str]:
    """Proof runs only: `<directory>: <path>` for each path tracked in the pinned head
    whose bytes under a directory differ from that commit's; [] when all match."""
    from factory import mutation_scope

    return [f"{directory}: {path}" for directory in directories
            for path in mutation_scope.tracked_mismatches(scope.head, directory)]


def exclude_non_head(scope, *copies: Path) -> list[str]:
    """Proof runs only: delete from each disposable prepared copy every path the pinned
    head's tree does not list (the copy's own top-level .git excepted); returns
    `<copy>: <path>` for each removed path. The checkout is never touched.

    Read-only modes inside a copy do not stop it: a removed directory gets owner rwx before
    its entries go; a retained directory gets owner rwx only while its entries go, then its
    own mode back. Symlinks are unlinked, never followed or chmodded. A removal that still
    fails raises (no completion record: the gate fails closed).

    Before anything is deleted or chmodded, every copy must be a real directory (lstat: not
    a symlink) owned by this process's effective uid, and no parent directory of a head
    path inside it may be a symlink (mutation_scope.linked_ancestors: lstat, shallow-first,
    the shallowest link is named and left untouched); otherwise an OSError is raised. Any
    directory the walk cannot list (the copy itself or a retained one) raises that error,
    and so does a walk that does not start at the copy itself. This is a type/owner check
    of the starting path only, not a guarantee against concurrent replacement."""
    from factory import mutation_scope

    def check_copy(copy: Path) -> None:
        info = os.lstat(copy)  # a missing copy raises FileNotFoundError here
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "prepared copy is not a real directory", os.fspath(copy))
        if info.st_uid != os.geteuid():
            raise PermissionError(errno.EPERM, "prepared copy is not owned by this process", os.fspath(copy))

    def unlistable(error: OSError) -> None:
        raise error  # never skip a directory the walk could not list

    def remove(path: str) -> None:
        if not stat.S_ISDIR(os.lstat(path).st_mode):  # a file or a symlink (not followed)
            os.unlink(path)
            return
        os.chmod(path, stat.S_IRWXU)  # removed next: its mode needs no restoring
        with os.scandir(path) as entries:
            children = [entry.path for entry in entries]
        for child in children:
            remove(child)
        os.rmdir(path)

    tracked = mutation_scope.tracked_paths(scope.head)
    keep = tracked | {parent.as_posix() for path in tracked for parent in PurePosixPath(path).parents}
    removed = []
    for copy in copies:
        check_copy(copy)
        linked = mutation_scope.linked_ancestors(tracked, copy)  # lstat only, shallow-first
        if linked:
            raise NotADirectoryError(errno.ENOTDIR, "retained head ancestor directory is a symlink",
                                     os.fspath(Path(copy) / linked[0]))
    for copy in copies:
        top = os.fspath(copy)
        visited = False
        for current, dirs, files in os.walk(copy, onerror=unlistable):
            if not visited:
                if current != top:
                    raise NotADirectoryError(errno.ENOTDIR, "traversal did not start at the prepared copy", top)
                visited = True
            here = Path(current).relative_to(copy)
            mode = None  # `current`'s own mode, set once it is made owner-writable
            try:
                for name in [*dirs, *files]:
                    rel = (here / name).as_posix()
                    if rel in keep or (rel == ".git" and here == Path(".")):
                        continue
                    if mode is None:
                        mode = stat.S_IMODE(os.lstat(current).st_mode)
                        os.chmod(current, mode | stat.S_IRWXU)
                    remove(os.path.join(current, name))
                    removed.append(f"{copy}: {rel}")
            finally:
                if mode is not None:
                    os.chmod(current, mode)  # a retained directory keeps its own mode
            dirs[:] = [d for d in dirs if (here / d).as_posix() in keep]  # never into .git
        if not visited:
            raise NotADirectoryError(errno.ENOTDIR, "traversal never visited the prepared copy", top)
    return removed


def on_changed_line(mutation, lines: dict[str, set[int]]) -> bool:
    return not lines[str(mutation.module_path)].isdisjoint(
        range(mutation.start_pos[0], mutation.end_pos[0] + 1),
    )


def total_duration(value: str) -> float:
    """`--duration SECONDS`: an explicit total, finite and longer than the cleanup RESERVE."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not math.isfinite(seconds) or seconds <= RESERVE:
        raise argparse.ArgumentTypeError(
            f"must be finite and greater than the {RESERVE}s cleanup reserve: {value!r}")
    return seconds


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
    """Run the trusted producer's test-only entry point against a prepared head copy."""
    environment = Path(sys.executable).parent.parent
    paths = [
        sysconfig.get_path(name, vars={"base": str(environment), "platbase": str(environment)})
        for name in ("purelib", "platlib")
    ]
    launcher = (
        "import runpy,sys;"
        "sys.path[:0]=sys.argv[1:3];"
        "script=sys.argv[3];"
        "sys.argv=sys.argv[3:];"
        "runpy.run_path(script,run_name='__main__')"
    )
    command = [
        "env", "-C", str(copy), sys.executable, "-I", "-S", "-B", "-c", launcher,
        *paths, str(Path(__file__).resolve()), "--test-module", module or "",
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
    # Popped before any work: baseline, mutant and --test-module children inherit
    # os.environ, and must never learn where the completion record goes or its base.
    proof_path = os.environ.pop(PROOF_ENV, None)
    proof_base = os.environ.pop(PROOF_BASE_ENV, None)
    outcome: dict = {}
    code = _main(argv, outcome, proof_base if proof_path else BASE, proof=bool(proof_path))
    if proof_path and "status" in outcome:
        write_proof(proof_path, proof_record(exit_code=code, **outcome))
    return code


def _main(argv: list[str] | None, outcome: dict, base: str | None, *, proof: bool = False) -> int:
    """Baseline `main`; `outcome` receives the completion status of each return path.

    A path that raises or returns without a status writes no record (the gate fails closed).
    `base` is origin/main without a record path; with one, only the gate's resolved caller
    base commit, and None (not named) is an error record."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocking", action="store_true")
    parser.add_argument("--equivalents", metavar="FILE", help="reviewed equivalent-mutants JSON file")
    parser.add_argument("--test-module", default=None)
    parser.add_argument("--duration", type=total_duration, default=None, metavar="SECONDS",
                        help="explicit total: the deadline is SECONDS - RESERVE after the anchor, "
                        "instead of max(1, [gate].timeout - RESERVE)")
    args = parser.parse_args(argv)
    if args.test_module is not None:
        return run_tests(args.test_module)
    outcome["mode"] = "blocking" if args.blocking else "report-only"
    if base is None:
        print("mutation proof requested without a base commit")
        outcome.update(status="error", reason="missing-base")
        return 1
    try:
        reviewed = load_equivalents(args.equivalents)
    except (OSError, ValueError, TypeError) as exc:
        print(f"invalid equivalents: {exc}")
        outcome.update(status="error", reason="invalid-equivalents")
        return 1
    if stale_equivalents(reviewed, set()):
        outcome.update(status="error", reason="stale-equivalents")
        return 1
    computed: list = []
    lines = scope_lines(base, computed) if proof else changed_lines()
    # The exact scope the lines below came from (absent only when scope_lines is replaced).
    scope = computed[0] if computed else None
    outcome["scope"] = scope.receipt() if scope else None
    if not lines:
        print("no mutable files")
        # The shared rule found no added/modified factory/*.py destination lines.
        outcome.update(status="skip", reason="no-applicable-files")
        return 0
    outcome["files"] = sorted(lines)

    def drifted(reason: str, *directories: Path, rescope: bool = False) -> bool:
        """Proof runs: True (an error outcome) unless every tracked execution input under
        `directories` is the pinned head's bytes and, with `rescope`, base/head/scope
        still resolve to the recorded Scope."""
        if not proof:
            return False
        drift = pinned_drift(scope, *directories)
        if rescope and not drift:
            again: list = []
            scope_lines(base, again)
            if again[0] != scope:
                drift = [f"scope changed: {again[0].receipt()}"]
        if drift:
            print(f"tracked inputs differ from HEAD {scope.head} ({reason}): {', '.join(drift[:20])}")
            outcome.update(status="error", reason=reason)
        return bool(drift)

    def exclude(*copies: Path) -> None:
        """Proof runs: each prepared copy keeps only paths the pinned head lists; anything
        else (untracked, ignored or staged-new in the checkout, or written by the baseline)
        is removed from the copy, never from the checkout, and disclosed."""
        if not proof:
            return
        removed = exclude_non_head(scope, *copies)
        if removed:
            print(f"{len(removed)} paths excluded from prepared copies (absent from HEAD "
                  f"{scope.head}): {', '.join(removed[:20])}")

    from attrs import evolve
    from cosmic_ray.mutating import mutate_and_test
    from cosmic_ray.work_item import TestOutcome
    window = max(1, gate_timeout() - RESERVE) if args.duration is None else args.duration - RESERVE
    deadline = time.monotonic() + window
    root = Path.cwd()
    started = time.monotonic()
    # Enumeration reads the checkout: its tracked bytes must be the pinned head's, before
    # and after (the fingerprint names committed blobs, never worktree bytes).
    if drifted("dirty-tracked-inputs", root):
        return 1
    with tempfile.TemporaryDirectory(prefix="factory-mutation-") as tmp:
        scratch = Path(tmp)
        mutations, hot = sample(lines, scratch)
        if drifted("dirty-tracked-inputs", root):
            return 1
        if args.blocking:
            mutations = mutations[:hot]

        def baseline_timed_out() -> int:
            print(f"baseline suite did not finish before the {deadline - started:.0f}s deadline")
            print(f"incomplete: {len(mutations)} mutants not started and 0 cut short by the "
                  f"{deadline - started:.0f}s deadline")
            outcome.update(status="incomplete", reason="baseline-deadline",
                           planned=len(mutations), not_started=len(mutations))
            return 1

        workers = max(1, min(WORKERS, len(mutations)))
        copies = [scratch / f"copy{i}" for i in range(workers)]
        for copy in copies:
            # Isolate Git metadata, but overlay current files rather than checking out HEAD:
            # unopted baselines include uncommitted edits/deletions and untracked files too
            # (proof runs then keep only the pinned head's paths, below).
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return baseline_timed_out()
            subprocess.run(
                ["git", "clone", "--quiet", "--shared", "--no-checkout", str(root), str(copy)],
                check=True, timeout=remaining,
            )
            shutil.copytree(root, copy, ignore=COPY_IGNORE, dirs_exist_ok=True)
            # Each prepared child, before any use: only the pinned head's paths, holding
            # exactly its tracked bytes.
            exclude(copy)
            if drifted("prepared-copy-mismatch", copy):
                return 1
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
            outcome.update(status="error", reason="baseline-failed", planned=len(mutations))
            return 1
        # Again after the baseline and before any mutation: every copy back to the pinned
        # head's paths, no tracked input of the checkout or of any copy changed, and
        # base/head/scope still resolve the same.
        exclude(*copies)
        if drifted("baseline-changed-inputs", root, *copies, rescope=True):
            return 1
        remaining = deadline - baseline_finished
        timeout = min(max(60.0, 3 * baseline), remaining / 4)
        print(f"baseline: {baseline:.0f}s, per-mutant timeout {timeout:.0f}s, "
              f"{remaining:.0f}s left before the deadline")

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
    timeouts = sum(result.output == "timeout" for _, result in results)
    print(f"ran {len(results)} of {len(mutations)} mutants in {time.monotonic() - started:.0f}s: "
          f"{timeouts} timeouts (included in killed); {summary}; {len(survivors)} survived")
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
    killed = sum(r.test_outcome == TestOutcome.KILLED for _, r in results)
    tally = {
        "planned": len(mutations), "ran": len(results), "killed": killed,
        "survived": len(survivors), "timeouts": timeouts,
        "blocking_survivors": len(hot_survivors), "not_started": len(queue),
        "cut_short": len(cut), "broken": len(results) - killed - len(survivors),
    }
    # Timeout-scored mutants stay "killed" here exactly as in the summary line above.
    status, reason = (
        ("error", "tool-broken") if tally["broken"]
        else ("incomplete", "deadline") if queue or cut
        else ("error", "stale-equivalents") if stale
        else ("complete", "blocking-survivors" if args.blocking and hot_survivors
              # Applicable lines exist but enumerated no candidate: complete, never a skip.
              else "no-mutation-candidates" if not mutations
              else "no-blocking-survivors")
    )
    outcome.update(status=status, reason=reason, **tally)
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
