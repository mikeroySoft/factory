"""Deterministic quality gate for agent worktrees.

Runs from a worktree root. Exit 0 = all checks pass, 1 = any failure.
Writes a short Markdown report (PASS/FAIL per check + failure excerpts).
"""

from __future__ import annotations

import argparse
import ast
import fcntl
import fnmatch
import os
import re
import signal
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from factory import config, lifecycle
from factory.config import CONFIG_NAME, Config

cfg: Config
GPU_LOCK: Path
LEAK_RE: re.Pattern[str] | None = None
GPU_CHECKS: set[str] = set()
TAIL_LINES = 80
CHECK_TIMEOUT = 1200  # seconds; overridable via --check-timeout


def configure(c: Config) -> None:
    global cfg, GPU_LOCK, LEAK_RE, GPU_CHECKS, CHECK_TIMEOUT
    cfg = c
    GPU_LOCK = cfg.lock
    LEAK_RE = re.compile(cfg.leak_pattern, re.IGNORECASE) if cfg.leak_pattern else None
    GPU_CHECKS = {check.name for check in cfg.checks if check.exclusive}
    CHECK_TIMEOUT = cfg.check_timeout


def timed(
    cmd: list[str], cwd: Path | None = None, pythonpath: Path | None = None
) -> subprocess.CompletedProcess:
    """Run a command with the per-check timeout.

    A wedged check must FAIL, not sit on the GPU lock (ticket #5's gate hung
    for 2h). The check runs in its own session so a timeout kills the whole
    tree (cargo/test grandchildren included), not just the direct child.
    """
    execution = lifecycle.current()
    # The handoff row is still recorded, but the check never inherits the context:
    # a repo's own tests would otherwise append their fixture rows to the live ledger.
    env = execution.env() if execution else dict(os.environ)
    env.pop(lifecycle.CONTEXT_ENV, None)
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", start_new_session=True, env=env,
    )
    if execution:
        execution.child(proc.pid)
    try:
        out, _ = proc.communicate(timeout=CHECK_TIMEOUT)
        if execution:
            execution.emit("result", command=cmd, returncode=proc.returncode, timed_out=False)
            if proc.returncode < 0:
                execution.outcome = "unknown"
                execution.reason = "check_terminated_by_signal"
        return subprocess.CompletedProcess(cmd, proc.returncode, out, "")
    except subprocess.TimeoutExpired:
        if execution:
            execution.outcome = "unknown"
            execution.reason = "check_timeout"
            execution.emit("timeout", command=cmd, timeout_seconds=CHECK_TIMEOUT)
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        if execution:
            execution.emit("result", command=cmd, returncode=proc.returncode, timed_out=True)
        return subprocess.CompletedProcess(
            cmd, 124, f"{out}\ncheck timed out after {CHECK_TIMEOUT}s", ""
        )
    finally:
        if execution and proc.poll() is not None:
            execution.child_done(proc.pid)


def run(cmd: list[str]) -> tuple[bool, str]:
    """Run a command, return (passed, combined output)."""
    proc = timed(cmd)
    execution = lifecycle.current()
    if execution and proc.returncode != 0 and execution.outcome == "completed":
        execution.outcome = "product_feedback"
        execution.reason = "configured_check_failed"
    return proc.returncode == 0, proc.stdout + proc.stderr

def _check_status(name: str, passed: bool, output: str, blocking_mutation: bool) -> str:
    if (
        blocking_mutation
        and name == "mutation"
        and passed
        and "no mutable files" in output.splitlines()
    ):
        return "SKIP (no changed factory code)"
    return "PASS" if passed else "FAIL"



def failed_test_ids(output: str) -> list[str]:
    """Canonical unittest, pytest and cargo libtest IDs, in report order."""
    found = []
    for line in output.splitlines():
        match = re.match(r"^(?:FAIL|ERROR): \S+ \(([^()\s]+)\)", line)
        if match is None:
            match = re.match(r"^FAILED ([^\s]+::[^\s]+)(?:\s+-|\s*$)", line)
        if match is None:
            match = re.match(r"^---- ([^\s]+) stdout ----$", line)
        if match is not None and match[1] not in found:
            found.append(match[1])
    return found


_EXCEPTION_LINE = re.compile(r"^(?:[A-Za-z_]\w*\.)*[A-Z]\w*(?::|$)")
_PYTEST_HEADING = re.compile(r"^_+\s+.*[A-Za-z0-9].*\s+_+$")


