"""The repository's own `.factory.toml` opt-in, through REAL config.load and the actual caller.

Config context these fixtures load (and why a candidate edit is not a deployment):
- `config.load()` resolves `<root>/.factory.toml` where root is the MAIN checkout found via
  `git rev-parse --git-common-dir`, never a ticket worktree (config.repo_root). The
  dispatcher pins that checkout to `origin/<main>` before loading (dispatch.pin_root) and
  the gate, started with cwd = the ticket worktree, loads the same main-checkout file.
- Fixture: `main/` is a main checkout whose single commit carries the config under test;
  the fixture-local ref `refs/remotes/origin/main` points at it and the checkout is detached
  there (the state pin_root leaves; pin_root itself is not run - it would `git fetch`).
  `wt/` is a linked worktree (ticket branch) whose head commit edits factory/example.py.
- The config bytes are this checkout's candidate `.factory.toml` (the PM-authorized one-line
  delta). They live only in a temporary fixture repository: NOT DEPLOYMENT. A running
  dispatcher keeps the config it loaded from its pinned origin/main until that ref carries
  the change and the dispatcher reloads; editing a candidate worktree activates nothing,
  which the second case shows.
Only the processes, reviewer and journal rows are faked (tests/test_mutation_caller_witness.py
harness); config.load runs for real in both the dispatcher and the in-process gate.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

from factory import config, dispatch, gate, lifecycle
from scripts import mutation
from tests import test_mutation_caller_witness as witness
from tests.test_mutation_proof_caller import NO_PROOF_PASS, real_runner
from tests.test_mutation_scope import commit, git

REAL_LOAD = config.load  # captured at import, before the witness harness replaces it
CANDIDATE = (witness.CHECKOUT / config.CONFIG_NAME).read_text()
PROOF_LINE = 'proof = "mutation-v1"\n'
BEFORE = CANDIDATE.replace(PROOF_LINE, "", 1)
ORIGIN_URL = "https://github.com/acme/widgets.git"  # remote URL only; nothing is fetched


class RealConfigHarness(witness.MutationCallerWitness):
    def setUp(self) -> None:
        super().setUp()
        self.gate_cmds: list[list[str]] = []
        self.loaded_roots: list[Path] = []
        host = self.root / "host"
        host.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(host)  # empty host layer (environ is restored)

        def gate_load(*args, **kwargs):
            loaded = REAL_LOAD(*args, **kwargs)  # the gate's own real load, recorded
            self.loaded_roots.append(loaded.root)
            return loaded

        patcher = mock.patch.object(gate.config, "load", gate_load)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, main_config: str, worktree_config: str) -> None:
        self.main = self.root / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "remote", "add", "origin", ORIGIN_URL)
        self.base = commit(self.main, {
            config.CONFIG_NAME: main_config, "mutation-equivalents.json": "[]\n",
            "factory/example.py": "value = 1\n",
        }, "main")
        git(self.main, "update-ref", "refs/remotes/origin/main", self.base)
        git(self.main, "checkout", "-q", "--detach", "origin/main")  # as pin_root leaves it
        wt = self.root / "wt"
        git(self.main, "worktree", "add", "-q", "-b", f"agent/{witness.N}", str(wt), "origin/main")
        self.head = commit(wt, {"factory/example.py": "value = 2\n", config.CONFIG_NAME: worktree_config},
                           "ticket head")
        self.root = wt.resolve()
        os.chdir(self.root)

    def configure(self, tier: str = "High", **_unused) -> None:
        self.cfg = REAL_LOAD(self.root)  # the dispatcher's load, started from the worktree
        dispatch.configure(self.cfg)
        self.record_ticket(tier)

    def _dispatch_run(self, cmd, cwd=None, check=True, timeout=None, **kwargs):
        if cmd[:6] == witness.GATE_ARGV:
            self.gate_cmds.append(list(cmd))
        return super()._dispatch_run(cmd, cwd=cwd, check=check, timeout=timeout, **kwargs)

    def mutation_check(self) -> config.Check:
        return next(check for check in self.cfg.checks if check.name == "mutation")


for _name in [name for name in dir(witness.MutationCallerWitness) if name.startswith("test_")]:
    setattr(RealConfigHarness, _name, None)


class RealConfigCallerTest(RealConfigHarness):
    def test_candidate_is_exactly_the_one_line_opt_in(self) -> None:
        self.assertEqual(CANDIDATE.count(PROOF_LINE), 1)
        before, after = BEFORE.splitlines(), CANDIDATE.splitlines()
        index = after.index(PROOF_LINE.strip())
        self.assertEqual(after[:index] + after[index + 1:], before)
        self.assertEqual(after[index - 1], 'when = "pr"')  # inside the existing mutation check
        names = [line for line in after[:index] if line.startswith("name = ")]
        self.assertEqual(names[-1], 'name = "mutation"')

    def test_origin_main_config_opts_the_actual_pr_caller_in(self) -> None:
        self.build(main_config=CANDIDATE, worktree_config=CANDIDATE)
        self.configure()
        # Which context and ref own the loaded config.
        self.assertEqual(self.cfg.root, self.main.resolve())
        self.assertEqual(git(self.main, "rev-parse", "HEAD"), git(self.main, "rev-parse", "origin/main"))
        self.assertEqual(git(self.main, "show", f"origin/main:{config.CONFIG_NAME}"), CANDIDATE.strip())
        check = self.mutation_check()
        self.assertEqual((check.when, check.proof, check.run),
                         ("pr", config.MUTATION_PROOF, list(witness.REPO_MUTATION_RUN)))

        result = self.invoke([real_runner()])
        argv = self.gate_cmds[0]
        self.assertEqual(argv[:6], witness.GATE_ARGV)
        for flag in ("--mutation-proof", "--blocking-mutation"):
            self.assertIn(flag, argv)
        self.assertEqual((argv[argv.index("--tier") + 1], argv[argv.index("--base") + 1]), ("pr", "origin/main"))
        self.assertEqual(self.loaded_roots, [self.main.resolve()])  # the gate's real load, same file
        self.assertEqual([child.args for child in self.children], [[*witness.REPO_MUTATION_RUN, "--blocking"]])
        env = self.children[0].kwargs["env"]
        self.assertEqual((env[mutation.PROOF_BASE_ENV], mutation.PROOF_ENV in env), (self.base, True))
        self.assertEqual(self.summary(result), {
            "approve": True, "head_accepted": True, "gate_exit": 0, "risk": [("PASS", "PASS")],
        })
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertEqual((row["mutation_proof"], row["mutation_base"]), (config.MUTATION_PROOF, self.base))

    def test_worktree_config_edit_does_not_activate_the_running_dispatcher(self) -> None:
        self.changed = "M\t.factory.toml\nM\tfactory/example.py\n"
        self.build(main_config=BEFORE, worktree_config=CANDIDATE)
        self.configure()
        self.assertEqual((self.root / config.CONFIG_NAME).read_text(), CANDIDATE)  # the PR carries it
        self.assertEqual(self.cfg.root, self.main.resolve())
        self.assertIsNone(self.mutation_check().proof)  # origin/main does not

        result = self.invoke([NO_PROOF_PASS])  # exit 0, no record: the legacy High path
        self.assertNotIn("--mutation-proof", self.gate_cmds[0])
        self.assertIn("--blocking-mutation", self.gate_cmds[0])  # High stays exit-status blocking
        self.assertEqual(self.loaded_roots, [self.main.resolve()])
        self.assertNotIn(mutation.PROOF_ENV, self.children[0].kwargs["env"])
        row = [e for e in lifecycle.read_events(dispatch.EVENTS) if e.get("event") == "risk-check"][-1]
        self.assertNotIn("mutation_proof", row)
        self.assertEqual(result["risk"], [("PASS", "PASS")])


del RealConfigHarness
