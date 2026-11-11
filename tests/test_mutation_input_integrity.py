"""Proof-mode tracked-input integrity: the bytes a mutation-v1 run enumerates, baselines and
mutates are the pinned HEAD's, checked by the gate and by the producer.

Executed for real on a tiny bounded fixture repository (never a real repository campaign):
scripts.mutation.main in proof mode; its shared-rule scope (real git); the tracked-byte
check (real `git ls-tree` plus file hashing); the production sample() (ExceptionReplacer
applicability filter, key sort, on_changed_line hot/cold split, seeded shuffle, CAP);
child preparation by the real `git clone --shared --no-checkout` and shutil.copytree; the
worker queue and threads; the tally and the completion record; and, in the caller cases,
the real dispatcher -> gate -> risk-check -> head-acceptance chain of the witness harness.

Synthetic boundaries (disclosed; cosmic-ray 8.7.0 lives in the `mutation` dependency group,
which these focused runs do not install):
- cosmic_ray.commands.init / cosmic_ray.work_db.use_db: a tiny ast enumerator (one
  NumberReplacer per int literal, one ExceptionReplacer per except-clause type) that reads
  the files exactly where cosmic-ray would (relative to the runner's cwd).
- cosmic_ray.mutating.mutate_and_test: a recorder that never mutates; it checks the copy's
  tracked bytes against HEAD independently (git cat-file) and returns KILLED.
- attrs.evolve: a namespace copy.
- The baseline suite child (`env -C <copy> ... -m scripts.mutation --test-module ''`): an
  adapter that runs a scripted action in that copy and exits 0. No suite, mutant, real
  signal or timeout runs; scripts/mutation.py in the fixture is tracked bytes only.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import io
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import factory
from factory import config, dispatch, gate, lifecycle, mutation_scope
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests.test_mutation_scope import REAL_RUN, build_repo, git

REAL_COPYTREE = shutil.copytree
COLD = "".join(f"v{i} = {i}\n" for i in range(210))  # 210 unchanged candidates: CAP applies
BASE_SRC = "import config\na = 10\nb = 20\n" + COLD
HEAD_SRC = ("import config\na = 1\nb = 2\ntry:\n    pass\nexcept config.ConfigError:\n    pass\n"
            "except ValueError:\n    pass\n" + COLD)  # changed lines 2-9
TRACKED = {"factory/example.py": BASE_SRC, "tests/test_example.py": "import unittest\n",
           "scripts/mutation.py": "# runner stand-in: tracked bytes only, never executed here\n",
           "AGENTS.md": "feature map\n"}
HEAD_FILES = {"factory/example.py": HEAD_SRC}
# The hot candidates the real sample() must keep: a = 1, b = 2, `except ValueError`
# (the dotted `except config.ConfigError` is filtered as inapplicable).
HOT = {("factory/example.py", (2, 4), "core/NumberReplacer"),
       ("factory/example.py", (3, 4), "core/NumberReplacer"),
       ("factory/example.py", (8, 7), "core/ExceptionReplacer")}
DIRTY_INPUTS = ("factory/example.py", "tests/test_example.py", "scripts/mutation.py")

# scripts/mutation.py changed_lines() at baseline 235025a, verbatim.
BASELINE_CHANGED_LINES = '''def changed_lines() -> dict[str, set[int]]:
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
'''


class Outcome:  # stands in for cosmic_ray.work_item.TestOutcome
    KILLED = SimpleNamespace(value="killed")
    SURVIVED = SimpleNamespace(value="survived")
    INCOMPETENT = SimpleNamespace(value="incompetent")


def killed() -> SimpleNamespace:
    return SimpleNamespace(test_outcome=Outcome.KILLED, worker_outcome=SimpleNamespace(value="normal"),
                           diff="-a = 1\n+a = 2\n", output="")


def enumerate_candidates(paths) -> list:
    """The cosmic-ray init stand-in: candidates from the files at the runner's cwd."""
    items = []
    for path in paths:
        for node in ast.walk(ast.parse(Path(path).read_text())):
            if isinstance(node, ast.Constant) and type(node.value) is int:
                operator, span = "core/NumberReplacer", node
            elif isinstance(node, ast.ExceptHandler) and node.type is not None:
                operator, span = "core/ExceptionReplacer", node.type
            else:
                continue
            items.append(SimpleNamespace(mutations=[SimpleNamespace(
                module_path=Path(path), operator_name=operator, occurrence=0,
                start_pos=(span.lineno, span.col_offset), end_pos=(span.end_lineno, span.end_col_offset))]))
    return items