def _failure_block(lines: list[str], test_id: str) -> tuple[str, list[str]]:
    unittest_heading = re.compile(
        rf"^(?:FAIL|ERROR): \S+ \({re.escape(test_id)}\)"
    )
    cargo_heading = re.compile(rf"^---- {re.escape(test_id)} stdout ----$")
    for index, line in enumerate(lines):
        if unittest_heading.match(line):
            end = next((
                later for later in range(index + 1, len(lines))
                if re.match(r"^(?:FAIL|ERROR): ", lines[later])
            ), len(lines))
            return "unittest", lines[index + 1:end]
        if cargo_heading.match(line):
            end = next((
                later for later in range(index + 1, len(lines))
                if re.match(r"^---- [^\s]+ stdout ----$", lines[later])
            ), len(lines))
            return "cargo", lines[index:end]

    node = test_id.partition("::")[2]
    name, bracket, parameter = node.partition("[")
    pytest_name = name.replace("::", ".") + bracket + parameter
    pytest_heading = re.compile(rf"^_+\s+{re.escape(pytest_name)}\s+_+$")
    for index, line in enumerate(lines):
        if pytest_heading.match(line):
            end = next((
                later for later in range(index + 1, len(lines))
                if _PYTEST_HEADING.match(lines[later])
                or re.match(r"^(?:-+\s+Captured |=+\s)", lines[later])
            ), len(lines))
            return "pytest", lines[index:end]
    return "pytest", []


def _terminal_exception_block(block: list[str]) -> list[str]:
    start = 0
    for index, line in enumerate(block):
        if line in {
            "The above exception was the direct cause of the following exception:",
            "During handling of the above exception, another exception occurred:",
        }:
            start = index + 1
    return block[start:]


def _test_frame(path: str, test_id: str) -> bool:
    test_file = (
        test_id.partition("::")[0] if "::" in test_id
        else test_id.rsplit(".", 2)[0].replace(".", "/") + ".py"
    )
    directory = Path(test_file).parent
    parent = Path(path).parent
    return Path(path).name == Path(test_file).name or (
        directory != Path(".")
        and (parent == directory or str(parent).endswith(f"/{directory}"))
    )


def _failure_location(
    kind: str, block: list[str], test_id: str
) -> tuple[str | None, int]:
    patterns = {
        "unittest": r'File "([^"]+)", line (\d+)',
        "cargo": r"panicked at (.+?):(\d+)(?::\d+)?:$",
        "pytest": r"^(\S.*\.py):(\d+)(?::\d+)?:",
    }
    if kind == "unittest":
        end = next((
            index for index, line in enumerate(block) if _EXCEPTION_LINE.match(line)
        ), len(block))
        block = block[:end]
    location = None, 0
    for index in reversed(range(len(block))):
        if match := re.search(patterns[kind], block[index]):
            candidate = f"{match[1]}:{match[2]}", index
            if location[0] is None:
                location = candidate
            if kind == "cargo" or _test_frame(match[1], test_id):
                return candidate
    return location


def _pytest_source_message(block: list[str]) -> str | None:
    for line in block:
        if match := re.match(r"^>\s+(.*)", line):
            source = match[1]
            if "assert" in source or "raise" in source:
                return source.strip()
    return None


def _pytest_failure_message(block: list[str]) -> str | None:
    errors = []
    for line in block:
        if match := re.match(r"^E\s+(.*)", line):
            errors.append(match[1].strip())
    for line in errors:
        if _EXCEPTION_LINE.match(line):
            return line
    for line in errors:
        if "assert" in line:
            return line
    return _pytest_source_message(block)


def _failure_message(kind: str, block: list[str], location_index: int) -> str | None:
    match kind:
        case "cargo":
            return next((
                line.strip() for line in block[location_index + 1:] if line.strip()
            ), None)
        case "unittest":
            return next((
                line.strip() for line in block[location_index + 1:]
                if _EXCEPTION_LINE.match(line)
            ), None)
        case _:
            return _pytest_failure_message(block)


def _expected_actual_line(line: str, message: str) -> str | None:
    marked = re.match(r"^[E>]\s+(.*)", line)
    candidate = marked[1].strip() if marked else line.strip()
    if candidate == message:
        return None
    if candidate.startswith(("- ", "+ ")):
        return candidate
    if candidate.lower().startswith(("expected:", "actual:", "left:", "right:")):
        return candidate
    if marked and (" == " in candidate or " != " in candidate):
        return candidate
    return None


def _expected_actual(block: list[str], message: str) -> list[str]:
    excerpt = []
    for line in block:
        candidate = _expected_actual_line(line, message)
        if candidate and candidate not in excerpt:
            excerpt.append(candidate)
            if len(excerpt) == 4:
                break
    return excerpt


