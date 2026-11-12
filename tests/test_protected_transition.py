"""Exact-content protected transition allowance for `.factory.toml` (invocation-only).

API under test (tests-first; the production change is a later phase):

- gate CLI: `--protected-transition .factory.toml=<pre blob>:<post blob>` plus a required
  `--protected-transition-base <commit>`; each given at most once, never one without the other.
- `gate.check_protected_paths(..., transition=(path, pre, post), transition_base=commit)`,
  both keywords defaulting to None.

The only transition the product may ever exempt is the PM-reviewed historical tuple
(base 19457e8…, `.factory.toml`, 36dee1c… -> 7c1c0aa…), known to the product itself, and
only when an invocation opts in explicitly. Supplying any other tuple (unreviewed post
content with its own matching hash, another base whose blob coincides) is refused. The
allowance holds only when the supplied reviewed base and the gate `--base` both resolve to
that commit and it is the merge-base, the merge-base `.factory.toml` is a 100644 blob equal to
`pre`, the HEAD one a 100644 blob equal to `post`, and the working tree file is a
non-executable regular file hashing to `post`. A passing run writes an audit line
`- protected-transition: …` naming path, pre, post and base. Every other protected or
add-only change keeps failing; the 21 externally reviewed `--add-only-allow` pins stay verified.

Fixtures:
- `ReviewedHistoryTest`: a local fixture repository that borrows this checkout's own object
  store read-only (git alternates; no network, nothing written to this checkout) and works in
  per-test detached worktrees of the real reviewed base 19457e8. Fixture commits sit on top
  of it; no commit ID is faked and no verification is patched. Its main checkout is the
  reviewed base itself: the gate loads `.factory.toml` from there (config.repo_root), so the
  configured check set is always the committed PRE, never the worktree's config under review.
- `FabricatedHistoryTest`: a tiny unrelated repository carrying the exact reviewed blobs, used
  only to show a coinciding blob never stands in for the reviewed base.
The fixture gate skips `test`/`ruff` (reported SKIP) and `mutation` is pr-tier: this exercises
the protected guard and the gate's own built-in checks, never the configured check set, and is
not overall proof of anything.

Run: python -m unittest tests.test_protected_transition
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from factory import gate

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ".factory.toml"
TIMEOUT = 120  # seconds per fixture git/gate subprocess
REVIEWED_BASE = "19457e840460e0163e1172f08580538c4dcae767"
BASELINE = "aeee1ce172dd8ad9f504269377b7fd54a862f649"  # already carries the post blob
PRE_BLOB = "36dee1c6386165f2f434c32d09ee98d26c88e7d4"
POST_BLOB = "7c1c0aad5e8bf221df02c1c2329cda354530f1c5"
# The 21 externally reviewed add-only pins (unchanged; no 22nd pin is ever added).
PINS = {
    "tests/test_binding_cli.py": "996be026b1af16c33e715b3da208d0bc9c399501",
    "tests/test_darwin_observer.py": "118ee32009ccfeeee93b3ca860505a2485b3e7b4",
    "tests/test_dashboard_passive.py": "6ab2f6272cfee19fc29dee03834cd7332648edbd",
    "tests/test_dashboard_port.py": "c8f07ad9f52df558ad69e643cd06ac3f0a2e3932",
    "tests/test_decision_fallback.py": "a948fb6d638a101115754be70e2b0e403c91c200",
    "tests/test_dispatch_lifecycle.py": "d570e7bb8ecb42b2595d7d3e4707c7c1ba493fdc",
    "tests/test_evidence.py": "e431f23983a832eb4863db550cf6379fe574b4eb",
    "tests/test_evidence_transport.py": "8a6fd1b5fb011e44d597b2dbaac5b471db549928",
    "tests/test_factory.py": "76648825e18c17c09c56cddf0b84491d57dcd958",
    "tests/test_gate.py": "11619dbb389e29e012dbc192da36cabbedd691e2",
    "tests/test_gate_resources.py": "b6ad3b2389e65a24aaba790de69ef72d157ee07d",
    "tests/test_lifecycle_consumers.py": "b93a39157672390079af812fa24ab609b11bfb1a",
    "tests/test_lifecycle_platform.py": "aca2bf268d6e78fb2b752933fe1b1fe6c7f07786",
    "tests/test_mcp.py": "8f766f82b1379d00e8578d1adc24a9139f5179e0",
    "tests/test_native_capability.py": "3bbaa51d4cc62bf2940bb57e17379d7ac71150bc",
    "tests/test_prime_pilot.py": "adbda41e1b7af97c7d3a834a28b195581c4a7f9f",
    "tests/test_rename_noreplace.py": "f108810d5a67e9c98ae9ca422fb11d282bfc8dae",
    "tests/test_resources.py": "dc8f0669c5cc22eb92972550130c2340d0ba9c21",
    "tests/test_runtime.py": "6f07e7116d5de724f23f1e9d24d9378655ff7bb0",
    "tests/test_subprocess_encoding.py": "c08c55c208f798840bacf68e412fc98c32f2cc51",
    "tests/test_workspace.py": "2ca0c6fe857d294dbe986948dc0e7c38c173d5fa",
}
PROOF_LINE = b'proof = "mutation-v1"\n'
POST = b"""# agent-factory configuration. Docs: https://github.com/mikeroySoft/factory
# Gate checks proposed by District from: --check