def head_bytes(root: Path, head: str) -> dict[str, bytes]:
    """Independent of the production check: every tracked path's blob at `head`."""
    names = REAL_RUN(["git", "-C", str(root), "ls-tree", "-r", "-z", "--name-only", head],
                     capture_output=True, check=True).stdout.decode().split("\0")
    return {name: REAL_RUN(["git", "-C", str(root), "cat-file", "blob", f"{head}:{name}"],
                           capture_output=True, check=True).stdout for name in names if name}


def matches_head(directory: Path, expected: dict[str, bytes]) -> bool:
    return all((directory / name).is_file() and (directory / name).read_bytes() == data
               for name, data in expected.items())


@contextlib.contextmanager
def producer_boundaries(log: dict, *, root: Path, head: str, baseline=None, corrupt=None,
                        workers: int = 2):
    """Only the synthetic boundaries in the module docstring; everything else is real."""
    log.update(enumerated=[], clones=[], baselines=[], mutants=[])
    expected = head_bytes(root, head)

    @contextlib.contextmanager
    def use_db(path):
        yield SimpleNamespace(work_items=[], path=path)

    def init(paths, db, _config):
        log["enumerated"].append([str(p) for p in paths])
        db.work_items.extend(enumerate_candidates(paths))

    def mutate_and_test(local, command, timeout):
        spec = local[0]
        copy = next(Path(c) for c in log["clones"] if str(spec.module_path).startswith(f"{c}/"))
        rel = Path(spec.module_path).relative_to(copy).as_posix()
        assert command == mutation.test_command(copy, rel), command
        log["mutants"].append(((rel, spec.start_pos, spec.operator_name), matches_head(copy, expected)))
        return killed()

    def process(cmd, **kwargs):
        if cmd[:2] == ["git", "clone"]:
            log["clones"].append(cmd[-1])
            return REAL_RUN(cmd, **kwargs)  # the real child preparation
        if cmd[:1] == ["git"]:
            return REAL_RUN(cmd, **kwargs)
        if cmd[:1] == ["env"]:  # the baseline suite child
            copy = Path(cmd[2])
            assert cmd == shlex.split(mutation.test_command(copy)), cmd
            log["baselines"].append((str(copy), matches_head(copy, expected)))
            if baseline is not None:
                baseline(copy)
            return subprocess.CompletedProcess(cmd, 0, "baseline ok\n", None)
        raise AssertionError(f"unexpected runner process: {cmd}")

    def copytree(src, dst, *args, **kwargs):  # shutil recurses through this name
        result = REAL_COPYTREE(src, dst, *args, **kwargs)
        if corrupt is not None and str(dst) in log["clones"]:  # a prepared child, fully copied
            corrupt(Path(dst))
        return result

    cosmic = types.ModuleType("cosmic_ray")
    cosmic.commands = SimpleNamespace(init=init)
    cosmic.work_db = SimpleNamespace(use_db=use_db)
    cosmic.mutating = SimpleNamespace(mutate_and_test=mutate_and_test)
    cosmic.work_item = SimpleNamespace(TestOutcome=Outcome)
    attrs = types.ModuleType("attrs")
    attrs.evolve = lambda value, **changes: SimpleNamespace(**{**vars(value), **changes})
    modules = {"attrs": attrs, "cosmic_ray": cosmic, "cosmic_ray.commands": cosmic.commands,
               "cosmic_ray.work_db": cosmic.work_db, "cosmic_ray.mutating": cosmic.mutating,
               "cosmic_ray.work_item": cosmic.work_item}
    with mock.patch.dict(sys.modules, modules), \
            mock.patch.object(mutation, "WORKERS", workers), \
            mock.patch.object(mutation.shutil, "copytree", copytree), \
            mock.patch.object(mutation.subprocess, "run", process):
        yield log


