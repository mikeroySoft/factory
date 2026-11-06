"""Native-host evidence must reach the PR and isolated reviewer, not just the worktree."""
import json
import sys
import unittest

from tests import test_dispatch_lifecycle as fixtures


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
        self.local.write_config(reviewer=[sys.executable, str(self.local.reviewer), "{prompt}"])
        self.local.commit_config()

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
        self.assertIn("Current-head proof:", posted)
        self.assertNotIn("Current-head proof:", second["prompt"])
        for expected in (head, "a" * 64, "Ultron native import and publication: PASS"):
            self.assertIn(expected, posted)
            self.assertIn(expected, first["prompt"])
        self.assertIn("untrusted", first["prompt"].lower())
        self.assertIn("stale", second["prompt"].lower())
        self.assertIn(head, second["prompt"])
        self.assertIn(new_head, second["prompt"])
        self.assertEqual(self.local.git("-C", ".factory/wt-7", "check-ignore", ".factory/macos-proof.txt").stdout.strip(), ".factory/macos-proof.txt")

    def test_native_proof_inline_section_is_bounded_and_secret_redacted(self):
        secret = "ghp_" + "x" * 25
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
Path(".factory").mkdir(exist_ok=True)
Path(".factory/handoff-7.md").write_text(
    "## Native-host proof\\nHead SHA: " + head + "\\nNative launchd smoke: PASS\\n"
    + "ghp_" + "x" * 25 + "\\n" + "z" * 6000 + "\\nTree digest SHA-256: " + "b" * 64
    + "\\n## Still unverified\\nNot part of native evidence\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        for packet in state["packets"]:
            comment = packet["comments"][-1]
            self.assertIn("Current-head proof:", comment)
            self.assertIn("Native launchd smoke: PASS", comment)
            self.assertIn("b" * 64, comment)
            self.assertIn("[REDACTED]", comment)
            self.assertIn("truncated", comment)
            self.assertNotIn(secret, comment)
            self.assertNotIn(secret, packet["prompt"])
            self.assertNotIn("Not part of native evidence", comment)
            self.assertLessEqual(len(comment), 20_000)
            self.assertIn(comment.strip(), packet["prompt"])

    def test_native_proof_missing_unsafe_and_unbound_are_explicit(self):
        outside = self.local.base / "private.txt"
        outside.write_text("Private file must not be published")
        self.local.worker.write_text(fixtures.WORKER + f'''
Path(".factory").mkdir(exist_ok=True)
link = Path(".factory/unsafe.txt")
if not link.exists():
    link.symlink_to({str(outside)!r})
Path(".factory/unbound.txt").write_text("Native unbound result: PASS")
Path(".factory/handoff-7.md").write_text(
    "Proof: `.factory/missing.txt`\\nProof: `.factory/unsafe.txt`\\nProof: `.factory/unbound.txt`\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        for packet in state["packets"]:
            comment = packet["comments"][-1]
            self.assertIn("Proof not supplied", comment)
            self.assertIn("missing, unreadable, unsafe or oversized", comment)
            self.assertIn("no usable recorded head SHA", comment)
            self.assertNotIn("Current-head proof:", comment)
            self.assertNotIn(outside.read_text(), comment)
            self.assertNotIn(outside.read_text(), packet["prompt"])

    def test_native_proof_same_head_retry_posts_once(self):
        self.local.worker.write_text('''
import subprocess
from pathlib import Path
if not Path("change.txt").exists():
    Path("change.txt").write_text("change\\n")
    subprocess.run(["git", "add", "change.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "worker change"], check=True)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
Path(".factory").mkdir(exist_ok=True)
Path(".factory/handoff-7.md").write_text(
    "## Native-host proof\\nHead SHA: " + head + "\\nUltron same-head check: PASS\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        proof_comments = [body for body in state["pr_comments"] if "## Native-host proof" in body]
        self.assertEqual(len(proof_comments), 1)
        for packet in state["packets"]:
            self.assertIn("Ultron same-head check: PASS", packet["prompt"])
            self.assertIn(proof_comments[0], packet["comments"])

    def test_native_proof_multiline_citation_reaches_review_and_stales(self):
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
Path(".factory").mkdir(exist_ok=True)
proof = Path(".factory/macos-run.txt")
if not proof.exists():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    proof.write_text("Verified source revision: " + head + "\\nNative multiline smoke: PASS\\n")
Path(".factory/unrelated.txt").write_text("Unrelated evidence must not be published")
Path(".factory/handoff-7.md").write_text(
    "Native-host proof:\\n- `.factory/macos-run.txt`\\n- `.factory/missing.txt`\\n"
    "\\n## Next steps\\n- `.factory/unrelated.txt`\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        first, second = state["packets"]
        text = (self.local.repo / ".factory/wt-7/.factory/macos-run.txt").read_text()
        head = text.splitlines()[0].split()[-1]
        for packet in (first, second):
            self.assertTrue(packet["comments"], "Named multiline proof was not posted before review")
            comment = packet["comments"][-1]
            self.assertIn("Native multiline smoke: PASS", comment)
            self.assertIn(head, comment)
            self.assertIn("missing, unreadable, unsafe or oversized", comment)
            self.assertIn(comment, packet["prompt"])
            self.assertNotIn("Unrelated evidence must not be published", comment)
        self.assertIn("Current-head proof:", first["prompt"])
        self.assertNotIn("Current-head proof:", second["prompt"])
        self.assertIn("STALE / conflicting proof", second["prompt"])
        new_head = self.local.git("-C", ".factory/wt-7", "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(head, new_head)
        self.assertIn(new_head, second["prompt"])

    def test_native_proof_nul_filename_is_reported_unavailable(self):
        self.local.worker.write_text(fixtures.WORKER + '''
Path(".factory").mkdir(exist_ok=True)
Path(".factory/native.txt").write_text("NUL-stripped path must not be read")
Path(".factory/handoff-7.md").write_text("Native-host proof: `.factory/native\\x00.txt`\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        first, second = state["packets"]
        for packet in (first, second):
            comment = packet["comments"][-1]
            self.assertIn("Proof not supplied", comment)
            self.assertIn("missing, unreadable, unsafe or oversized", comment)
            self.assertIn(comment, packet["prompt"])
            self.assertNotIn("Current-head proof:", comment)
            self.assertNotIn("NUL-stripped path must not be read", comment)
            self.assertNotIn("\x00", packet["prompt"])

    def test_native_proof_quoted_paths_with_spaces_reach_review(self):
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
Path(".factory").mkdir(exist_ok=True)
citations = []
for label, quote in (("backtick", "`"), ("double", chr(34)), ("single", chr(39))):
    proof = Path(".factory/" + label + " native proof.txt")
    if not proof.exists():
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        proof.write_text("Verified source revision: " + head + "\\nNative " + label + " smoke: PASS\\n")
    citations.append("Proof: " + quote + str(proof) + quote)
Path(".factory/handoff-7.md").write_text("\\n".join(citations))
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        first, second = state["packets"]
        for packet in (first, second):
            self.assertTrue(packet["comments"], "Quoted proof was not posted before review")
            comment = packet["comments"][-1]
            for label in ("backtick", "double", "single"):
                with self.subTest(label=label):
                    self.assertIn("Native " + label + " smoke: PASS", comment)
            self.assertIn(comment, packet["prompt"])
        self.assertIn("Current-head proof:", first["prompt"])
        self.assertNotIn("Current-head proof:", second["prompt"])
        self.assertIn("STALE / conflicting proof", second["prompt"])

    def test_native_proof_prose_apostrophes_preserve_citation_and_stale_binding(self):
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
Path(".factory").mkdir(exist_ok=True)
proof = Path(".factory/macos-proof-532-current.txt")
if not proof.exists():
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    proof.write_text("Verified source revision: " + head + "\\nArchive SHA-256: " + "c" * 64
                     + "\\nUltron possessive citation smoke: PASS\\n")
Path(".factory/handoff-7.md").write_text(
    "Ultron's native proof: `.factory/macos-proof-532-current.txt` (doesn't cover CI)\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        first, second = state["packets"]
        path = ".factory/macos-proof-532-current.txt"
        text = (self.local.repo / ".factory/wt-7" / path).read_text()
        head = text.splitlines()[0].split()[-1]
        new_head = self.local.git("-C", ".factory/wt-7", "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(head, new_head)
        self.assertEqual(
            self.local.git("-C", ".factory/wt-7", "check-ignore", path).stdout.strip(), path,
        )
        for packet in (first, second):
            self.assertTrue(packet["comments"], "Proof must be published before reviewer invocation")
            comment = packet["comments"][-1]
            for expected in (head, "c" * 64, "Ultron possessive citation smoke: PASS"):
                self.assertIn(expected, comment)
                self.assertIn(expected, packet["prompt"])
            self.assertIn(comment, packet["prompt"])
            self.assertIn("UNTRUSTED NATIVE-HOST EVIDENCE", packet["prompt"])
        self.assertIn(f"Current-head proof: `{head}`.", first["comments"][-1])
        self.assertIn(f"Current-head proof: `{head}`.", first["prompt"])
        for evidence in (second["comments"][-1], second["prompt"]):
            self.assertIn(f"STALE / conflicting proof for `{head}`; gated head is `{new_head}`.", evidence)
            self.assertNotIn("Current-head proof:", evidence)

    def test_native_proof_handoff_link_is_published_for_each_reviewed_head(self):
        self.local.worker.write_text(fixtures.WORKER + '''
import subprocess
subprocess.run(["git", "add", "change.txt"], check=True)
subprocess.run(["git", "commit", "-m", "worker change"], check=True)
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
proof = Path(".factory/native/ultron-proof.txt")
proof.parent.mkdir(parents=True, exist_ok=True)
proof.write_text("Verified source revision: " + head + "\\nArchive SHA-256: " + "d" * 64
                 + "\\nUltron current revision smoke: PASS\\n")
Path(".factory/handoff-7.md").write_text(
    "## Verification\\nProof file: [native run](.factory/native/ultron-proof.txt)\\n")
''')
        self.local.cli("dispatch", "--ticket", "7")
        state = json.loads(self.local.state.read_text())
        first, second = state["packets"]
        path = ".factory/native/ultron-proof.txt"
        self.assertEqual(
            self.local.git("-C", ".factory/wt-7", "check-ignore", path).stdout.strip(), path,
        )
        self.assertEqual(self.local.git("-C", ".factory/wt-7", "ls-files", path).stdout, "")
        heads = []
        for packet in (first, second):
            self.assertTrue(packet["comments"], "Cited proof must be posted before review")
            comment = packet["comments"][-1]
            head = comment.split("Current-head proof: `", 1)[1].split("`", 1)[0]
            heads.append(head)
            for expected in (head, "d" * 64, "Ultron current revision smoke: PASS"):
                self.assertIn(expected, comment)
                self.assertIn(expected, packet["prompt"])
            self.assertIn(comment, packet["prompt"])
            self.assertIn("UNTRUSTED NATIVE-HOST EVIDENCE", packet["prompt"])
        self.assertNotEqual(*heads)
        self.assertEqual(
            heads[1], self.local.git("-C", ".factory/wt-7", "rev-parse", "HEAD").stdout.strip(),
        )