def first_failure_summary(output: str, test_id: str) -> dict | None:
    """Location, message and expected/actual excerpt for the first failed test."""
    kind, block = _failure_block(output.splitlines(), test_id)
    if kind != "cargo":
        block = _terminal_exception_block(block)
    location, location_index = _failure_location(kind, block, test_id)
    message = _failure_message(kind, block, location_index)
    if location is None or message is None:
        return None
    return {
        "test": test_id,
        "location": location,
        "message": message,
        "expected_actual": _expected_actual(block, message),
    }


def failure_summary_lines(
    failed_tests: list[str], summary: dict | None
) -> list[str]:
    if not failed_tests:
        return ["Failing tests: could not identify failing tests."]
    lines = ["Failing tests:"]
    for test_id in failed_tests:
        entry = f"- `{test_id}`"
        if summary is not None:
            entry += f" — `{summary['location']}`: {summary['message']}"
        lines.append(entry)
        if summary and summary["expected_actual"]:
            lines.append("  Expected/actual: " + " | ".join(summary["expected_actual"]))
        summary = None
    return lines


def changed_paths(base: str) -> list[str]:
    proc = timed(["git", "diff", "--name-only", "-z", f"{base}...HEAD"])
    return proc.stdout.rstrip("\0").split("\0") if proc.returncode == 0 and proc.stdout else []



def _acquire_exclusive(execution):
    lock = open(GPU_LOCK, "w")  # noqa: SIM115 — caller controls lock lifetime
    try:
        requested = execution.resource("requested", GPU_LOCK, scope="host", blocking=True)
        if lifecycle._lock_state(lifecycle._lock(GPU_LOCK)) == "held":
            execution.wait(
                "exclusive_resource", mode="blocking", resource=requested["resource"]
            )
        fcntl.flock(lock, fcntl.LOCK_EX)
        execution.resource("acquired", GPU_LOCK, scope="host", blocking=True)
        execution.wait_end()
        return lock
    except BaseException:
        lock.close()
        raise


def _release_exclusive(execution, lock) -> None:
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    execution.resource("released", GPU_LOCK, scope="host", blocking=True)



def check_conflict_markers() -> tuple[bool, str]:
    proc = timed(
        # Exactly-7-char markers only; long ===== separator lines are legit.
        ["git", "grep", "-nE", r"^(<{7}|={7}|>{7})( |$)"],
    )
    # git grep exits 1 when nothing matches — that is the pass case.
    if proc.returncode == 1:
        return True, ""
    execution = lifecycle.current()
    if execution and execution.outcome == "completed":
        execution.outcome = "product_feedback" if proc.returncode == 0 else "unknown"
        execution.reason = "conflict_markers" if proc.returncode == 0 else "git_grep_failed"
    return False, proc.stdout + proc.stderr


def check_leaks(base: str) -> tuple[bool, str]:
    # .factory.toml is always excluded: its `pattern` literal matches the scan.
    # Other excludes come from `leak_exclude` (paths that never go upstream).
    proc = timed(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-prefix",
            f"{base}..HEAD",
            "--",
            f":(exclude){CONFIG_NAME}",
            *(f":(exclude){p}" for p in cfg.leak_exclude),
        ],
    )
    if proc.returncode != 0:
        execution = lifecycle.current()
        if execution and execution.outcome == "completed":
            execution.outcome = "unknown"
            execution.reason = "git_diff_failed"
        return False, proc.stdout + proc.stderr
    hits, path, header, n = [], "", True, 0
    for line in proc.stdout.splitlines():
        if line.startswith("diff --git "):
            header = True
        elif header and line.startswith("+++ "):
            path = line[4:]
        elif line.startswith("@@ "):
            header, n = False, int(re.match(r"@@ -\S+ \+(\d+)", line).group(1))
        elif not header and line.startswith("+"):
            if m := LEAK_RE.search(line[1:]):
                hits.append(f"{path}:{n}: [{m.group(0)}] {line[1:]}")
            n += 1
    execution = lifecycle.current()
    if hits and execution:
        execution.outcome = "product_feedback"
        execution.reason = "leak_scan_matches"
    return not hits, "\n".join(hits)


ADD_ONLY_NOTE = "add-only path removed or changed existing lines"