def edit(path: Path, text: str = "# dirty\n") -> None:
    path.write_text(path.read_text() + text)


@contextlib.contextmanager
def unimportable_scope(replacement=None):
    """`from factory import mutation_scope` fails (None) or yields `replacement`."""
    saved = factory.__dict__.pop("mutation_scope")
    if replacement is not None:
        factory.mutation_scope = replacement
    try:
        with mock.patch.dict(sys.modules, {"factory.mutation_scope": replacement}):
            yield
    finally:
        factory.mutation_scope = saved


class ProducerFixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.base, self.head = build_repo(self.root, TRACKED, HEAD_FILES)
        self.proof = self.root.parent / "proof" / "record.json"
        self.proof.parent.mkdir()
        for patcher in (mock.patch.dict(os.environ), contextlib.chdir(self.root)):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def produce(self, argv=("--blocking",), *, proof: bool = True, **boundaries) -> tuple[int, str, dict]:
        if self.proof.exists():
            self.proof.unlink()
        if proof:
            os.environ[mutation.PROOF_ENV] = str(self.proof)
            os.environ[mutation.PROOF_BASE_ENV] = self.base
        log: dict = {}
        out = io.StringIO()
        with producer_boundaries(log, root=self.root, head=self.head, **boundaries), \
                contextlib.redirect_stdout(out):
            code = mutation.main(list(argv))
        return code, out.getvalue(), log

    def record(self) -> dict | None:
        return json.loads(self.proof.read_text()) if self.proof.exists() else None

    def verdict(self, code: int) -> str:
        data = self.proof.read_bytes() if self.proof.exists() else None
        return gate.read_mutation_proof(data, code, mutation_scope.compute(self.base))[0]

    def assert_rejected(self, code: int, log: dict, reason: str, *, enumerated: bool, baseline: bool) -> None:
        self.assertEqual((code, self.record()["status"], self.record()["reason"]), (1, "error", reason))
        self.assertEqual(self.verdict(code), "FAIL")
        self.assertEqual(bool(log["enumerated"]), enumerated)
        self.assertEqual(bool(log["baselines"]), baseline)
        self.assertEqual(log["mutants"], [])  # rejected before any mutation began


class ExecutedEnumerationTest(ProducerFixture):
    def test_real_sample_hot_order_cap_and_applicability(self) -> None:
        lines = mutation.scope_lines(self.base, [])
        self.assertEqual(lines, {"factory/example.py": set(range(2, 10))})
        log: dict = {}
        with producer_boundaries(log, root=self.root, head=self.head), \
                tempfile.TemporaryDirectory() as scratch:
            sampled, hot = mutation.sample(lines, Path(scratch))
            again, _ = mutation.sample(lines, Path(scratch))
        keys = [(str(m.module_path), m.start_pos, m.operator_name) for m in sampled]
        self.assertEqual(log["enumerated"], [["factory/example.py"], ["factory/example.py"]])
        self.assertEqual((hot, len(sampled)), (3, mutation.CAP))  # 213 enumerated, 1 inapplicable
        self.assertEqual(set(keys[:hot]), HOT)
        self.assertTrue(all(mutation.on_changed_line(m, lines) for m in sampled[:hot]))
        self.assertFalse(any(mutation.on_changed_line(m, lines) for m in sampled[hot:]))
        self.assertNotIn(("factory/example.py", (6, 7), "core/ExceptionReplacer"), keys)
        self.assertEqual(keys, [(str(m.module_path), m.start_pos, m.operator_name) for m in again])
        # The seeded order, recomputed here from the enumerated candidates.
        rng = random.Random(mutation.SEED)
        hot_sorted = sorted(HOT, key=lambda k: (k[0], k[1]))
        cold_sorted = [("factory/example.py", (10 + i, len(f"v{i} = ")), "core/NumberReplacer")
                       for i in range(210)]
        rng.shuffle(hot_sorted)
        rng.shuffle(cold_sorted)
        self.assertEqual(keys, (hot_sorted + cold_sorted)[:mutation.CAP])

    def test_clean_proof_run_executes_enumeration_children_and_bookkeeping(self) -> None:
        def untracked_outputs(copy: Path) -> None:  # ordinary test outputs are allowed
            (copy / "tests" / "__pycache__").mkdir(exist_ok=True)
            (copy / "tests" / "__pycache__" / "test_example.cpython.pyc").write_bytes(b"\0")
            (copy / "baseline-output.log").write_text("ok\n")

        code, output, log = self.produce(baseline=untracked_outputs)
        record = self.record()
        self.assertEqual((code, record["status"], record["reason"]), (0, "complete", "no-blocking-survivors"))
        self.assertEqual(record["counts"], {**dict.fromkeys(mutation.PROOF_COUNTS, 0),
                                            "planned": 3, "ran": 3, "killed": 3})
        self.assertIn("mutants: 3 sampled (seed 197, cap 200), 3 on changed lines first, 2 workers", output)
        self.assertEqual(log["enumerated"], [["factory/example.py"]])
        self.assertEqual(len(log["clones"]), 2)
        self.assertEqual(log["baselines"], [(log["clones"][0], True)])
        self.assertEqual({key for key, _ in log["mutants"]}, HOT)  # blocking: hot candidates only
        self.assertTrue(all(intact for _, intact in log["mutants"]))
        self.assertEqual(self.verdict(code), "PASS")


