"""Native-host evidence must reach the PR and isolated reviewer, not just the worktree."""
import json
import unittest

import test_dispatch_lifecycle as fixtures


class NativeProofTest(unittest.TestCase):
    def setUp(self):
        self.local = fixtures.LocalCLI(methodName="runTest")
        self.local.setUp()
        self.addCleanup(self.local.doCleanups)
        gh = self.local.bin / "gh"
        gh.write_text(fixtures.GH.replace(
            'elif a[:2] == ["pr", "checks"]:',
            'elif a[:2] == ["pr", "comment"]:\n'
            '    s.setdefault("pr_comments", []).append(Path(a[a.index("--body-file") + 1]).read_text())\n'
            '    p.write_text(json.dumps(s))\n'
            '    print("https://example.invalid/pull/70#issuecomment-123")\n'
            'elif a[:2] == ["pr", "checks"]:',
        ))
        self.local.reviewer.write_text(fixtures.REVIEWER.replace(
            's["reviews"] =',
            'import sys\n'
            's.setdefault("packets", []).append({"prompt": sys.argv[-1], "comments": s.get("pr_comments", [])[:]})\n'
            's["reviews"] =',
            1,
        ))

    def test_native_proof_file_is_posted_before_review_and_stales_on_new_head(self):
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
proof = Path(".factory/macos-proof.txt")
proof.parent.mkdir(exist_ok=True)
if not proof.exists():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    proof.write_text("Verified source revision: " + head + "\\nArchive SHA-256: " + "a" * 64
                     + "\\nUltron native import and publication: PASS\\n")
Path(".factory/handoff-7.md").write_text("Native-host proof: `.factory/macos-proof.txt`\\n")
''')
        result = self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        self.assertIn("packets", state, result.stdout + result.stderr + repr(state))
        first, second = state["packets"]
        proof = self.local.repo / ".factory/wt-7/.factory/macos-proof.txt"
        text = proof.read_text()
        head = text.splitlines()[0].split()[-1]
        new_head = self.local.git("-C", ".factory/wt-7", "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(head, new_head)
        posted = "\n".join(first["comments"])
        for expected in (head, "a" * 64, "Ultron native import and publication: PASS"):
            self.assertIn(expected, posted)
            self.assertIn(expected, first["prompt"])
        self.assertIn("untrusted", first["prompt"].lower())
        self.assertIn("stale", second["prompt"].lower())
        self.assertIn(head, second["prompt"])
        self.assertIn(new_head, second["prompt"])
        self.assertEqual(self.local.git("-C", ".factory/wt-7", "check-ignore", ".factory/macos-proof.txt").stdout.strip(), ".factory/macos-proof.txt")