[gate]
protected_paths = [".factory.toml", "mutation-equivalents.json", "**/acceptance/**", "**/*review*prompt*", "TRACKING*", "**/TRACKING*"]
add_only_paths = ["tests/**"]

[[gate.check]]
name = "test"
run = [
    "uv",
    "run",
    "--frozen",
    "--no-managed-python",
    "python",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
]
exclusive = false

[[gate.check]]
name = "ruff"
run = ["uvx", "ruff@0.16.10", "check", "."]
exclusive = false

# Report-only: lists surviving mutants of changed factory/ files for the reviewer; exits
# nonzero only when the tool itself fails to run. Runs once per PR head, not per attempt.
[[gate.check]]
name = "mutation"
run = [
    "uv",
    "run",
    "--frozen",
    "--no-managed-python",
    "--group",
    "mutation",
    "python",
    "scripts/mutation.py",
    "--equivalents",
    "mutation-equivalents.json",
]
exclusive = false
when = "pr"
proof = "mutation-v1"

[dashboard]
theme = ".factory-dashboard.css"

[dispatch]
review_rounds = 3
max_active = 4

[release]
auto = true

[manager]
rounds = 4
"""
PRE = POST.replace(PROOF_LINE, b"", 1)
# `test`/`ruff` would run uv/uvx on the fixture: skipped and reported SKIP, never faked PASS.
SKIP = ("--skip", "test,ruff", "--check-timeout", "60")
REFUSED = "- protected-paths: FAIL"
ADD_ONLY = "- protected-paths: FAIL (add-only path removed or changed existing lines)"
AUDIT = "- protected-transition:"
PASS_LINES = ("- conflict-markers: PASS", "- test: SKIP", "- ruff: SKIP",
              "- mutation: SKIP (pr tier)", "- leak-scan: PASS")


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    return git_bytes(repo, *args, stdin=stdin).decode().strip()


def git_bytes(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, input=stdin,
        timeout=TIMEOUT,
    ).stdout


def blob(repo: Path, content: bytes, write: bool = False) -> str:
    return git(repo, "hash-object", *(["-w"] if write else []), "--stdin", stdin=content)


def configure_fixture(repo: Path, hooks: Path) -> None:
    for key, value in (("user.email", "test@example.com"), ("user.name", "Test"),
                       ("core.fileMode", "true"), ("commit.gpgsign", "false"),
                       ("core.hooksPath", str(hooks)), ("gc.auto", "0")):
        git(repo, "config", key, value)
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    # The fixture gate's own report/journal never ride into a later `git add -A`.
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(".factory/\n")


def gate_env(root: Path) -> dict[str, str]:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "XDG_CONFIG_HOME": str(root / "xdg")}
    env.pop("FACTORY_LIFECYCLE_CONTEXT", None)
    return env


def python(*args: str) -> list[str]:
    # -P: the fixture checkout has its own (older) `factory/` package; never import it.
    return [sys.executable, "-P", *args]


def run_gate(repo: Path, root: Path, base: str, *args: str) -> tuple[int, str, str]:
    report = repo / ".factory" / "gate-report.md"
    report.unlink(missing_ok=True)
    proc = subprocess.run(
        python("-m", "factory", "gate", "--base", base, *SKIP, *args),
        cwd=repo, env=gate_env(root), capture_output=True, text=True, check=False,
        timeout=TIMEOUT,
    )
    text = report.read_text() if report.exists() else ""
    return proc.returncode, text, proc.stdout + proc.stderr


def allow(pre: str = PRE_BLOB, post: str = POST_BLOB, path: str = CONFIG,
          base: str | None = REVIEWED_BASE) -> list[str]:
    args = ["--protected-transition", f"{path}={pre}:{post}"]
    return [*args, "--protected-transition-base", base] if base else args


def pin_args(pins: dict[str, str], base: str = REVIEWED_BASE) -> list[str]:
    args = [arg for path, sha in pins.items() for arg in ("--add-only-allow", f"{path}={sha}")]
    return [*args, "--add-only-allow-base", base]


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class GuardAssertions(unittest.TestCase):
    repo: Path

    def check(self, base: str, **kwargs) -> tuple[bool, str, str]:
        """The actual `check_protected_paths`, run from the fixture; a missing transition API
        is an explicit failure rather than a TypeError."""
        missing = {"transition", "transition_base"} - set(
            inspect.signature(gate.check_protected_paths).parameters)
        if missing and {"transition", "transition_base"} & set(kwargs):
            self.fail(f"check_protected_paths lacks the transition API: {sorted(missing)}")
        with working_directory(self.repo):
            return gate.check_protected_paths(base, **kwargs)

    def assert_transition_refused(self, result: tuple[int, str, str]) -> None:
        """Gate ran (option recognised), protected-paths failed with a transition refusal."""
        code, report, out = result
        self.assertNotIn("unrecognized arguments", out)
        self.assertEqual(code, 1, report + out)
        self.assertIn(REFUSED, report, out)
        self.assertIn("protected transition", report.lower(), report)
        self.assertNotIn(AUDIT, report)
        self.assertNotIn("gate PASS", out)

    def assert_refused_in_process(self, result: tuple[bool, str, str]) -> None:
        passed, reason, detail = result
        self.assertEqual((passed, reason), (False, "protected_paths"), detail)
        self.assertIn("protected transition", detail.lower(), detail)

    def assert_parser_rejected(self, result: tuple[int, str, str]) -> None:
        code, report, out = result
        self.assertNotIn("unrecognized arguments", out)
        self.assertEqual(code, 2, out)
        self.assertIn("--protected-transition", out)
        self.assertEqual(report, "", "a rejected invocation must not run the gate")


# -- the explicit invocation API exists ------------------------------------------------------


class TransitionApiTest(unittest.TestCase):
    def test_gate_cli_offers_the_transition_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "xdg").mkdir()
            proc = subprocess.run(
                python("-m", "factory", "gate", "--help"), cwd=temp, env=gate_env(Path(temp)),
                capture_output=True, text=True, check=False, timeout=TIMEOUT,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--protected-transition ", proc.stdout)
        self.assertIn("--protected-transition-base", proc.stdout)

    def test_check_protected_paths_accepts_the_transition_keywords(self) -> None:
        parameters = inspect.signature(gate.check_protected_paths).parameters
        for name in ("transition", "transition_base"):
            self.assertIn(name, parameters)
            self.assertIsNone(parameters[name].default, name)


# -- a coinciding blob in unrelated history never stands in for the reviewed base -------------


class FabricatedHistoryTest(GuardAssertions):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "xdg").mkdir()  # no host config layer leaks into the fixture gate
        (self.root / "hooks").mkdir()
        self.repo = repo = self.root / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        configure_fixture(repo, self.root / "hooks")
        (repo / CONFIG).write_bytes(PRE)
        (repo / "README.md").write_text("fixture\n")
        (repo / "mutation-equivalents.json").write_text("{}\n")
        # r0 and r both carry the reviewed pre blob: a blob match alone never picks the base.
        self.r0 = self.commit("older base")
        (repo / "docs.txt").write_text("fabricated base\n")
        self.r = self.commit("fabricated base")
        git(repo, "checkout", "-q", "-B", "work", self.r)
        (repo / CONFIG).write_bytes(POST)
        self.commit("config transition")

    def commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def test_fabricated_fixture_carries_the_reviewed_blobs(self) -> None:
        self.assertEqual(blob(self.repo, PRE), PRE_BLOB, "fixture pre content is not the reviewed blob")
        self.assertEqual(blob(self.repo, POST), POST_BLOB, "fixture post content is not the reviewed blob")
        self.assertEqual(POST.count(PROOF_LINE), 1)
        pre, post = PRE.splitlines(keepends=True), POST.splitlines(keepends=True)
        at = post.index(PROOF_LINE)
        self.assertEqual(post[:at] + post[at + 1:], pre, "pre must be post minus the one insertion")
        for ref, sha in ((self.r, PRE_BLOB), (self.r0, PRE_BLOB), ("HEAD", POST_BLOB)):
            self.assertTrue(git(self.repo, "ls-tree", ref, "--", CONFIG).startswith(f"100644 blob {sha}"))
        self.assertNotEqual(self.r, REVIEWED_BASE)

    def test_fabricated_base_with_the_reviewed_blobs_is_refused(self) -> None:
        for name, tbase in (("supplied base is the fabricated merge-base", self.r),
                            ("supplied base is an older commit with the pre blob", self.r0)):
            with self.subTest(name):
                self.assert_transition_refused(run_gate(self.repo, self.root, self.r, *allow(base=tbase)))
                self.assert_refused_in_process(self.check(
                    self.r, transition=(CONFIG, PRE_BLOB, POST_BLOB), transition_base=tbase))
        with self.subTest("the known reviewed base is absent from this history"):
            code, report, out = run_gate(self.repo, self.root, self.r, *allow())
            self.assertNotIn("unrecognized arguments", out)
            self.assertIn(code, (1, 2), out)
            self.assertNotIn("gate PASS", out)
            self.assertNotIn(AUDIT, report)


# -- the real reviewed history ----------------------------------------------------------------


class ReviewedHistoryTest(GuardAssertions):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temp.name)
        (cls.root / "xdg").mkdir()
        (cls.root / "hooks").mkdir()
        cls.history = history = cls.root / "history"
        history.mkdir()
        git(history, "init", "-q")
        configure_fixture(history, cls.root / "hooks")
        # Read-only borrow of this checkout's objects: the fixture writes only its own store.
        common = Path(git(ROOT, "rev-parse", "--git-common-dir"))
        objects = (common if common.is_absolute() else ROOT / common).resolve() / "objects"
        (history / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (history / ".git" / "objects" / "info" / "alternates").write_text(f"{objects}\n")
        found = subprocess.run(
            ["git", "-C", str(history), "cat-file", "-e", f"{REVIEWED_BASE}^{{commit}}"],
            capture_output=True, check=False, timeout=TIMEOUT,
        )
        cls.missing = "" if found.returncode == 0 else (
            f"reviewed base {REVIEWED_BASE} is not in this checkout's history ({objects})")
        if not cls.missing:
            # The gate loads `.factory.toml` from the main checkout (config.repo_root), never
            # from the worktree under review: materialize the reviewed base there so the actual
            # config.load sees the committed PRE config and its configured checks.
            git(history, "checkout", "-q", "--detach", REVIEWED_BASE)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temp.cleanup()

    def setUp(self) -> None:
        if self.missing:
            self.fail(self.missing)
        self.repo = Path(tempfile.mkdtemp(dir=self.root)) / "wt"
        git(self.history, "worktree", "add", "-q", "--detach", str(self.repo), REVIEWED_BASE)
        self.addCleanup(subprocess.run, ["git", "-C", str(self.history), "worktree", "remove",
                                         "--force", str(self.repo)], capture_output=True,
                        check=False, timeout=TIMEOUT)

    # -- helpers -------------------------------------------------------------------------

    def head(self, config: bytes | None = POST, base: str = REVIEWED_BASE,
             files: dict[str, bytes] | None = None) -> str:
        git(self.repo, "checkout", "-q", "-f", "--detach", base)
        if config is not None:
            (self.repo / CONFIG).write_bytes(config)
        for path, content in (files or {}).items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_bytes(content)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "fixture change")
        return git(self.repo, "rev-parse", "HEAD")

    def pinned(self) -> dict[str, bytes]:
        return {path: git_bytes(self.history, "cat-file", "blob", sha) for path, sha in PINS.items()}

    def gate(self, *args: str, base: str = REVIEWED_BASE) -> tuple[int, str, str]:
        return run_gate(self.repo, self.root, base, *args)

    def transition(self, *, pre: str = PRE_BLOB, post: str = POST_BLOB,
                   tbase: str = REVIEWED_BASE, base: str = REVIEWED_BASE) -> tuple[int, str, str]:
        return self.gate(*allow(pre, post, base=tbase), base=base)

    def check_transition(self, base: str = REVIEWED_BASE, tbase: str = REVIEWED_BASE,
                         pre: str = PRE_BLOB, post: str = POST_BLOB, **kwargs) -> tuple[bool, str, str]:
        return self.check(base, transition=(CONFIG, pre, post), transition_base=tbase, **kwargs)

    def assert_audited(self, report: str) -> None:
        lines = [line for line in report.splitlines() if line.startswith(AUDIT)]
        self.assertEqual(len(lines), 1, report)
        for pin in (CONFIG, PRE_BLOB, POST_BLOB, REVIEWED_BASE):
            self.assertIn(pin, lines[0])

    # -- premises (pass on the baseline) ---------------------------------------------------

    def test_history_premise_is_the_reviewed_transition(self) -> None:
        self.assertEqual(git(self.history, "rev-parse", f"{REVIEWED_BASE}^{{commit}}"), REVIEWED_BASE)
        self.assertTrue(git(self.history, "ls-tree", REVIEWED_BASE, "--", CONFIG)
                        .startswith(f"100644 blob {PRE_BLOB}\t"))
        git(self.history, "merge-base", "--is-ancestor", REVIEWED_BASE, BASELINE)
        self.assertTrue(git(self.history, "ls-tree", BASELINE, "--", CONFIG)
                        .startswith(f"100644 blob {POST_BLOB}\t"))
        self.assertEqual(git_bytes(self.history, "cat-file", "blob", PRE_BLOB), PRE)
        self.assertEqual(git_bytes(self.history, "cat-file", "blob", POST_BLOB), POST)
        # The gate's config root: a regular, non-executable file equal to the committed PRE.
        main_config = self.history / CONFIG
        self.assertFalse(main_config.is_symlink())
        self.assertTrue(main_config.is_file())
        self.assertFalse(main_config.stat().st_mode & 0o111)
        self.assertEqual(main_config.read_bytes(), PRE)
        self.assertTrue(git(self.history, "ls-files", "-s", "--", CONFIG)
                        .startswith(f"100644 {PRE_BLOB} 0\t"))

    def test_pins_premise_are_baseline_edits_of_reviewed_tests(self) -> None:
        self.assertEqual(len(PINS), 21)
        for path, sha in PINS.items():
            with self.subTest(path):
                before = git(self.history, "ls-tree", REVIEWED_BASE, "--", path)
                self.assertTrue(before.startswith("100644 blob "), before)
                self.assertNotEqual(before.split()[2], sha, "a pin must name an in-place edit")
                self.assertTrue(git(self.history, "ls-tree", BASELINE, "--", path)
                                .startswith(f"100644 blob {sha}\t"))

    def test_fixture_gate_runs_this_checkout_not_the_fixture_package(self) -> None:
        proc = subprocess.run(
            python("-c", "import factory; print(factory.__file__)"), cwd=self.repo,
            env=gate_env(self.root), capture_output=True, text=True, check=True, timeout=TIMEOUT,
        )
        self.assertEqual(Path(proc.stdout.strip()).resolve(), (ROOT / "factory" / "__init__.py").resolve())

    def test_unprotected_change_passes_the_bounded_fixture_gate(self) -> None:
        readme = git_bytes(self.history, "cat-file", "blob", f"{REVIEWED_BASE}:README.md")
        self.head(config=None, files={"README.md": readme + b"\nunprotected edit\n"})
        code, report, out = self.gate()
        self.assertEqual(code, 0, report + out)
        self.assertIn("gate PASS", out)
        for line in PASS_LINES:
            self.assertIn(line, report)
        self.assertNotIn(AUDIT, report)

    def test_reviewed_transition_without_allowance_stays_blocked(self) -> None:
        self.head()
        code, report, out = self.gate()
        self.assertEqual(code, 1, report + out)
        self.assertIn(REFUSED + "\n", report)
        self.assertIn('+proof = "mutation-v1"', report)
        self.assertNotIn(AUDIT, report)
        passed, reason, _ = self.check(REVIEWED_BASE)
        self.assertEqual((passed, reason), (False, "protected_paths"))

    # -- the positives ---------------------------------------------------------------------

    def test_exact_reviewed_transition_passes_the_actual_gate(self) -> None:
        self.head()
        code, report, out = self.transition()
        self.assertEqual(code, 0, report + out)
        self.assertIn("gate PASS", out)
        self.assertNotIn("FAIL", report)
        for line in PASS_LINES:
            self.assertIn(line, report)
        self.assert_audited(report)  # externally auditable: names exactly what was allowed

    def test_exact_reviewed_transition_passes_check_protected_paths(self) -> None:
        self.head()
        self.assertEqual(self.check_transition(), (True, "", ""))

    def test_reviewed_transition_with_the_21_pins_passes_the_protected_guard(self) -> None:
        self.head(files=self.pinned())
        self.assertEqual(self.check_transition(allow=dict(PINS), allow_base=REVIEWED_BASE), (True, "", ""))
        code, report, out = self.gate(*allow(), *pin_args(PINS))
        self.assertNotIn("unrecognized arguments", out)
        self.assertNotIn(REFUSED, report, out)
        # The guard passed and the gate went on to its own checks; leak-scan's verdict on the
        # pinned test bytes is outside this guard's scope and deliberately not asserted.
        for line in ("- conflict-markers: PASS", "- test: SKIP", "- ruff: SKIP", "- mutation: SKIP (pr tier)"):
            self.assertIn(line, report)
        self.assert_audited(report)
        with self.subTest("the pins never imply the transition"):
            code, report, out = self.gate(*pin_args(PINS))
            self.assertEqual(code, 1, report + out)
            self.assertIn(REFUSED + "\n", report)
            passed, reason, _ = self.check(REVIEWED_BASE, allow=dict(PINS), allow_base=REVIEWED_BASE)
            self.assertEqual((passed, reason), (False, "protected_paths"))
        with self.subTest("the transition never implies the pins"):
            code, report, out = self.gate(*allow())
            self.assertEqual(code, 1, report + out)
            self.assertIn(ADD_ONLY, report)
            passed, reason, _ = self.check_transition()
            self.assertEqual((passed, reason), (False, "add_only_paths"))

    # -- the 21 pins stay verified ---------------------------------------------------------

    def test_one_wrong_pin_or_dirty_pinned_byte_is_refused(self) -> None:
        pinned = self.pinned()
        target = "tests/test_gate.py"
        near = dict(PINS, **{target: blob(self.history, pinned[target] + b"# near\n")})
        with self.subTest("one wrong pin"):
            self.head(files=pinned)
            code, report, out = self.gate(*allow(), *pin_args(near))
            self.assertEqual(code, 1, report + out)
            self.assertIn(ADD_ONLY, report)
            self.assertIn(target, report)
            passed, reason, detail = self.check_transition(allow=near, allow_base=REVIEWED_BASE)
            self.assertEqual((passed, reason), (False, "add_only_paths"))
            self.assertIn(target, detail)
        with self.subTest("extra dirty pinned byte"):
            self.head(files=pinned)
            (self.repo / target).write_bytes(pinned[target] + b"\n")
            code, report, out = self.gate(*allow(), *pin_args(PINS))
            self.assertEqual(code, 1, report + out)
            self.assertIn(ADD_ONLY, report)
            self.assertIn(target, report)
            passed, reason, _ = self.check_transition(allow=dict(PINS), allow_base=REVIEWED_BASE)
            self.assertEqual((passed, reason), (False, "add_only_paths"))

    def test_add_only_rules_are_unchanged_by_the_transition(self) -> None:
        listing = git(self.history, "ls-tree", "--name-only", REVIEWED_BASE, "tests/").splitlines()
        target, content = next(
            (path, data) for path in sorted(listing)
            if path.endswith(".py") and path not in PINS
            and len((data := git_bytes(self.history, "cat-file", "blob", f"{REVIEWED_BASE}:{path}"))
                    .splitlines()) > 1
        )
        edited = b"".join(content.splitlines(keepends=True)[:-1])  # drops an existing line
        self.head(files={**self.pinned(), target: edited})
        code, report, out = self.gate(*allow(), *pin_args(PINS))
        self.assertNotIn("unrecognized arguments", out)
        self.assertEqual(code, 1, report + out)
        self.assertIn(ADD_ONLY, report)
        self.assertIn(target, report)
        passed, reason, _ = self.check_transition(allow=dict(PINS), allow_base=REVIEWED_BASE)
        self.assertEqual((passed, reason), (False, "add_only_paths"))

    # -- only the known reviewed tuple -------------------------------------------------------

    def test_unreviewed_post_content_is_refused_even_with_its_own_hash(self) -> None:
        cases = {
            "appended": POST + b"# unreviewed\n",
            "other proof": PRE.replace(b'when = "pr"\n', b'when = "pr"\nproof = "mutation-v2"\n', 1),
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.assertNotEqual(content, PRE)
                self.head(content)
                post = blob(self.repo, content)
                self.assert_transition_refused(self.transition(post=post))
                self.assert_refused_in_process(self.check_transition(post=post))

    def test_wrong_or_swapped_pins_fail(self) -> None:
        self.head()
        near = blob(self.repo, PRE + b"# near\n")
        cases = {
            "wrong pre": {'pre': near},
            "wrong post": {'post': blob(self.repo, POST + b'# near\n')},
            "swapped": {'pre': POST_BLOB, 'post': PRE_BLOB},
        }
        for name, pins in cases.items():
            with self.subTest(name):
                self.assert_transition_refused(self.transition(**pins))
                self.assert_refused_in_process(self.check_transition(**pins))

    def test_reviewed_and_gate_base_must_both_be_the_known_commit(self) -> None:
        later = self.head(config=None, files={"docs-later.txt": b"descendant with the pre blob\n"})
        self.assertTrue(git(self.repo, "ls-tree", later, "--", CONFIG).startswith(f"100644 blob {PRE_BLOB}"))
        drifted = self.head(PRE + b"# base drift\n")
        cases = {  # name: (HEAD's parent, gate --base, supplied reviewed base)
            "both bases a descendant whose blob coincides": (later, later, later),
            "gate base a descendant whose blob coincides": (later, later, REVIEWED_BASE),
            "supplied base a descendant whose blob coincides": (REVIEWED_BASE, REVIEWED_BASE, later),
            "gate base a descendant whose merge-base coincides": (REVIEWED_BASE, BASELINE, REVIEWED_BASE),
            "base content drifted from the pre blob": (drifted, drifted, REVIEWED_BASE),
        }
        for name, (parent, base, tbase) in cases.items():
            with self.subTest(name):
                self.head(base=parent)
                self.assert_transition_refused(self.transition(base=base, tbase=tbase))
                self.assert_refused_in_process(self.check_transition(base=base, tbase=tbase))
        self.head()
        with self.subTest("supplied base is HEAD"):
            self.assert_transition_refused(self.transition(tbase=git(self.repo, "rev-parse", "HEAD")))
        with self.subTest("unresolvable supplied base"):
            code, report, out = self.transition(tbase="0" * 40)
            self.assertNotIn("unrecognized arguments", out)
            self.assertIn(code, (1, 2), out)
            self.assertNotIn("gate PASS", out)
            self.assertNotIn(AUDIT, report)

    def test_allowance_must_name_the_observed_change(self) -> None:
        with self.subTest("nothing transitions against the reviewed base"):
            self.head(config=None, files={"docs-later.txt": b"no config change\n"})
            self.assert_transition_refused(self.transition())
            self.assert_refused_in_process(self.check_transition())
        with self.subTest("the gate base already carries the post blob"):
            settled = self.head()
            git(self.repo, "checkout", "-q", "--detach", settled)
            (self.repo / "docs-later.txt").write_text("later\n")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", "later")
            self.assert_transition_refused(self.transition(base=settled))

    # -- invocation shape -------------------------------------------------------------------

    def test_malformed_or_foreign_pins_are_rejected_by_the_parser(self) -> None:
        self.head()
        other = blob(self.repo, b"{}\n")
        for value in (
            f"{CONFIG}={PRE_BLOB}",
            f"{CONFIG}={PRE_BLOB}:",
            f"{CONFIG}=:{POST_BLOB}",
            f"{CONFIG}={PRE_BLOB[:12]}:{POST_BLOB}",
            f"{CONFIG}={PRE_BLOB.upper()}:{POST_BLOB}",
            f"{CONFIG}={PRE_BLOB}:{POST_BLOB}:{POST_BLOB}",
            f"{CONFIG}={PRE_BLOB}..{POST_BLOB}",
            f"{PRE_BLOB}:{POST_BLOB}",
            f"={PRE_BLOB}:{POST_BLOB}",
            f"./{CONFIG}={PRE_BLOB}:{POST_BLOB}",
            f"/{CONFIG}={PRE_BLOB}:{POST_BLOB}",
            f"{CONFIG}/={PRE_BLOB}:{POST_BLOB}",
            f".Factory.toml={PRE_BLOB}:{POST_BLOB}",
            f"{CONFIG}={POST_BLOB}:{POST_BLOB}",  # not a transition
            f"mutation-equivalents.json={other}:{other}",  # never another protected path
            f"mutation-equivalents.json={PRE_BLOB}:{POST_BLOB}",
            f"tests/test_gate.py={PRE_BLOB}:{POST_BLOB}",
        ):
            with self.subTest(value=value):
                self.assert_parser_rejected(self.gate(
                    "--protected-transition", value, "--protected-transition-base", REVIEWED_BASE))

    def test_duplicate_or_unpaired_options_are_rejected_by_the_parser(self) -> None:
        self.head()
        with self.subTest("duplicate"):
            self.assert_parser_rejected(self.gate(*allow(base=None), *allow()))
        with self.subTest("missing base"):
            self.assert_parser_rejected(self.gate(*allow(base=None)))
        with self.subTest("base without transition"):
            self.assert_parser_rejected(self.gate("--protected-transition-base", REVIEWED_BASE))
        with self.subTest("duplicate base"):
            self.assert_parser_rejected(self.gate(*allow(), "--protected-transition-base", REVIEWED_BASE))

    # -- bytes, modes and types -------------------------------------------------------------

    def test_extra_committed_bytes_fail(self) -> None:
        self.head(POST + b"# extra\n")
        self.assert_transition_refused(self.transition())
        self.assert_refused_in_process(self.check_transition())

    def test_dirty_worktree_bytes_fail(self) -> None:
        cases = {"appended": POST + b"# dirty\n", "reverted to pre": PRE}
        for name, content in cases.items():
            with self.subTest(name):
                self.head()
                (self.repo / CONFIG).write_bytes(content)
                self.assert_transition_refused(self.transition())
                self.assert_refused_in_process(self.check_transition())

    def test_committed_executable_bit_fails(self) -> None:
        git(self.repo, "checkout", "-q", "-f", "--detach", REVIEWED_BASE)
        (self.repo / CONFIG).write_bytes(POST)
        (self.repo / CONFIG).chmod(0o755)
        git(self.repo, "add", "-A")
        git(self.repo, "update-index", "--chmod=+x", CONFIG)
        git(self.repo, "commit", "-q", "-m", "exec config")
        self.assertTrue(git(self.repo, "ls-tree", "HEAD", "--", CONFIG).startswith(f"100755 blob {POST_BLOB}"))
        self.assert_transition_refused(self.transition())
        self.assert_refused_in_process(self.check_transition())

    def test_worktree_executable_bit_fails(self) -> None:
        self.head()
        (self.repo / CONFIG).chmod(0o755)
        self.assert_transition_refused(self.transition())
        self.assert_refused_in_process(self.check_transition())

    def test_committed_symlink_with_the_post_blob_fails(self) -> None:
        git(self.repo, "checkout", "-q", "-f", "--detach", REVIEWED_BASE)
        sha = blob(self.repo, POST, write=True)
        git(self.repo, "update-index", "--cacheinfo", f"120000,{sha},{CONFIG}")
        git(self.repo, "commit", "-q", "-m", "symlink config")
        self.assertTrue(git(self.repo, "ls-tree", "HEAD", "--", CONFIG).startswith(f"120000 blob {POST_BLOB}"))
        (self.repo / CONFIG).write_bytes(POST)  # the working tree stays a regular post file
        self.assert_transition_refused(self.transition())
        self.assert_refused_in_process(self.check_transition())

    def test_worktree_symlink_to_post_bytes_fails(self) -> None:
        self.head()
        target = self.repo.parent / "post.toml"
        target.write_bytes(POST)
        (self.repo / CONFIG).unlink()
        (self.repo / CONFIG).symlink_to(target)
        self.assert_transition_refused(self.transition())
        self.assert_refused_in_process(self.check_transition())

    # -- every other protection stays enforced ----------------------------------------------

    def test_another_protected_path_still_fails(self) -> None:
        cases = {
            "edited": ("mutation-equivalents.json", b'{"x": 1}\n'),
            "added": ("TRACKING.md", b"tracked\n"),
            "nested": ("docs/acceptance/case.txt", b"case\n"),
        }
        for name, (path, content) in cases.items():
            with self.subTest(name):
                self.head(files={path: content})
                code, report, out = self.transition()
                self.assertNotIn("unrecognized arguments", out)
                self.assertEqual(code, 1, report + out)
                self.assertIn(REFUSED, report)
                self.assertIn(path, report)
                self.assertNotIn("gate PASS", out)
                passed, reason, _ = self.check_transition()
                self.assertEqual((passed, reason), (False, "protected_paths"))


if __name__ == "__main__":
    unittest.main()