class ProducerIntegrityTest(ProducerFixture):
    def test_dirty_tracked_inputs_at_the_same_head_are_rejected_before_enumeration(self) -> None:
        for rel in DIRTY_INPUTS:
            with self.subTest(rel=rel):
                edit(self.root / rel)
                self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.head)
                code, _, log = self.produce()
                self.assert_rejected(code, log, "dirty-tracked-inputs", enumerated=False, baseline=False)
                self.assertEqual(log["clones"], [])
                git(self.root, "checkout", "-q", "--", rel)

    def test_deleted_tracked_input_is_rejected(self) -> None:
        (self.root / "tests" / "test_example.py").unlink()
        code, _, log = self.produce()
        self.assert_rejected(code, log, "dirty-tracked-inputs", enumerated=False, baseline=False)

    def test_each_corrupted_prepared_child_is_rejected_before_baseline(self) -> None:
        cases = {"copy0": lambda copy: edit(copy / "factory" / "example.py"),
                 "copy1": lambda copy: edit(copy / "tests" / "test_example.py"),
                 "copy1-missing-runner": lambda copy: (copy / "scripts" / "mutation.py").unlink()}
        for name, damage in cases.items():
            with self.subTest(case=name):
                target = name.split("-")[0]
                code, _, log = self.produce(corrupt=lambda c, t=target, d=damage: d(c) if c.name == t else None)
                self.assert_rejected(code, log, "prepared-copy-mismatch", enumerated=True, baseline=False)

    def test_baseline_that_changes_tracked_source_is_rejected_before_mutation(self) -> None:
        cases = {
            "copy source": lambda copy: edit(copy / "factory" / "example.py"),
            "copy test": lambda copy: edit(copy / "tests" / "test_example.py"),
            "checkout runner": lambda copy: edit(self.root / "scripts" / "mutation.py"),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                code, _, log = self.produce(baseline=change)
                self.assert_rejected(code, log, "baseline-changed-inputs", enumerated=True, baseline=True)
                self.assertEqual(log["baselines"][0][1], True)  # the copy was intact before it ran
                git(self.root, "checkout", "-q", "--", "scripts/mutation.py")

    def test_head_moving_during_baseline_is_rejected_before_mutation(self) -> None:
        def move_head(_copy: Path) -> None:  # same tree, new HEAD commit
            git(self.root, "commit", "-q", "--allow-empty", "--no-verify", "-m", "moved")

        code, _, log = self.produce(baseline=move_head)
        self.assert_rejected(code, log, "baseline-changed-inputs", enumerated=True, baseline=True)


class HelperAvailabilityTest(ProducerFixture):
    def test_missing_scope_helper_fails_closed_in_proof_mode(self) -> None:
        with unimportable_scope(), self.assertRaises(ImportError):
            self.produce()
        self.assertIsNone(self.record())  # no record: the gate reads a missing record
        self.assertEqual(self.verdict(1), "FAIL")

    def test_incompatible_scope_helper_fails_closed_in_proof_mode(self) -> None:
        old = types.ModuleType("factory.mutation_scope")  # draft2-era helper: no byte check
        old.__dict__.update({k: v for k, v in vars(mutation_scope).items()
                             if k not in {"tracked_mismatches", "__name__"}})
        with unimportable_scope(old), self.assertRaises(AttributeError):
            self.produce()
        self.assertIsNone(self.record())
        self.assertEqual(self.verdict(1), "FAIL")

    def test_unopted_branch_never_needs_the_scope_helper(self) -> None:
        with unimportable_scope():
            self.assertEqual(mutation.changed_lines(), {"factory/example.py": set(range(2, 10))})
            code, output, log = self.produce(proof=False, workers=1)
        self.assertEqual(code, 0)
        self.assertIn("mutants: 3 sampled", output)
        self.assertEqual(list(self.proof.parent.iterdir()), [])
        self.assertEqual(len(log["mutants"]), 3)


class LegacyChangedLinesTest(ProducerFixture):
    def baseline_function(self, run):
        namespace = {"subprocess": SimpleNamespace(run=run)}
        # The in-file baseline 235025a literal, run as the reference implementation.
        exec(compile("from __future__ import annotations\n" + BASELINE_CHANGED_LINES,  # noqa: S102
                     "baseline", "exec"), namespace)
        return namespace["changed_lines"]

    def test_source_is_baseline_235025a_verbatim(self) -> None:
        self.assertEqual(inspect.getsource(mutation.changed_lines), BASELINE_CHANGED_LINES)

    def test_command_flags_output_and_errors_match_baseline(self) -> None:
        calls: list = []

        def recorded(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            return REAL_RUN(cmd, **kwargs)

        with mock.patch.object(mutation.subprocess, "run", recorded):
            current = mutation.changed_lines()
        original = self.baseline_function(recorded)()
        self.assertEqual(current, original)
        self.assertEqual(current, {"factory/example.py": set(range(2, 10))})
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[0], (["git", "diff", "--unified=0", "--no-renames", "--diff-filter=AM",
                                     "origin/main...HEAD", "--", "factory/*.py"],
                                    {"capture_output": True, "text": True, "check": True}))
        git(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaises(subprocess.CalledProcessError):
            mutation.changed_lines()
        with self.assertRaises(subprocess.CalledProcessError):
            self.baseline_function(REAL_RUN)()

    def test_parser_edges_match_baseline(self) -> None:
        diffs = [
            "+++ b/factory/a.py\n@@ -1 +1 @@\n@@ -5,0 +6,3 @@\n",
            "+++ b/factory/gone.py\n@@ -1,2 +0,0 @@\n",
            '+++ "b/factory/\\303.py"\n@@ -0,0 +1 @@\n',  # quoted path: baseline KeyError/NameError
            "@@ -0,0 +1 @@\n",
            "+++ b/factory/a.py\n@@ -0,0 +x @@\n",
        ]
        for diff in diffs:
            def run(cmd, d=diff, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, d, "")

            with self.subTest(diff=diff):
                outcomes = []
                for function in (self.baseline_function(run), mutation.changed_lines):
                    with mock.patch.object(mutation.subprocess, "run", run):
                        try:
                            outcomes.append(("ok", function()))
                        except Exception as exc:  # noqa: BLE001 - comparing exception types
                            outcomes.append(("raised", type(exc)))
                self.assertEqual(outcomes[0], outcomes[1])


class IntegrityCallerTest(witness.MutationCallerWitness):
    """The real dispatcher -> gate -> risk-check -> head acceptance chain (witness harness)."""

    def commit_fixture(self) -> None:
        if self.head is None:
            self.base, self.head = build_repo(self.root, TRACKED, HEAD_FILES)

    def producer(self, **boundaries):
        def step(child) -> tuple[str, int]:
            argv = child.args[child.args.index("scripts/mutation.py") + 1:]
            Path("mutation-equivalents.json").write_text("[]\n")  # untracked, as in a checkout
            out = io.StringIO()
            self.log: dict = {}
            with mock.patch.dict(os.environ, child.kwargs["env"], clear=True), \
                    producer_boundaries(self.log, root=self.root, head=self.head, **boundaries), \
                    contextlib.redirect_stdout(out):
                code = mutation.main(argv)
            return out.getvalue(), code
        return step

    def blocked(self, result: dict, why: str) -> None:
        self.assertEqual(self.summary(result), witness.BLOCKED)
        self.assertEqual(result["report_status"], "FAIL")
        self.assertIn("Mutation proof: mutation FAIL\n", self.review_reports[0])
        self.assertIn(f"mutation proof: FAIL ({why}", self.review_reports[0])
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertEqual((row["mutation"], row["pr_gate"], row["mutation_proof"]),
                         ("FAIL", "FAIL", config.MUTATION_PROOF))
        self.assertNotIn("mutation_scope", row)

    def test_clean_real_producer_reaches_approve_and_head_acceptance(self) -> None:
        self.configure()
        result = self.invoke([self.producer()])
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")]})
        self.assertIn("complete: ran 3 of 3 mutants", self.review_reports[0])
        self.assertEqual({key for key, _ in self.log["mutants"]}, HOT)
        self.assertTrue(all(intact for _, intact in self.log["mutants"]))
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertEqual(row["mutation_scope"], mutation_scope.compute(self.base, self.root).fingerprint)

    def test_gate_rejects_dirty_tracked_inputs_at_the_same_head(self) -> None:
        for rel in DIRTY_INPUTS:
            with self.subTest(rel=rel):
                self.tearDown_reset()
                self.configure()
                edit(self.root / rel)
                result = self.invoke([witness.COMPLETE_PASS])  # a true record would pass
                self.assertEqual(self.children, [])  # refused before the check ran
                self.blocked(result, "tracked inputs differ from HEAD")
                git(self.root, "checkout", "-q", "--", rel)

    def test_gate_rejects_tracked_inputs_changed_by_the_check(self) -> None:
        self.configure()

        def altering(child):
            output = witness.COMPLETE_PASS(child)
            edit(self.root / "tests" / "test_example.py")
            return output

        self.blocked(self.invoke([altering]), "tracked inputs changed during the run")

    def test_corrupted_prepared_child_blocks_through_the_caller(self) -> None:
        self.configure()
        step = self.producer(corrupt=lambda copy: edit(copy / "tests" / "test_example.py")
                             if copy.name == "copy1" else None)
        self.blocked(self.invoke([step]), "error (prepared-copy-mismatch)")
        self.assertEqual(self.log["baselines"], [])

    def test_baseline_changing_tracked_source_blocks_through_the_caller(self) -> None:
        self.configure()
        step = self.producer(baseline=lambda copy: edit(copy / "factory" / "example.py"))
        self.blocked(self.invoke([step]), "error (baseline-changed-inputs)")
        self.assertEqual(self.log["mutants"], [])

    def tearDown_reset(self) -> None:
        """Fresh journal/children between subTests of one fixture repository."""
        self.children.clear()
        self.scripts.clear()
        self.gate_exits.clear()
        self.review_reports.clear()
        dispatch._PENDING_REVIEWS.clear()
        dispatch._PENDING_SECOND_REVIEWS.clear()
        if (self.root / ".factory" / "events.jsonl").exists():
            (self.root / ".factory" / "events.jsonl").unlink()


for _name in [name for name in dir(witness.MutationCallerWitness) if name.startswith("test_")]:
    setattr(IntegrityCallerTest, _name, None)


if __name__ == "__main__":
    unittest.main()
