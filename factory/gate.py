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

    def visit(
        nodes: list[ast.stmt],
        parents: tuple[str, ...] = (),
        class_decorators: str = "",
    ) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                visit(
                    node.body,
                    (*parents, node.name),
                    class_decorators + "".join(lines[start - 1:node.lineno - 1]),
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                start = min(
                    [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
                )
                qualified = ".".join((*parents, node.name))
                found[f"python:{path}:{qualified}"] = {
                    "source": class_decorators + "".join(lines[start - 1:node.end_lineno]),
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
_RUST_MASK = re.compile(
    r"//[^\n]*|/\*.*?\*/|r(?P<hashes>#+)?\".*?\"(?P=hashes)|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
    re.DOTALL,
)


def _rust_tests(path: str, source: str) -> dict[str, dict]:
    masked = _RUST_MASK.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), source)
    found, occurrences = {}, {}
    for match in _RUST_TEST.finditer(source):
        if not re.search(r"#\[(?:[A-Za-z_][A-Za-z0-9_]*::)*test(?:\([^]]*\))?\]", match["attrs"]):
            continue
        brace = masked.find("{", match.end("name"))
        if brace < 0:
            continue
        depth, end = 0, None
        for index in range(brace, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            continue
        name = match["name"]
        occurrence = occurrences.get(name, 0)
        occurrences[name] = occurrence + 1
        found[f"rust:{path}:{name}:{occurrence}"] = {
            "source": source[match.start():end],
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
        except SyntaxError as exc:
            return {}, f"{ref}:{path}: cannot parse Python tests: {exc}"
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


def _repro_command(test_id: str, record: dict) -> list[str]:
    language = record["language"]
    for check in cfg.checks:
        if language == "python" and "pytest" in check.run:
            selector = next(alias for alias in record["aliases"] if "::" in alias)
            return [*check.run[:check.run.index("pytest") + 1], selector]
        if language == "python" and "unittest" in check.run:
            runner = check.run.index("unittest")
            prefix = check.run[:runner - 1] if runner and check.run[runner - 1] == "-m" else check.run[:runner]
            return [*(prefix or [sys.executable]), "-c", _UNITTEST_ONE, record["path"], record["qualified"]]
        if language == "rust" and "cargo" in check.run and "test" in check.run:
            separator = check.run.index("--") if "--" in check.run else len(check.run)
            suffix = [*check.run[separator:], "--exact"] if separator < len(check.run) else ["--", "--exact"]
            return [*check.run[:separator], test_id, *suffix]
    if language == "python" and "::" not in test_id:
        return [sys.executable, "-c", _UNITTEST_ONE, record["path"], record["qualified"]]
    if language == "python":
        return [sys.executable, "-m", "pytest", test_id]
    return ["cargo", "test", test_id, "--", "--exact"]


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
    base: str, test_id: str | None, no_repro_test: str | None
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
    test_commit = revisions[0]
    with_test, error = _tests_at(test_commit)
    if error:
        return [("bug-repro", "FAIL", error)]
    added = [
        record for key, record in with_test.items()
        if key not in before and _matches_test_id(test_id, record)
    ]
    if not added:
        return [(
            "bug-repro",
            "FAIL",
            f"first bug-ticket commit does not add declared reproduction test `{test_id}`",
        )]
    command = _repro_command(test_id, added[0])
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
    fixed = timed(command, cwd=Path.cwd(), pythonpath=Path.cwd())
    return [
        ("bug-repro-before", "EXPECTED FAIL", reproduced.stdout + reproduced.stderr),
        (
            "bug-repro-head",
            "PASS" if fixed.returncode == 0 else "FAIL",
            "" if fixed.returncode == 0 else "reproduction test still fails at HEAD\n" + fixed.stdout + fixed.stderr,
        ),
    ]


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
    parser.add_argument("--no-repro-test", help="ticket-author reason a reproduction test is impractical")
    parser.add_argument("--upstream", action="append", default=[], metavar="REF",
                        help="upstream sync: first REF is the tip, later REFs the staged head (repeatable); "
                        "protected-path hits equal to the staged head, or to the tip for paths upstream changed, "
                        "are upstream-authored, not worker edits; the leak scan diffs from the tip")
    parser.add_argument("--tier", choices=config.CHECK_TIERS, default="attempt",
                        help="attempt: every check except `when = \"pr\"`/`\"release\"` ones (reported SKIP); "
                        "pr: only the `when = \"pr\"` checks (release ones reported SKIP); "
                        "release: only the `when = \"release\"` checks")
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
        if args.bug and not (args.protected_override or args.test_override):
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
    configured = [(check.name, (lambda c=check: run(c.run))) for check in listed]
    checks = [
        ("conflict-markers", check_conflict_markers),
        *configured,
        ("leak-scan", lambda: check_leaks(args.upstream[0] if args.upstream else args.base)),
    ] if args.tier == "attempt" else configured
    later_tier = {check.name: check.when for check in listed if check.when != args.tier}

    results: list[tuple[str, str, str]] = (
        check_bug_reproduction(args.base, args.repro_test, args.no_repro_test)
        if args.bug and args.tier == "attempt" else []
    )
    outcome, reason = (
        ("product_feedback", "bug_reproduction_failed")
        if any(status == "FAIL" for _, status, _ in results)
        else ("completed", None)
    )
    gpu_lock = None
    gpu_acquired = False
    try:
        for name, fn in checks:
            if name in skip or name in later_tier:
                results.append((name, f"SKIP ({later_tier[name]} tier)" if name in later_tier else "SKIP", ""))
                continue
            if name in GPU_CHECKS and gpu_lock is None:
                gpu_lock = open(GPU_LOCK, "w")  # noqa: SIM115 — lock must outlive the loop
                requested = execution.resource("requested", GPU_LOCK, scope="host", blocking=True)
                if lifecycle._lock_state(lifecycle._lock(GPU_LOCK)) == "held":
                    execution.wait("exclusive_resource", mode="blocking", resource=requested["resource"])
                fcntl.flock(gpu_lock, fcntl.LOCK_EX)
                gpu_acquired = True
                execution.resource("acquired", GPU_LOCK, scope="host", blocking=True)
                execution.wait_end()
            with lifecycle.scope(
                cfg.factory / "events.jsonl", "gate-check", lock=GPU_LOCK if gpu_lock else None
            ) as check:
                check.emit("check", check=name)
                passed, output = fn()
                if not passed and check.outcome == "completed":
                    check.outcome = "product_feedback"
                    check.reason = "check_failed"
                check.emit("result", check=name, passed=passed)
                results.append((name, "PASS" if passed else "FAIL", output))
            if not passed and (outcome == "completed" or check.outcome == "unknown"):
                outcome, reason = check.outcome, check.reason
    finally:
        if gpu_lock is not None:
            fcntl.flock(gpu_lock, fcntl.LOCK_UN)
            gpu_lock.close()
            if gpu_acquired:
                execution.resource("released", GPU_LOCK, scope="host", blocking=True)

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
            lines += ["", f"## {name} {'failure' if status == 'FAIL' else 'output'}", "```", tail, "```"]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n")

    failed = [name for name, status, _ in results if status == "FAIL"]
    summary = "gate FAIL: " + ", ".join(failed) if failed else "gate PASS"
    print(f"report: {report}")
    print(summary)
    execution.outcome, execution.reason = outcome, reason
    return 1 if failed else 0