def check_protected_paths(
    base: str, upstream: list[str] = (), add_only_override: bool = False
) -> tuple[bool, str, str]:
    """(passed, reason, detail): reason is `protected_paths`, or `add_only_paths` when
    every hit is an add-only glob whose change was not purely additive. With `upstream`
    (the tip, then the staged head) a hit is ignored when its HEAD content equals the
    staged head's, or equals the tip's for a path upstream itself changed since it
    diverged from `base`; fork-only paths the worker deletes or resets still count."""
    listing = timed(["git", "ls-tree", "--name-only", base, "--", CONFIG_NAME])
    if listing.returncode:
        # A new repository has no base commit yet; no worker diff exists to guard.
        unborn = timed(["git", "rev-parse", "--verify", "HEAD"])
        return (True, "", "") if unborn.returncode else (False, "protected_paths", listing.stdout + listing.stderr)
    protected, add_only = [], []
    if listing.stdout.strip():
        source = timed(["git", "show", f"{base}:{CONFIG_NAME}"])
        if source.returncode:
            return False, "protected_paths", source.stdout + source.stderr
        try:
            gate = tomllib.loads(source.stdout).get("gate", {})
        except tomllib.TOMLDecodeError as exc:
            return False, "protected_paths", f"base {CONFIG_NAME}: {exc}"
        protected, add_only = gate.get("protected_paths", []), gate.get("add_only_paths", [])
        for key, globs in (("protected_paths", protected), ("add_only_paths", add_only)):
            if not isinstance(globs, list) or any(not isinstance(p, str) or not p for p in globs):
                return False, "protected_paths", f"base gate.{key} must be an array of nonempty globs"
    proc = timed(["git", "diff", "--no-renames", "--raw", "-z", f"{base}...HEAD"])
    if proc.returncode:
        return False, "protected_paths", proc.stdout + proc.stderr
    fields = proc.stdout.split("\0")
    hits, not_additive, additive = [], [], []  # additive: add-only files modified in place, pending numstat
    for meta, path in zip(fields[::2], fields[1::2]):
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        if any(fnmatch.fnmatchcase(path, g) for g in protected):
            hits.append(path)
        elif any(fnmatch.fnmatchcase(path, g) for g in add_only):
            if status == "M" and old_mode == new_mode:
                additive.append(path)
            elif status != "A":
                not_additive.append(path)
    if upstream and (candidates := hits + not_additive + additive):
        tip = upstream[0]
        fork_point = timed(["git", "merge-base", base, tip])
        if fork_point.returncode:
            return False, "protected_paths", fork_point.stdout + fork_point.stderr
        changed = timed(["git", "diff", "--no-renames", "--name-only", "-z",
                         fork_point.stdout.strip(), tip, "--", *candidates])
        if changed.returncode:
            return False, "protected_paths", changed.stdout + changed.stderr
        upstream_changed = set(changed.stdout.split("\0"))
        authored = set(candidates)
        for ref in upstream:
            diff = timed(["git", "diff", "--no-renames", "--name-only", "-z", ref, "HEAD", "--", *candidates])
            if diff.returncode:
                return False, "protected_paths", diff.stdout + diff.stderr
            same = authored - set(diff.stdout.split("\0"))
            if ref == tip:  # absent-at-both or reset-to-upstream is not upstream authorship
                same &= upstream_changed
            authored -= same
        hits, not_additive, additive = ([p for p in group if p in authored] for group in (hits, not_additive, additive))
    if additive:
        numstat = timed(["git", "diff", "--no-renames", "--numstat", "-z", f"{base}...HEAD", "--", *additive])
        if numstat.returncode:
            return False, "protected_paths", numstat.stdout + numstat.stderr
        for line in numstat.stdout.split("\0"):
            if line:
                added, deleted, path = line.split("\t")
                if added == "-" or deleted == "-" or (not add_only_override and deleted != "0"):
                    not_additive.append(path)
    if not hits and not not_additive:
        return True, "", ""
    reason = "protected_paths" if hits else "add_only_paths"
    diff = timed(["git", "diff", "--no-renames", "--binary", f"{base}...HEAD", "--", *hits, *not_additive])
    return False, reason, diff.stdout + diff.stderr


def _python_test_path(path: str) -> bool:
    file = Path(path)
    return (
        any(part in {"test", "tests"} for part in file.parts[:-1])
        or file.stem.startswith("test_")
        or file.stem.endswith("_test")
    )


