"""`factory chat` launcher smoke: isolation, pinning, read-only argv and scope
validation without Node, a live model, or a real terminal.

The console extension/transport are exercised separately by
`console/app/check.py` and `console/app/check-transport.ts`; this suite pins the
supported Python launcher's guarantees a consumer depends on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from factory import chat, cli


def fail(message):
    raise SystemExit(message)


def make_checkout(root: Path, *, version: str = chat.VERSION) -> Path:
    app = root / "console" / "app"
    pkg = app / "node_modules/@earendil-works/pi-coding-agent"
    (pkg / "dist/bundle").mkdir(parents=True, exist_ok=True)
    (pkg / "dist/bundle/cli.js").write_text("// pi cli")
    (pkg / "package.json").write_text(json.dumps({"version": version}))
    (app / "extension.ts").write_text("// extension")
    return app


def fake_bin(root: Path) -> Path:
    binaries = root / "bin"
    binaries.mkdir()
    for name in ("node", "gh"):
        exe = binaries / name
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return binaries


def args(root: Path, **over):
    argv = ["--root", str(root), "--repository", over.pop("repository", "owner/name")]
    if over.pop("resume", False):
        argv.append("--continue")
    for key in ("provider", "model", "console"):
        if key in over:
            argv += [f"--{key}", over.pop(key)]
    assert not over, over
    return chat.parser().parse_args(argv)


class ChatLauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_checkout(self.root)
        self.bin = fake_bin(self.root)
        self.fake_sys = types.SimpleNamespace(
            platform="linux", executable="/usr/bin/python3",
            stdin=types.SimpleNamespace(isatty=lambda: True),
            stdout=types.SimpleNamespace(isatty=lambda: True))
        self.addCleanup(self.tmp.cleanup)

    def prepare(self, parsed, env=None):
        base = {"PATH": str(self.bin), "HOME": "/home/x", "USER": "x"}
        base.update(env or {})
        with patch.object(chat, "sys", self.fake_sys), patch.dict(os.environ, base, clear=True):
            return chat.prepare(parsed, fail)

    def runtime(self, name):
        return json.loads((self.root / "console/app/.runtime/agent" / name).read_text())

    # ---- read-only, isolated launch argv -------------------------------------

    def test_read_only_isolated_argv(self):
        argv, env, work = self.prepare(args(self.root, repository="o/r"))
        for flag in ("--offline", "--no-approve", "--no-context-files", "--no-extensions",
                     "--no-skills", "--no-prompt-templates", "--no-themes"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], chat.TOOLS)
        self.assertEqual(len(chat.TOOLS.split(",")), 7)
        self.assertEqual(argv[argv.index("--thinking") + 1], "off")
        self.assertNotIn("--continue", argv)
        self.assertEqual(argv[0], str(self.bin / "node"))
        self.assertTrue(work.is_dir() and work.name == "work")

    def test_environment_scrubbed(self):
        argv, env, work = self.prepare(args(self.root, repository="o/r"),
                                       env={"OPENAI_API_KEY": "sk-secret", "MY_SECRET": "leak"})
        self.assertNotIn("OPENAI_API_KEY", env, "ornith must not carry provider secrets")
        self.assertNotIn("MY_SECRET", env)
        self.assertEqual(env["PI_OFFLINE"], "1")
        self.assertEqual(env["FM_C0_REPOSITORY"], "o/r")
        self.assertEqual(env["FM_C0_ROOT"], str(self.root.resolve()))
        self.assertEqual(env["FM_C0_PROVIDER"], "c0-ornith")

    def test_settings_disable_mutation_surface(self):
        self.prepare(args(self.root))
        settings = self.runtime("settings.json")
        self.assertEqual(settings["defaultProjectTrust"], "never")
        for empty in ("packages", "extensions", "skills", "prompts", "themes"):
            self.assertEqual(settings[empty], [])
        self.assertTrue(settings["images"]["blockImages"])
        model = self.runtime("models.json")["providers"]["c0-ornith"]["models"][0]
        self.assertEqual(model["id"], chat.MODEL)
        self.assertEqual(model["cost"], {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0})

    def test_resume_reobserves_via_flag(self):
        argv, _, _ = self.prepare(args(self.root, resume=True))
        self.assertIn("--continue", argv)

    def test_openai_carries_only_its_own_auth(self):
        argv, env, _ = self.prepare(args(self.root, provider="openai", model="gpt-5.5"),
                                    env={"OPENAI_API_KEY": "sk-live"})
        self.assertEqual(env["OPENAI_API_KEY"], "sk-live")
        self.assertEqual(env["FM_C0_PROVIDER"], "openai")
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.5")

    # ---- refusals ------------------------------------------------------------

    def test_ornith_model_pinned(self):
        with self.assertRaises(SystemExit):
            self.prepare(args(self.root, model="some/other-model"))

    def test_openai_requires_model(self):
        with self.assertRaises(SystemExit):
            self.prepare(args(self.root, provider="openai"))

    def test_runtime_version_pinned(self):
        make_checkout(self.root, version="9.9.9")  # overwrite pinned package.json
        with self.assertRaises(SystemExit):
            self.prepare(args(self.root))

    def test_missing_console_dependency(self):
        with tempfile.TemporaryDirectory() as bare:
            fresh = Path(bare)
            (fresh / "console/app").mkdir(parents=True)
            (fresh / "console/app/extension.ts").write_text("// ext")  # but no node_modules
            with self.assertRaises(SystemExit):
                self.prepare(args(fresh))

    def test_console_decoupled_from_root(self):
        with tempfile.TemporaryDirectory() as other:
            bare = Path(other)  # a --root with no console/app of its own
            argv, env, _ = self.prepare(
                args(bare, console=str(self.root / "console/app")))
            self.assertEqual(env["FM_C0_ROOT"], str(bare.resolve()))
            self.assertIn(str(self.root / "console/app"), " ".join(argv))

    def test_non_interactive_refused(self):
        self.fake_sys.stdout = types.SimpleNamespace(isatty=lambda: False)
        with self.assertRaises(SystemExit):
            self.prepare(args(self.root))

    # ---- CLI registration ----------------------------------------------------

    def test_registered_in_cli(self):
        self.assertEqual(cli.COMMANDS["chat"][:2], ("chat", "main"))
        self.assertTrue(callable(chat.main))


if __name__ == "__main__":
    unittest.main()