def _python_tests(path: str, source: str) -> dict[str, dict]:
    if not _python_test_path(path):
        return {}
    tree = ast.parse(source, filename=path)
    lines = source.splitlines(keepends=True)
    module = path.removesuffix(".py").replace("/", ".")
    found = {}

    def is_pytestmark(node: ast.stmt) -> bool:
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign))
            else []
        )
        return any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets)

    def visit(
        nodes: list[ast.stmt],
        parents: tuple[str, ...] = (),
        enclosing_source: str = "",
    ) -> None:
        scope_source = enclosing_source + "".join(
            "".join(lines[node.lineno - 1:node.end_lineno])
            for node in nodes
            if is_pytestmark(node)
        )
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                visit(
                    node.body,
                    (*parents, node.name),
                    scope_source + "".join(lines[start - 1:node.lineno - 1]),
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                qualified = ".".join((*parents, node.name))
                found[f"python:{path}:{qualified}"] = {
                    "source": scope_source + "".join(lines[start - 1:node.end_lineno]),
                    "aliases": {
                        f"{module}.{qualified}",
                        f"{path}::{'::'.join((*parents, node.name))}",
                    },
                    "language": "python",
                    "display": f"{path}:{qualified}",
                    "path": path,
                    "qualified": qualified,
                }
    visit(tree.body)
    return found


_RUST_TEST = re.compile(
    r"(?m)(?P<attrs>(?:^[ \t]*#\[[^\n]+\][ \t]*\n)+)"
    r"^[ \t]*(?:(?:pub(?:\([^)]*\))?)[ \t]+)?(?:async[ \t]+)?fn[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
_RUST_MOD = re.compile(
    r"(?m)(?P<attrs>(?:^[ \t]*#\[[^\n]+\][ \t]*\n)*)"
    r"^[ \t]*(?:(?:pub(?:\([^)]*\))?)[ \t]+)?mod[ \t]+"
    r"[A-Za-z_][A-Za-z0-9_]*[ \t]*\{"
)
_RUST_MASK = re.compile(
    r"//[^\n]*|/\*.*?\*/|r(?P<hashes>#+)?\".*?\"(?P=hashes)|\"(?:\\.|[^\"\\])*\"|"
    r"'(?:[^'\\\r\n\t]|\\(?:[nrt0\\'\"]|x[0-7][0-9a-fA-F]|u\{[0-9a-fA-F]{1,6}\}))'",
    re.DOTALL,
)


def _rust_tests(path: str, source: str) -> dict[str, dict]:
    masked = _RUST_MASK.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), source)

    def block_end(brace: int) -> int | None:
        depth = 0
        for index in range(brace, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    modules = []
    for module in _RUST_MOD.finditer(masked):
        end = block_end(module.end() - 1)
        if end is not None:
            modules.append((
                module.end() - 1,
                end,
                source[module.start("attrs"):module.end("attrs")],
            ))

    found, occurrences = {}, {}
    for match in _RUST_TEST.finditer(source):
        if not re.search(r"#\[(?:[A-Za-z_][A-Za-z0-9_]*::)*test(?:\([^]]*\))?\]", match["attrs"]):
            continue
        brace = masked.find("{", match.end("name"))
        end = block_end(brace) if brace >= 0 else None
        if end is None:
            continue
        name = match["name"]
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        enclosing = "".join(
            attrs for start, stop, attrs in modules
            if start < match.start() < stop
        )
        found[f"rust:{path}:{name}:{occurrence}"] = {
            "source": enclosing + source[match.start():end],
            "aliases": {name},
            "language": "rust",
            "display": f"{path}:{name}",
        }
    return found


def _tests_at(ref: str) -> tuple[dict[str, dict], str]:
    listing = timed(["git", "ls-tree", "-r", "--name-only", "-z", ref])
    if listing.returncode:
        return {}, listing.stdout + listing.stderr
    found = {}
    for path in listing.stdout.split("\0"):
        if not path.endswith((".py", ".rs")):
            continue
        blob = timed(["git", "show", f"{ref}:{path}"])
        if blob.returncode:
            return {}, blob.stdout + blob.stderr
        try:
            tests = _python_tests(path, blob.stdout) if path.endswith(".py") else _rust_tests(path, blob.stdout)
        except SyntaxError:
            continue
        found.update(tests)
    return found, ""


def check_existing_tests(base: str) -> tuple[bool, str]:
    common = timed(["git", "merge-base", base, "HEAD"])
    if common.returncode:
        return False, common.stdout + common.stderr
    before, error = _tests_at(common.stdout.strip())
    if error:
        return False, error
    after, error = _tests_at("HEAD")
    if error:
        return False, error
    changed = [
        f"{record['display']}: {'deleted' if key not in after else 'modified'}"
        for key, record in before.items()
        if key not in after or record["source"] != after[key]["source"]
    ]
    return not changed, "\n".join(changed)


def _matches_test_id(test_id: str, record: dict) -> bool:
    return test_id in record["aliases"] or (
        record["language"] == "rust" and test_id.rsplit("::", 1)[-1] in record["aliases"]
    )


_UNITTEST_ONE = """import importlib.util, sys, unittest
path, name = sys.argv[1:]
spec = importlib.util.spec_from_file_location("_factory_repro", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(name, module))
raise SystemExit(not result.wasSuccessful())
"""


def _repro_command(test_id: str, record: dict) -> tuple[list[str], bool]:
    language = record["language"]
    for check in cfg.checks:
        if language == "python" and "pytest" in check.run:
            selector = next(alias for alias in record["aliases"] if "::" in alias)
            return (
                [*check.run[:check.run.index("pytest") + 1], selector],
                check.name in GPU_CHECKS,
            )
        if language == "python" and "unittest" in check.run:
            runner = check.run.index("unittest")
            prefix = check.run[:runner - 1] if runner and check.run[runner - 1] == "-m" else check.run[:runner]
            return (
                [*(prefix or [sys.executable]), "-c", _UNITTEST_ONE, record["path"], record["qualified"]],
                check.name in GPU_CHECKS,
            )
        if language == "rust" and "cargo" in check.run and "test" in check.run:
            separator = check.run.index("--") if "--" in check.run else len(check.run)
            return (
                [*check.run[:separator], test_id, *check.run[separator:]],
                check.name in GPU_CHECKS,
            )
    if language == "python" and "::" not in test_id:
        return [sys.executable, "-c", _UNITTEST_ONE, record["path"], record["qualified"]], False
    if language == "python":
        return [sys.executable, "-m", "pytest", test_id], False
    return ["cargo", "test", test_id], False


def _run_at(
    ref: str, command: list[str]
) -> tuple[subprocess.CompletedProcess, bool]:
    with tempfile.TemporaryDirectory(prefix="factory-repro-") as directory:
        checkout = Path(directory) / "checkout"
        added = timed(["git", "worktree", "add", "--detach", str(checkout), ref])
        if added.returncode:
            return added, False
        try:
            return timed(command, cwd=checkout, pythonpath=checkout), True
        finally:
            timed(["git", "worktree", "remove", "--force", str(checkout)])


def check_bug_reproduction(
    base: str, test_id: str | None, no_repro_test: str | None, execution=None
) -> list[tuple[str, str, str]]:
    if no_repro_test:
        return [("bug-repro", f"SKIP (No repro test: {no_repro_test})", "")]
    if not test_id:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a `Repro test:` handoff line or `No repro test:` ticket line",
        )]
    commits = timed(["git", "rev-list", "--reverse", "--first-parent", f"{base}..HEAD"])
    if commits.returncode:
        return [("bug-repro", "FAIL", commits.stdout + commits.stderr)]
    revisions = commits.stdout.split()
    if len(revisions) < 2:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a test commit followed by a separate fix commit",
        )]
    before, error = _tests_at(base)
    if error:
        return [("bug-repro", "FAIL", error)]
    for test_commit in revisions:
        with_test, error = _tests_at(test_commit)
        if error:
            return [("bug-repro", "FAIL", error)]
        added = [
            (key, record) for key, record in with_test.items()
            if key not in before and _matches_test_id(test_id, record)
        ]
        if added:
            break
    else:
        return [(
            "bug-repro",
            "FAIL",
            f"no bug-ticket commit adds declared reproduction test `{test_id}`",
        )]
    if test_commit == revisions[-1]:
        return [(
            "bug-repro",
            "FAIL",
            "bug ticket needs a test commit followed by a separate fix commit",
        )]
    test_key, test_record = added[0]
    command, exclusive = _repro_command(test_id, test_record)
    gpu_lock = _acquire_exclusive(execution) if exclusive else None
    try:
        reproduced, ran = _run_at(test_commit, command)
        if not ran or reproduced.returncode == 124:
            return [(
                "bug-repro-before",
                "FAIL",
                reproduced.stdout + reproduced.stderr,
            )]
        if reproduced.returncode == 0:
            return [(
                "bug-repro-before",
                "FAIL",
                "test does not reproduce the bug: it passes at the test commit",
            )]
        results = [(
            "bug-repro-before",
            "EXPECTED FAIL",
            reproduced.stdout + reproduced.stderr,
        )]
        head, error = _tests_at("HEAD")
        if error:
            return [*results, ("bug-repro-head", "FAIL", error)]
        head_record = head.get(test_key)
        if head_record is None or not _matches_test_id(test_id, head_record):
            return [*results, (
                "bug-repro-head",
                "FAIL",
                f"declared reproduction test `{test_id}` is missing at HEAD",
            )]
        if head_record["source"] != test_record["source"]:
            return [*results, (
                "bug-repro-head",
                "FAIL",
                f"declared reproduction test changed at HEAD: `{test_id}`",
            )]
        fixed = timed(command, cwd=Path.cwd(), pythonpath=Path.cwd())
        return [
            *results,
            (
                "bug-repro-head",
                "PASS" if fixed.returncode == 0 else "FAIL",
                "" if fixed.returncode == 0 else "reproduction test still fails at HEAD\n" + fixed.stdout + fixed.stderr,
            ),
        ]
    finally:
        if gpu_lock is not None:
            _release_exclusive(execution, gpu_lock)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Quality gate for agent worktrees.")
    parser.add_argument("--base", default=None, help="base ref for leak scan and protected paths")
    parser.add_argument(
        "--report", default=".factory/gate-report.md", help="report output path"
    )
    parser.add_argument(
        "--skip",
        default="",
        help="comma-separated check names to skip "
        "(conflict-markers, leak-scan, <configured names>)",
    )
    parser.add_argument(
        "--check-timeout",
        type=int,
        default=None,
        help="per-check timeout in seconds",
    )
    parser.add_argument("--protected-override", action="store_true",
                        help="human-authorized protected-path override")
    parser.add_argument("--test-override", action="store_true",
                        help="ticket-authorized existing-test override")
    parser.add_argument("--add-only-override", action="store_true",
                        help="manager- or human-authorized edits to add-only paths")
    parser.add_argument("--bug", action="store_true",
                        help="enforce failing-test-first and existing-test protection")
    parser.add_argument("--repro-test", help="test ID named by the worker handoff")
    parser.add_argument("--no-repro-test", help="ticket-author or authenticated-triage reason a reproduction test is impractical")
    parser.add_argument("--upstream", action="append", default=[], metavar="REF",
                        help="upstream sync: first REF is the tip, later REFs the staged head (repeatable); "
                        "protected-path hits equal to the staged head, or to the tip for paths upstream changed, "
                        "are upstream-authored, not worker edits; the leak scan diffs from the tip")
    parser.add_argument("--tier", choices=config.CHECK_TIERS, default="attempt",
                        help="attempt: every check except `when = \"pr\"`/`\"release\"` ones (reported SKIP); "
                        "pr: only the `when = \"pr\"` checks (release ones reported SKIP); "
                        "release: only the `when = \"release\"` checks")
    parser.add_argument("--blocking-mutation", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    configure(config.load())
    with lifecycle.scope(cfg.factory / "events.jsonl", "gate") as execution:
        return execute(args, execution)


def toolchain() -> dict[str, str]:
    values = {}
    for name, command in (
        ("rocm", ["hipconfig", "--version"]),
        ("kernel", ["uname", "-r"]),
        ("amdgpu", ["modinfo", "-F", "version", "amdgpu"]),
    ):
        try:
            result = subprocess.run(
                command, capture_output=True, encoding="utf-8", errors="replace",
                timeout=5, check=False,
            )
            values[name] = result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            values[name] = "unavailable"
    return values


def execute(args: argparse.Namespace, execution) -> int:
    global CHECK_TIMEOUT
    host = toolchain()
    execution.emit("toolchain", **host)
    if args.base is None:
        args.base = f"origin/{cfg.main}"
    if args.check_timeout is not None:
        CHECK_TIMEOUT = args.check_timeout
    skip = {name.strip() for name in args.skip.split(",") if name.strip()}
    if LEAK_RE is None:
        skip.add("leak-scan")

    if args.tier == "attempt":
        if not args.protected_override:
            passed, reason, detail = check_protected_paths(
                args.base, args.upstream, args.add_only_override
            )
            if not passed:
                execution.outcome, execution.reason = "project_escalation", reason
                report = Path(args.report)
                report.parent.mkdir(parents=True, exist_ok=True)
                note = f" ({ADD_ONLY_NOTE})" if reason == "add_only_paths" else ""
                report.write_text(f"# Gate report\n\n- protected-paths: FAIL{note}\n\n"
                                  "## protected-paths failure\n\n```diff\n" + detail + "\n```\n")
                print(f"report: {report}\ngate FAIL: protected-paths")
                return 1
        if args.bug and not (
            args.protected_override or args.test_override or args.add_only_override
        ):
            passed, detail = check_existing_tests(args.base)
            if not passed:
                execution.outcome, execution.reason = "project_escalation", "protected_tests"
                report = Path(args.report)
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    "# Gate report\n\n"
                    "- protected-tests: FAIL (existing test deleted or modified)\n\n"
                    "## protected-tests failure\n\n```\n" + detail + "\n```\n"
                )
                print(f"report: {report}\ngate FAIL: protected-tests")
                return 1
    # attempt lists every configured check, pr the pr+release ones, release only its own;
    # listed checks of a later tier are reported `SKIP (<tier> tier)` instead of run.
    rank = config.CHECK_TIERS.index(args.tier)
    listed = [check for check in cfg.checks if config.CHECK_TIERS.index(check.when) >= rank]
    configured = [
        (
            check.name,
            lambda c=check: run([
                *c.run,
                *(["--blocking"] if args.blocking_mutation and c.name == "mutation" else []),
            ]),
        )
        for check in listed
    ]
    checks = [
        ("conflict-markers", check_conflict_markers),
        *configured,
        ("leak-scan", lambda: check_leaks(args.upstream[0] if args.upstream else args.base)),
    ] if args.tier == "attempt" else configured
    later_tier = {check.name: check.when for check in listed if check.when != args.tier}

    results: list[tuple[str, str, str]] = (
        check_bug_reproduction(
            args.base, args.repro_test, args.no_repro_test, execution
        )
        if args.bug and args.tier == "attempt" else []
    )
    outcome, reason = (
        ("product_feedback", "bug_reproduction_failed")
        if any(status == "FAIL" for _, status, _ in results)
        else ("completed", None)
    )
    test_failures: dict[str, tuple[list[str], dict | None]] = {}
    gpu_lock = None
    try:
        for name, fn in checks:
            if name in skip or name in later_tier:
                results.append((name, f"SKIP ({later_tier[name]} tier)" if name in later_tier else "SKIP", ""))
                continue
            if name in GPU_CHECKS and gpu_lock is None:
                gpu_lock = _acquire_exclusive(execution)
            with lifecycle.scope(
                cfg.factory / "events.jsonl", "gate-check", lock=GPU_LOCK if gpu_lock else None
            ) as check:
                check.emit("check", check=name)
                passed, output = fn()
                if not passed and check.outcome == "completed":
                    check.outcome = "product_feedback"
                    check.reason = "check_failed"
                fields = {}
                if not passed:
                    failed_tests = failed_test_ids(output)
                    fields["failed_tests"] = failed_tests
                    if name.casefold() in {"test", "tests", "pytest", "unittest"}:
                        summary = first_failure_summary(
                            output, failed_tests[0]
                        ) if failed_tests else None
                        test_failures[name] = failed_tests, summary
                        if summary is not None:
                            fields["failure_summary"] = summary
                if fields.get("failed_tests"):
                    fields["changed_paths"] = changed_paths(args.base)
                check.emit("result", check=name, passed=passed, **fields)
                status = _check_status(name, passed, output, args.blocking_mutation)
                results.append((name, status, output))
            if not passed and (outcome == "completed" or check.outcome == "unknown"):
                outcome, reason = check.outcome, check.reason
    finally:
        if gpu_lock is not None:
            _release_exclusive(execution, gpu_lock)

    lines = [
        "# Gate report" if args.tier == "attempt" else f"# Gate report ({args.tier} tier)", "", "## Host toolchain",
        f"ROCm: {host['rocm']}", f"Kernel: {host['kernel']}", f"amdgpu: {host['amdgpu']}", "",
    ]
    for name, status, _ in results:
        lines.append(f"- {name}: {status}")
    for name, status, output in results:
        # pr-tier checks may be report-only (exit 0, findings on stdout); the reviewer sees
        # them only through this report, so keep their output when they pass too.
        if status == "FAIL" or (args.tier == "pr" and status == "PASS" and output.strip()):
            tail = "\n".join(output.splitlines()[-TAIL_LINES:])
            lines += ["", f"## {name} {'failure' if status == 'FAIL' else 'output'}"]
            if name in test_failures:
                failed_tests, summary = test_failures[name]
                lines += ["", *failure_summary_lines(failed_tests, summary), ""]
            lines += ["```", tail, "```"]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n")

    failed = [name for name, status, _ in results if status == "FAIL"]
    summary = "gate FAIL: " + ", ".join(failed) if failed else "gate PASS"
    print(f"report: {report}")
    print(summary)
    execution.outcome, execution.reason = outcome, reason
    return 1 if failed else 0
