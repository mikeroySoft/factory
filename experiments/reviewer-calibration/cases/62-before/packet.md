## Frozen review packet — case 62-before

This packet supplies, verbatim, the evidence the reviewer would otherwise read from the
repository and GitHub. The reviewer process for this experiment has no tools, so the diff,
the issue text and the repository README are inlined below. Cite `path:line` from the diff.

- repository: mikeroySoft/factory
- issue: #62
- review base (origin/main): f6dfc77bb61437773d87cf06df8df7d78844f837
- head under review: cfbdb6f9be738076d32b6085f9d38e9cb8ed9383
- documented conventions present at this head: README.md (inlined below). No AGENTS.md or
  CONTRIBUTING.md exists at this commit. docs/manager-plan.md (if present) is NOT inlined and is
  outside this packet; do not assume its contents.

### Issue

# Issue #62: manager: `{prompt}` is inline text while workers get a path; a worker-shaped command fails with ENAMETOOLONG and posts a stack trace

**Scope**

On 2026-09-07T22:11Z the manager ran for District #26's escalation and failed with:

```
Factory manager: Manager command failed (1):
... ENAMETOOLONG: name too long, open '/home/mike/dev/mikeroysoft/district/.factory/wt-26/You are the factory manager. Diagnose only; ...
```

The host config had `[defaults.manager].command = ["omp", "-p", "--no-session", "--model", "openai-codex/gpt-6-astra", "--cwd", "{cwd}", "@{prompt}"]` — the *worker* shape, where `@{prompt}` means "read the prompt file". The manager contract is different: `Config.manager_cmd` (`factory/config.py:143`) expands `{prompt}` with the inline prompt **text** (README:92), like the reviewer, while `Config.worker` (`config.py:135`) expands it with a **path**. `omp` therefore received `@<2 KB of prompt text>` as a filename. The manager decision became `HUMAN` with a minified JS stack trace as the diagnosis, so the escalated ticket got no automated diagnosis and the operator got no useful message.

Three things are wrong, in order of leverage:

1. **Nothing validates the manager command's shape.** `config.py` accepts any argv; `factory doctor` has no row for it. A `{prompt}` placeholder prefixed with `@` (or used as an `-f`/`--file` argument) can never work for a text-expanded command, and the failure only appears at the first escalation — the moment the manager is needed.
2. **The failure is reported as a stack trace.** `factory/manage.py:245` posts `proc.stderr or proc.stdout` verbatim as the HUMAN body. A nonzero manager exit should post a one-line cause (`manager command exited 1; stderr tail: …`) with the argv shape (placeholders unexpanded), not 20 lines of minified JavaScript.
3. **The inline-text transport is fragile by design.** A prompt containing the issue body, the escalation packet, lessons and notes is passed as one argv element; it already grows past 2 KB and will hit `ARG_MAX`/per-argument limits (`MAX_ARG_STRLEN` = 128 KiB on Linux) as packets grow. The worker path writes `.factory-prompt.md` and passes a path; the manager should do the same.

After this change:

- `Config.manager_cmd(prompt_path, cwd)` writes the prompt to `<factory>/manager-prompt-<n>.md` and expands `{prompt}` with that path, matching the worker contract. `README.md` §"Configuration" and the `factory init` template document `{prompt}` = file for workers **and** manager, text for the reviewer only. The default manager command becomes the worker shape (`@{prompt}` for omp).
- Existing host configs written for inline text (a bare `{prompt}` argument to `omp`) keep working: `omp -p <path>` is not a valid prompt, so `manager_cmd` rejects a bare `{prompt}` element for commands whose argv[0] is `omp` with a `ConfigError` naming the fix (`use "@{prompt}"`). Other programs are not second-guessed.
- `factory doctor` gains a `manager command` row: PASS when `{prompt}` and `{cwd}` are present and argv[0] resolves on PATH; FAIL naming the missing placeholder or executable; WARN when `[dispatch.manager]` is unset (escalations get no automated diagnosis).
- A nonzero manager exit posts `Manager command exited N (argv: …unexpanded…)` plus the last 5 stderr lines, and records an `escalate`-round event with `reason: manager_failed` so `factory stats` can count it.

**Touches**

`factory/config.py` (`manager_cmd`, `DEFAULT_MANAGER`, validation), `factory/manage.py` (prompt file, failure body), `factory/doctor.py` (new row), `factory/init` template + `README.md`, `tests/test_factory.py`.

**Exit gate**

- A test builds a `Config` with the District-shaped command (`"@{prompt}"`), calls `manager_cmd`, and asserts argv contains `@<path>` where `<path>` is a readable file whose content is the prompt text; a test with a bare `"{prompt}"` for `omp` raises `ConfigError` mentioning `@{prompt}`.
- A test runs the manager with a stub that exits 1 and asserts the HUMAN body starts with `Manager command exited 1` and contains no more than 5 stderr lines.
- `factory doctor` on a repo with no manager prints the WARN row; with a placeholder-less command prints FAIL.
- `uv run python -m unittest discover -s tests` passes.
- Manual: re-run `factory manage` on District #26's kept packet (`.factory/escalations/26.md`) with the host's current command and get a real DECISION line, not ENAMETOOLONG.

**Out of scope**

Changing manager decision semantics, the escalation packet format, or which model the manager uses.

## Comment by @mikeroySoft (2026-09-08T23:49:59Z)

Triage: The issue is fully specified: it has a concrete problem statement (ENAMETOOLONG failure from inline `{prompt}` text for the manager), a detailed 'After this change' acceptance section, and an 'Exit gate' with observable done-conditions and a verification command. It is scoped to code/config/doc changes within this repository and requires no design judgment, release, signing, or security policy decisions, so it is ready for an AFK agent to implement.

Agent brief: Make the manager use the file-path prompt transport like workers instead of inline text. (1) In factory/config.py, `Config.manager_cmd(prompt_path, cwd)` writes the prompt to `<factory>/manager-prompt-<n>.md` and expands `{prompt}` with that path; default `DEFAULT_MANAGER` becomes the worker shape (`@{prompt}` for omp). Reject a bare `{prompt}` element for commands whose argv[0] is `omp` with a ConfigError naming the fix (`use "@{prompt}"`); do not second-guess other programs. (2) In factory/manage.py, a nonzero manager exit posts a one-line cause `Manager command exited N (argv: <unexpanded argv>)` plus the last 5 stderr lines (not the verbatim stack trace), and records an escalate-round event with `reason: manager_failed`. (3) In factory/doctor.py, add a `manager command` row: PASS when `{prompt}` and `{cwd}` present and argv[0] resolves on PATH, FAIL naming the missing placeholder/executable, WARN when `[dispatch.manager]` unset. (4) Update README.md §Configuration and the `factory init` template to document `{prompt}` = file for workers and manager, text for reviewer only. Verification: `uv run python -m unittest discover -s tests` passes; tests must assert `@<path>` with readable prompt content, ConfigError on bare `{prompt}` for omp, HUMAN body starting with `Manager command exited 1` capped at 5 stderr lines, and doctor WARN/FAIL rows.

## Comment by @mikeroySoft (2026-09-09T00:26:38Z)

Factory dispatcher escalating: REVISE verdict after 1 review round(s).

Escalation packet: `/home/mike/dev/mikeroysoft/factory/.factory/escalations/62.md`

Worker logs: `/home/mike/dev/mikeroysoft/factory/.factory/logs/62-attempt-4.log`

Worker handoff notes:

# Issue 62 handoff — review round 1

## Changed

Addressed only the two required reviewer fixes:

- `factory/manage.py` catches `config.ConfigError` alongside `OSError` at the manager execution boundary. An old bare `{prompt}` omp configuration now yields a HUMAN diagnosis naming `@{prompt}` and a recorded manage decision, rather than aborting the dispatch pass. The same round is not replayed.
- `factory/dispatch.py` excludes `reason: manager_failed` escalation events from escalation-round arithmetic. These events remain available for stats; the next actual escalation still receives round 2 after a round-1 manager failure.
- Added a CLI regression for invalid omp syntax and extended the nonzero-exit regression through the next escalation and a successful RETRY decision with `manager_rounds=2`.
- Existing prompt transport, doctor behavior, README and template documentation remain unchanged. Optional reviewer suggestions were not implemented.

## Commits

- `134cf38` — fix(manager): contain command configuration errors per ticket
- `8442220` — fix(manager): preserve escalation rounds after command failures

Both commits have Signed-off-by trailers. No push, merge, or ticket mutation performed.

## Verified this attempt

- Configuration-error regression failed before the fix with exit code 1, then passed with a HUMAN diagnosis and no repeated comment on the next pass.
- Round-budget regression failed before the fix with round 3 instead of 2, then passed and applied RETRY on the next real escalation.
- `uv run python -m unittest discover -s tests`: 149 tests passed.
- `/home/mike/.local/share/uv/tools/factory/bin/python3 -m factory gate --report .factory/gate-report-62.md`: gate PASS.
- Both commands used `UV_EXCLUDE_NEWER=2026-09-06T22:10:50Z`, matching the existing lockfile.

## Still unverified

- No live District actions or end-to-end live dispatch were performed in this revision. Tests isolate GitHub effects.
- The previous attempt's handoff reports a successful District #26 kept-packet replay yielding `DECISION: FIX`, with decision application intercepted and output in `.factory/district-26-replay.log`. This revision did not repeat that model invocation.

## Next

Dispatcher can publish the updated `agent/62` commits for re-review and CI. No further required reviewer fixes remain.

### git diff origin/main..HEAD

```diff
diff --git a/README.md b/README.md
index c3ecba4..9418f4d 100644
--- a/README.md
+++ b/README.md
@@ -34,7 +34,8 @@ npx skills add mikeroysoft/factory
 
 - a **worker** agent CLI that accepts a prompt file and works in a directory
   (default `omp -p`; `droid`, `codex exec`, `claude -p`, … work the same way)
-- a **reviewer** CLI that answers a prompt on stdout (default `omp -p --model anthropic/claude-fable-5-1`; `codex exec` works the same way)
+- optionally, a **manager** agent CLI that accepts a prompt file and works in a directory
+- a **reviewer** CLI that answers an inline prompt on stdout (default `omp -p --model anthropic/claude-fable-5-1`; `codex exec` works the same way)
 - optionally, an OpenAI-compatible local model for triage (Ollama, vLLM,
   llama.cpp, LM Studio)
 
@@ -93,11 +94,15 @@ merge stage.
 Every command reads `.factory.toml` from the main checkout, even when run
 inside one of its worktrees.
 
-The manager command receives `{prompt}` as inline text and `{cwd}` as the kept
-worktree (or repository root). Configure the agent CLI in read-only/no-tools mode:
-the prompt prohibits file edits, but an arbitrary configured executable is trusted,
-not sandboxed by factory. It reads the packet, `.factory-lessons.md`, and optional
-`.factory/manager/notes.md`. Its last `DECISION:` header
+Worker and manager commands receive `{prompt}` as a prompt-file path and `{cwd}`
+as the worktree (or repository root); reviewers receive `{prompt}` as inline
+text. OMP loads a prompt file only when it is prefixed with `@`, so use
+`@{prompt}`: bare `omp {prompt}` passes the path as prompt text and is rejected.
+Keep `{cwd}` in worker and manager commands, and configure the manager CLI in
+read-only/no-tools mode: the prompt prohibits file edits, but an arbitrary
+configured executable is trusted, not sandboxed by factory. The manager reads
+the packet, `.factory-lessons.md`, and optional `.factory/manager/notes.md`.
+Its last `DECISION:` header
 selects `RETRY`, `REWRITE`, `SPLIT`, `ROUTE`, `FIX`, or `HUMAN`, followed by the decision
 body. RETRY/HUMAN use plain text; REWRITE uses the complete replacement issue body.
 SPLIT uses a JSON array of `{title, body, blocked_by}` children, with `blocked_by`
@@ -207,6 +212,9 @@ chore   = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd
 [review]
 command = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]   # {prompt} = review prompt text
 
+[manager]                         # optional; unset command disables it
+command = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]   # {prompt} = manager prompt file
+
 [gate]
 timeout = 1200
 lock = "/tmp/factory.lock"
diff --git a/factory/config.py b/factory/config.py
index 6205ac9..807ffd9 100644
--- a/factory/config.py
+++ b/factory/config.py
@@ -143,8 +143,12 @@ class Config:
     def review_cmd(self, prompt: str) -> list[str]:
         return expand(self.reviewer, prompt=prompt)
 
-    def manager_cmd(self, prompt: str, cwd: Path) -> list[str]:
-        return expand(self.manager or [], prompt=prompt, cwd=str(cwd))
+    def manager_cmd(self, prompt_path: Path, cwd: Path) -> list[str]:
+        """Expand the manager's prompt file, matching the worker transport."""
+        argv = self.manager or []
+        if argv and Path(argv[0]).name == "omp" and "{prompt}" in argv:
+            raise ConfigError('manager.command: use "@{prompt}" instead of bare "{prompt}" for omp')
+        return expand(argv, prompt=str(prompt_path), cwd=str(cwd))
 
 
 def expand(argv: list[str], **values: str) -> list[str]:
diff --git a/factory/dispatch.py b/factory/dispatch.py
index 72209f8..1b09b5a 100644
--- a/factory/dispatch.py
+++ b/factory/dispatch.py
@@ -373,7 +373,8 @@ def escalation_packet(
         + f"\n\n## Worktree path\n\n`{wt}`\n"
     )
     round_number = 1 + sum(
-        event.get("event") == "escalate" for event in ticket_events
+        event.get("event") == "escalate" and event.get("reason") != "manager_failed"
+        for event in ticket_events
     )
     return packet, round_number
 
diff --git a/factory/learn.py b/factory/learn.py
index a23376c..28d42fc 100644
--- a/factory/learn.py
+++ b/factory/learn.py
@@ -13,6 +13,7 @@ import argparse
 import json
 from datetime import date
 from pathlib import Path
+from tempfile import NamedTemporaryFile
 from typing import Callable
 
 from factory import config, dispatch, lifecycle, manage, triage
@@ -60,8 +61,11 @@ def evidence(last: int) -> tuple[list[int], str]:
 def manager_llm(cfg: config.Config) -> Callable[[list[dict]], str]:
     """Chat-shaped adapter over `manager.command`: the transcript is flattened into one prompt."""
     def ask(messages: list[dict]) -> str:
-        prompt = "\n\n".join(m["content"] for m in messages)
-        return dispatch.run(cfg.manager_cmd(prompt, cfg.root), cwd=cfg.root).stdout
+        cfg.factory.mkdir(parents=True, exist_ok=True)
+        with NamedTemporaryFile(mode="w", dir=cfg.factory, prefix="manager-prompt-", suffix=".md") as prompt:
+            prompt.write("\n\n".join(m["content"] for m in messages))
+            prompt.flush()
+            return dispatch.run(cfg.manager_cmd(Path(prompt.name), cfg.root), cwd=cfg.root).stdout
     return ask
 
 
diff --git a/factory/manage.py b/factory/manage.py
index 1a2cba6..d03b4d8 100644
--- a/factory/manage.py
+++ b/factory/manage.py
@@ -260,14 +260,22 @@ def manage_pass(dry_run: bool = False) -> None:
                     cwd = wt if wt.is_dir() else cfg.root
                     notes = rejected = None
                     try:
-                        proc = dispatch.run(cfg.manager_cmd("\n\n".join(parts), cwd), cwd=cwd, check=False)
+                        prompt_path = cfg.factory / f"manager-prompt-{n}.md"
+                        prompt_path.write_text("\n\n".join(parts))
+                        proc = dispatch.run(cfg.manager_cmd(prompt_path, cwd), cwd=cwd, check=False)
                         if proc.returncode:
-                            decision, body, data = "HUMAN", f"Manager command failed ({proc.returncode}):\n{proc.stderr or proc.stdout}", None
+                            body = f"Manager command exited {proc.returncode} (argv: {json.dumps(cfg.manager)})"
+                            tail = "\n".join(proc.stderr.splitlines()[-5:])
+                            if tail:
+                                body += "\n" + tail
+                            decision, data = "HUMAN", None
+                            dispatch.record("escalate", ticket=n, round=round_number,
+                                            packet=str(packet), reason="manager_failed")
                         else:
                             output, notes = split_notes(proc.stdout)
                             rejected = "CURATE" if split_curate(output)[1] is not None else None
                             decision, body, data = ("HUMAN", CURATE_REJECTED, None) if rejected else parse(output, workers)
-                    except OSError as exc:
+                    except (OSError, config.ConfigError) as exc:
                         decision, body, data = "HUMAN", f"Manager command failed: {exc}", None
                     # A human may have taken over while the model was thinking.
                     if human_activity(n, escalation):
diff --git a/factory/onboard.py b/factory/onboard.py
index a3c7313..062df97 100644
--- a/factory/onboard.py
+++ b/factory/onboard.py
@@ -223,8 +223,19 @@ def doctor(argv: list[str]) -> int:
     for label, argv_t in cfg.workers.items():
         report(shutil.which(argv_t[0]) is not None, f"worker `{label}`: {argv_t[0]}")
     report(shutil.which(cfg.reviewer[0]) is not None, f"reviewer: {cfg.reviewer[0]}")
-    if cfg.manager:
-        report(shutil.which(cfg.manager[0]) is not None, f"manager: {cfg.manager[0]}")
+    if not cfg.manager:
+        report(None, "manager command", "[manager].command is unset; escalations get no automated diagnosis")
+    else:
+        problems = [
+            f"missing `{{{name}}}` placeholder"
+            for name in ("prompt", "cwd")
+            if not any(f"{{{name}}}" in arg for arg in cfg.manager)
+        ]
+        if Path(cfg.manager[0]).name == "omp" and "{prompt}" in cfg.manager:
+            problems.append('omp needs a prompt file; use "@{prompt}"')
+        if shutil.which(cfg.manager[0]) is None:
+            problems.append(f"executable not on PATH: {cfg.manager[0]}")
+        report(not problems, "manager command", "; ".join(problems) if problems else cfg.manager[0])
 
     if cfg.checks:
         for check in cfg.checks:
diff --git a/factory/templates/factory.toml b/factory/templates/factory.toml
index b53c194..fd99cdf 100644
--- a/factory/templates/factory.toml
+++ b/factory/templates/factory.toml
@@ -22,6 +22,7 @@
 # cost_pattern = 'Total cost:\s*\$([0-9.]+)'      # Claude Code
 [workers]
 # argv templates. {prompt} = path to the ticket prompt file, {cwd} = worktree.
+# OMP file prompts require `@{prompt}`; bare `{prompt}` passes path text and is rejected.
 # The key is a ticket label; `default` is required. First matching label wins.
 # default = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
 # chore   = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]
@@ -40,14 +41,14 @@
 #
 # [workers.ci-fix]
 # when = "CI is red: read the failing job log, fix the cause or declare a flake with evidence."
-# command = ["omp", "-p", "--cwd", "{cwd}", "Read the ticket prompt at {prompt}. Read the failing CI job log. Fix the cause, or declare a flake in the handoff with concrete log and run evidence. Do not claim a flake without evidence."]
+# command = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
 #
 # [workers.conflict]
 # when = "A rebase conflicts: resolve the rebase, keep both intents, make no semantic changes."
-# command = ["omp", "-p", "--cwd", "{cwd}", "Read the ticket prompt at {prompt}. Resolve the rebase conflicts, preserving both sides' intents with no semantic changes. If the rebase was aborted, restart it against the configured main before resolving. If the intents cannot be preserved without semantic changes, stop and explain in the handoff."]
+# command = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
 
 [review]
-# {prompt} = review prompt text. Output must end with `VERDICT: APPROVE` or `VERDICT: REVISE`.
+# {prompt} = inline review prompt text. Output must end with `VERDICT: APPROVE` or `VERDICT: REVISE`.
 # Keep the reviewer on a different model family than [manager] so the two verdicts are independent.
 # command = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]
 # command = ["codex", "exec", "{prompt}"]
@@ -59,8 +60,9 @@
 #
 # Host-side frontier manager configuration; running it is a separate feature.
 # Unset command disables the frontier manager, not read-only dashboard briefings.
-# {prompt} = manager prompt text; {cwd} = kept worktree or repository root.
-# command = ["omp", "-p", "--model", "openai-codex/gpt-6-astra", "--cwd", "{cwd}", "{prompt}"]
+# {prompt} = path to the manager prompt file; {cwd} = kept worktree or repository root.
+# OMP requires `@{prompt}`; bare `{prompt}` passes path text and is rejected.
+# command = ["omp", "-p", "--model", "openai-codex/gpt-6-astra", "--cwd", "{cwd}", "@{prompt}"]
 # rounds = 1
 # review = "escalated"  # "all" also reviews non-escalated factory PRs
 #
diff --git a/tests/test_factory.py b/tests/test_factory.py
index b067776..ddfcb24 100644
--- a/tests/test_factory.py
+++ b/tests/test_factory.py
@@ -147,6 +147,23 @@ def gate(cwd: Path, *args: str) -> tuple[int, str, str]:
 
 
 class ConfigTest(unittest.TestCase):
+    def test_manager_prompt_file_and_omp_inline_rejection(self) -> None:
+        with tempfile.TemporaryDirectory() as d:
+            root = Path(d)
+            prompt = root / "manager-prompt-7.md"
+            text = "Issue body\n" + "packet " * 30_000
+            prompt.write_text(text)
+            cfg = config.Config(root, "acme/widgets", manager=[
+                "omp", "-p", "--no-session", "--model", "openai-codex/gpt-6-astra",
+                "--cwd", "{cwd}", "@{prompt}",
+            ])
+            argv = cfg.manager_cmd(prompt, root)
+            self.assertEqual(argv[-1], "@" + str(prompt))
+            self.assertEqual(Path(argv[-1][1:]).read_text(), text)
+            cfg.manager[-1] = "{prompt}"
+            with self.assertRaisesRegex(config.ConfigError, r'@\{prompt\}'):
+                cfg.manager_cmd(prompt, root)
+
     def test_defaults_from_origin(self) -> None:
         with tempfile.TemporaryDirectory() as d:
             repo = make_repo(Path(d))
@@ -390,15 +407,33 @@ class HostConfigTest(unittest.TestCase):
                     with self.assertRaises(config.ConfigError):
                         config.load(repo)
 
-    def test_doctor_reports_manager_only_when_configured(self) -> None:
-        gh = 'case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac\nexit 0'
-        with tempfile.TemporaryDirectory() as d:
-            repo = make_repo(Path(d))
-            stubs = stub_bin(Path(d), gh=gh, systemctl="echo inactive", manage="exit 0")
-            rows = lambda: {r["label"]: r for r in json.loads(factory(repo, "doctor", "--json", path=stubs).stdout)["rows"]}  # noqa: E731
-            self.assertNotIn("manager: manage", rows())
-            (repo / ".factory.toml").write_text('[manager]\ncommand = ["manage", "{prompt}"]\n')
-            self.assertEqual(rows()["manager: manage"]["status"], "PASS")
+    def test_doctor_manager_command(self) -> None:
+        cases = [
+            (None, "WARN", ["unset", "no automated diagnosis"]),
+            (["manage"], "FAIL", ["{prompt}", "{cwd}"]),
+            (["manage", "{prompt}"], "FAIL", ["{cwd}"]),
+            (["manage", "{cwd}"], "FAIL", ["{prompt}"]),
+            (["missing-manager-executable", "{prompt}", "{cwd}"], "FAIL", ["missing-manager-executable"]),
+            (["omp", "{prompt}", "--cwd", "{cwd}"], "FAIL", ['use "@{prompt}"']),
+            (["omp", "@{prompt}", "--cwd", "{cwd}"], "PASS", []),
+            (["manage", "{prompt}", "{cwd}"], "PASS", []),
+        ]
+        host_file("")
+        for command, status, details in cases:
+            with self.subTest(command=command), tempfile.TemporaryDirectory() as d:
+                settings = '[triage]\nurl = "http://127.0.0.1:1/v1/chat/completions"\n'
+                if command is not None:
+                    settings += "[manager]\ncommand = " + json.dumps(command) + "\n"
+                repo = make_repo(Path(d), settings)
+                stubs = stub_bin(Path(d), gh='case "$1 $2" in "repo view") echo ADMIN;; "label list") echo "[]";; esac',
+                                 systemctl="echo inactive", manage="exit 0", omp="exit 0")
+                result = factory(repo, "doctor", "--json", path=stubs)
+                rows = {row["label"]: row for row in json.loads(result.stdout)["rows"]}
+                self.assertEqual(rows["manager command"]["status"], status)
+                for detail in details:
+                    self.assertIn(detail, rows["manager command"]["detail"])
+                text = factory(repo, "doctor", path=stubs)
+                self.assertIn(f"{status}  manager command", text.stdout)
 
 
 class StatsTest(unittest.TestCase):
@@ -692,6 +727,75 @@ esac
 ''')
         return repo, stubs, packet
 
+    def test_manager_reads_prompt_file_with_district_command(self) -> None:
+        repo, stubs, packet = self.scenario()
+        text = "Escalation evidence\n" + "packet " * 30_000
+        packet.write_text(text)
+        stub_bin(Path(stubs).parent, omp='''
+python3 - "$5" <<'PY'
+import pathlib, sys
+assert sys.argv[1].startswith("@"), sys.argv
+prompt = pathlib.Path(sys.argv[1][1:]).read_text()
+assert "Original body" in prompt
+assert "packet " * 30_000 in prompt
+print("DECISION: HUMAN\\nRead the complete prompt")
+PY
+''')
+        (repo / config.CONFIG_NAME).write_text(
+            '[manager]\ncommand = ["omp", "-p", "--cwd", "{cwd}", "--no-session", "@{prompt}"]\n')
+        result = factory(repo, "manage", path=stubs)
+        self.assertEqual(result.returncode, 0, result.stderr)
+        self.assertIn("Factory manager: Read the complete prompt", (Path(stubs) / "gh.log").read_text())
+        self.assertIn(text, (repo / ".factory/manager-prompt-7.md").read_text())
+
+    def test_manager_failure_bounds_stderr_and_records_reason(self) -> None:
+        from unittest.mock import patch
+        from factory import dispatch
+
+        repo, _, packet = self.scenario()
+        command = [sys.executable, "-c",
+                   "import sys; sys.stderr.write(''.join(f'error-{i}\\n' for i in range(20))); sys.exit(1)",
+                   "{prompt}", "{cwd}"]
+        cfg = config.Config(repo, "acme/widgets", manager=command, manager_rounds=2)
+        dispatch.configure(cfg)
+        with patch.object(dispatch, "gh_json", return_value=[
+            {"number": 7, "title": "Fix gate", "body": "Original body"}
+        ]), patch.object(manage, "human_activity", return_value=False), patch.object(manage, "apply") as apply:
+            manage.manage_pass()
+        _, _, decision, body, _, _ = apply.call_args.args
+        self.assertEqual(decision, "HUMAN")
+        self.assertTrue(body.startswith("Manager command exited 1 (argv: "), body)
+        self.assertIn("{prompt}", body.splitlines()[0])
+        self.assertIn("{cwd}", body.splitlines()[0])
+        self.assertEqual(body.splitlines()[1:], [f"error-{i}" for i in range(15, 20)])
+        events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
+        failed = [e for e in events if e.get("event") == "escalate" and e.get("reason") == "manager_failed"]
+        self.assertEqual([(e["event"], e["ticket"], e["round"], e["packet"]) for e in failed],
+                         [("escalate", 7, 1, str(packet))])
+        packet, round_number = dispatch.escalation_packet(7, "gate_failed", None, repo / ".factory/wt-7")
+        self.assertEqual(round_number, 2)
+        dispatch.record("escalate", ticket=7, round=round_number, packet=str(packet), reason="gate_failed")
+        command[:] = ["printf", "DECISION: RETRY\nTry again"]
+        with patch.object(dispatch, "gh_json", return_value=[
+            {"number": 7, "title": "Fix gate", "body": "Original body"}
+        ]), patch.object(manage, "human_activity", return_value=False), patch.object(manage, "apply") as apply:
+            manage.manage_pass()
+        self.assertEqual(apply.call_args.args[2:4], ("RETRY", "Try again"))
+
+    def test_manager_config_error_leaves_diagnosis_without_replaying(self) -> None:
+        repo, stubs, _ = self.scenario()
+        (repo / config.CONFIG_NAME).write_text(
+            '[manager]\ncommand = ["omp", "-p", "{prompt}", "--cwd", "{cwd}"]\n')
+        result = factory(repo, "manage", path=stubs)
+        self.assertEqual(result.returncode, 0, result.stderr)
+        calls = (Path(stubs) / "gh.log").read_text()
+        self.assertIn('use "@{prompt}"', calls)
+        events = list(map(json.loads, (repo / ".factory/events.jsonl").read_text().splitlines()))
+        self.assertEqual([(e["decision"], e["round"]) for e in events if e.get("event") == "manage"],
+                         [("HUMAN", 1)])
+        self.assertEqual(factory(repo, "manage", path=stubs).returncode, 0)
+        self.assertNotIn("issue comment", (Path(stubs) / "gh.log").read_text()[len(calls):])
+
     def test_manage_retry_records_before_comment_and_relabels(self) -> None:
         repo, stubs, packet = self.scenario()
         result = factory(repo, "manage", path=stubs)
@@ -754,7 +858,7 @@ esac
                 prompt = repo / ".factory/manager-prompt.txt"
                 output = 'DECISION: ROUTE\n{"add":["chore"],"guidance":"Use the mechanical worker"}'
                 command = [sys.executable, "-c",
-                           "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); print(sys.argv[3])",
+                           "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); print(sys.argv[3])",
                            str(prompt), "{prompt}", output]
                 settings = "[manager]\ncommand = " + json.dumps(command) + '\n[workers]\ndefault = ["false"]\n'
                 if configured:
@@ -876,7 +980,7 @@ esac
             with events_path.open("a") as events:
                 events.write(json.dumps({**escalation, "round": round_number}) + "\n")
             command = [sys.executable, "-c",
-                       "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.stdout.write(sys.argv[3])",
+                       "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); sys.stdout.write(sys.argv[3])",
                        str(prompt), "{prompt}", output]
             (repo / config.CONFIG_NAME).write_text(
                 "[manager]\nrounds = 3\ncommand = " + json.dumps(command) + "\n")
@@ -1112,7 +1216,7 @@ class DispatchTest(unittest.TestCase):
         prompt = root / "prompt.txt"
         (root / "reply.txt").write_text(reply)
         command = [sys.executable, "-c",
-                   "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); "
+                   "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(pathlib.Path(sys.argv[2]).read_text()); "
                    "sys.stdout.write(pathlib.Path(sys.argv[3]).read_text())",
                    str(prompt), "{prompt}", str(root / "reply.txt")]
         (repo / config.CONFIG_NAME).write_text(
```

### README.md at this head (documented conventions)

```markdown
# factory

A label-driven autonomous ticket pipeline for any GitHub repository. Issues
labelled `ready-for-agent` are claimed by a coding agent in a git worktree,
gated by your own deterministic checks, reviewed by a second model, opened as a
PR, and landed on `main` once the gate, the reviewer, GitHub CI, and freshness
against `main` all agree. Anything the pipeline cannot resolve is handed back
with a `ready-for-human` label and the evidence attached.

It runs on your machine, on a systemd timer, with the agent CLIs you already
have. State is GitHub (labels, comments, PRs) plus a gitignored `.factory/`
directory; the dispatcher itself is stateless and safe to re-run.

```
 needs-triage ──factory triage──▶ ready-for-agent ──factory dispatch──▶ agent/<n> PR ──merge stage──▶ main
                    │                                     │                  │
                    ▼                                     ▼                  ▼
                needs-info                          ready-for-human      factory-approved
```

## Install

Two ways in; both end with the same `factory` CLI on your machine.

**Have your coding agent do it:** install the skill and ask the agent to set up
the factory in your repo. The skill installs the CLI if it is missing, writes
the config from your CI, and runs the doctor.

```sh
npx skills add mikeroysoft/factory
```

**Or by hand.** Linux, Python ≥ 3.11, `git`, `gh` (authenticated with push access), and:

- a **worker** agent CLI that accepts a prompt file and works in a directory
  (default `omp -p`; `droid`, `codex exec`, `claude -p`, … work the same way)
- optionally, a **manager** agent CLI that accepts a prompt file and works in a directory
- a **reviewer** CLI that answers an inline prompt on stdout (default `omp -p --model anthropic/claude-fable-5-1`; `codex exec` works the same way)
- optionally, an OpenAI-compatible local model for triage (Ollama, vLLM,
  llama.cpp, LM Studio)

```sh
uv tool install git+https://github.com/mikeroySoft/factory          # stable: latest release
uv tool install git+https://github.com/mikeroySoft/factory@main     # latest: tip of main
uv tool install git+https://github.com/mikeroySoft/factory@v0.3.0   # a specific release
```

`pipx install` and `pip install --user` take the same URLs. The repository's
default branch is `stable`, which moves only on a tagged release; see
[CHANGELOG.md](CHANGELOG.md). No Python dependencies.

## Set up a repository

```sh
cd your-repo
factory init            # .factory.toml, .gitignore, issue template, labels
$EDITOR .factory.toml   # put your real test/lint commands in [[gate.check]]
git add .factory.toml .gitignore .github/ISSUE_TEMPLATE/agent_task.md && git commit
factory doctor          # tools, auth, remotes, model endpoint
factory install --dashboard   # systemd user timer every 10 min + dashboard on :8765
```

Generated triage, dispatch and dashboard services use `python -P -m factory`:
Python does not prepend the repository working directory to its module search
path, so an older checkout cannot shadow the installed Factory package.
The interpreter must already have Factory installed; explicit `PYTHONPATH`
overrides remain operator-controlled. This source change does not rewrite
existing units: review their import paths before any separately authorized
unit update or service reload.

Then file an issue with the **Agent task** template (Scope / Touches / Exit
gate / Out of scope). It gets `needs-triage`; the next pass triages it; if it is
fully specified it becomes `ready-for-agent` and is picked up.

For a fork that tracks an upstream, set `[repo].upstream = "upstream"` and the
dispatcher merges new upstream commits into your `main` (gated) before each
merge stage.

## Commands

| Command | What one invocation does |
|---|---|
| `factory triage` | Labels every `needs-triage` issue via the local model: `ready-for-agent` (with an agent brief), `needs-info` (with the question), `ready-for-human`, or a `wontfix` proposal comment. `--dry-run`, `--issue N`, `--replay a,b,c`. |
| `factory dispatch` | One stateless pass: upstream sync → merge stage (at most one PR) → manager → claim up to `max_active` tickets → worker → gate → PR → review → up to `review_rounds` bounces. `--ticket N` forces one issue; `--dry-run` prints the plan. |
| `factory manage` | Resolves untouched `ready-for-human` escalation packets, once per escalation and within `[manager].rounds`. Disabled unless `manager.command` is configured. `--dry-run` lists eligible tickets. |
| `factory gate` | Runs the deterministic gate in the current worktree and writes a Markdown report. Workers run it themselves; the dispatcher re-runs it as the evidence of record. |
| `factory stats` | Ticket table: attempts, review rounds, hours to merge, escalation count, resolver attribution, minutes in `ready-for-human`, and re-queues. Reads GitHub plus existing `events.jsonl`. `--by-worker` reads only events and shows every configured worker label: first-attempt gate pass rate, all attempts (including review bounces), and known cost. Attribution uses claim labels with current worker precedence; unclaimed attempts are excluded, missing rates/cost are `n/a`. The dashboard Ops view shows the same worker metrics. `--json`. |
| `factory learn` | Reads the last N finished tickets' event trail, failing-attempt log tails, reviewer findings, and escalation reasons; asks the local model for ≤10 repo-specific lessons; writes `.factory-lessons.md` (you commit it). Every worker prompt carries it. `--dry-run`, `--last N`. |
| `factory dashboard` | Local ops UI: tickets by stage, authoritative in-flight phase when known, gate reports, worker logs, journal heartbeat, upstream drift, and an action list with one-click answers. `--json` prints the existing snapshot, including independent executions and local interruption reconciliation. `--host 0.0.0.0` exposes it (and its mutating `/api/act`) to your network. |
| `factory dashboard --runtime-json` | One bounded schema 1 runtime observation using only local read-only evidence; no GitHub, model probe, journal append, lock acquisition, or state creation. Partial source failures remain structured JSON. See [runtime contract](#bounded-runtime-json-schema-1). |
| `factory evidence --root /path/to/main-checkout` | One explicit-repository schema 1 JSON read: compact cases, selected evidence, workflow/file/PR/CI investigations, or capabilities. Read-only GitHub GETs and F03 local evidence; no model, action execution, or state writes. See [evidence contract](#bounded-project-evidence-json-schema-1). |
| `factory doctor` / `init` / `install` | Onboarding, above. |

Every command reads `.factory.toml` from the main checkout, even when run
inside one of its worktrees.

Worker and manager commands receive `{prompt}` as a prompt-file path and `{cwd}`
as the worktree (or repository root); reviewers receive `{prompt}` as inline
text. OMP loads a prompt file only when it is prefixed with `@`, so use
`@{prompt}`: bare `omp {prompt}` passes the path as prompt text and is rejected.
Keep `{cwd}` in worker and manager commands, and configure the manager CLI in
read-only/no-tools mode: the prompt prohibits file edits, but an arbitrary
configured executable is trusted, not sandboxed by factory. The manager reads
the packet, `.factory-lessons.md`, and optional `.factory/manager/notes.md`.
Its last `DECISION:` header
selects `RETRY`, `REWRITE`, `SPLIT`, `ROUTE`, `FIX`, or `HUMAN`, followed by the decision
body. RETRY/HUMAN use plain text; REWRITE uses the complete replacement issue body.
SPLIT uses a JSON array of `{title, body, blocked_by}` children, with `blocked_by`
containing 1-based indexes of earlier children. ROUTE uses `{add, remove, guidance}`,
with label arrays restricted to configured worker labels (not `default`).
Workers can use either the legacy argv array or a `[workers.<label>]` table with
`command = [...]` and optional `when = "..."`. The prompt lists routable labels
with their `when` rules; neither ROUTE nor FIX accepts unlisted labels.
FIX uses `{"worker":"ci-fix","guidance":"..."}` to run exactly one selected worker
round in the kept `agent/<n>` worktree for its open PR, ignoring other ticket
labels. It passes guidance and the escalation packet to the worker, re-gates,
pushes only on gate PASS, and re-reviews. Only a fresh APPROVE restores
`factory-approved`; failure stays with the human. FIX does not merge or requeue
the issue. The template includes opt-in `ci-fix` and `conflict` profiles for a
human to apply in host config; the manager cannot add profiles or edit config.
Code validates the output, records a `manage` event before GitHub mutations, and
leaves malformed decisions with a prefixed HUMAN diagnosis. Split children enter
`needs-triage`; the parent keeps `ready-for-human` with child blocker lines.
A trailing fenced `notes` block replaces `.factory/manager/notes.md`
(gitignored, never committed, carried into every later manager prompt). The block
is optional; code refuses an empty or over-16 KB replacement, keeps the existing
file, and records the outcome in the `manage` event's `notes` field
(`written`, `empty_rejected`, `oversize_rejected`, or null when no block was sent).
Manager executions and ticket-lock waits use the lifecycle journal. If a GitHub
mutation fails, the execution records a terminal failure and the ticket remains
with the human; other tickets can proceed. The consumed round is not replayed,
because a partial rewrite or split may already have changed GitHub.

Human-touch metrics are read-only; no manager behavior is required. A
`ready-for-human` label addition starts an escalation interval; removal ends it
and attributes the resolution to that removal's actor (`User` → human, `Bot` →
factory, absent/other → unknown). Resolver logins are retained. Automation using
a human account is indistinguishable from manual activity under that account.
Open intervals accrue until now, or until closure/merge for finished tickets.
Re-queues count `ready-for-agent` additions after the initial queue entry, with
repeated `claimed` trace records as a fallback. Trace escalation counts likewise
supplement timeline counts without adding the two counts together.

The stats footer and dashboard KPIs show escalations in the trailing seven days
and the percentage of attributed resolutions performed by humans; unresolved
and unknown resolutions are excluded from that denominator (`n/a`/`null` when
none are attributed). `factory dashboard --json` exposes
`metrics.escalations_per_week`, `metrics.human_resolved_pct` (0–100), and each
ticket's `human_touch` details. The dashboard retains its existing 100-issue,
100-PR, and 100-timeline-item query limits; stats paginates label timelines.

## How a ticket moves

1. **Triage.** A deterministic lint rejects bodies under 80 characters or
   without acceptance criteria (`needs-info` with a specific question). The
   model then decides between the four labels; `wontfix` is only ever proposed.
2. **Claim.** The dispatcher re-reads the issue (search-backed listings lag),
   assigns itself, takes a per-ticket `flock`, and creates the worktree
   `.factory/wt-<n>` on branch `agent/<n>`.
3. **Work.** The worker gets the issue, its comments, standing instructions
   (commit incrementally, never touch `main`, never `git stash`, finish with
   `factory gate`), and — on retries — the previous gate report or the
   reviewer's findings. Up to `max_attempts` rounds within `budget_min`.
4. **Gate.** `conflict-markers`, your `[[gate.check]]` list in order, then a
   `leak-scan` of added lines against a regex. Checks marked `exclusive`
   serialise on a host-wide lock (one GPU, many worktrees). Every check has a
   timeout; a wedged check fails instead of holding the lock.
5. **Review.** The reviewer runs the diff itself, gets the gate report inline, and
   is told to read issue #N's comments (the triage brief, approved scope changes).
   Every finding cites `path:line`; a required fix also cites an acceptance
   criterion, a documented rule with its source, or a concrete correctness/security
   defect with its trigger and impact — preferences and hypothetical extensibility
   are optional suggestions, never requirements, and a passing gate does not
   excuse a defect it did not detect. Net-new abstractions beyond the brief,
   whether the diff introduced them or the review asks for them, need that same
   justification, but a missing justification alone does not block. Reviews end
   with `VERDICT: APPROVE` or `VERDICT: REVISE`; optional suggestions alone mean
   APPROVE. Each `REVISE` sends the findings back to the worker, flagged so only
   the required fixes are binding (re-gate, push, re-review), up to
   `review_rounds` times; then it escalates.
   `APPROVE` adds the `factory-approved` label — durable evidence on the PR,
   not in memory.
6. **Merge stage** (start of the next pass). One PR per pass, requiring all
   four: gate PASS in the PR body, `factory-approved`, green GitHub checks
   (fail-closed on missing or unparsable checks), and a head that already
   contains the current `main` tip. Behind `main` → rebase, re-gate on this
   host, force-push, merge next pass. Red CI → label removed, escalated once
   with the failing check names. A human blocks any merge by requesting
   changes on the PR.
7. **Escalation.** Budget exceeded, gate failed thrice, second `REVISE`,
   nothing to PR, rebase conflict, red CI: the issue gets `ready-for-human`,
   loses the assignee and `ready-for-agent`, and receives a comment with the
   reason and the worker log path. The worktree is kept for forensics.

## Configuration

`.factory.toml` at the repository root; every key is optional. The template
written by `factory init` documents them all. The ones you will actually set:

```toml
[repo]
# upstream = "upstream"          # fork workflow: sync upstream main each pass

[dispatch]
review_rounds = 1                # REVISE -> worker -> re-review cycles
# cost_pattern = 'Total cost:\s*\$([0-9.]+)'   # $ from the worker log (Claude Code prints this)

[workers]                        # ticket label -> argv; {prompt} file, {cwd} worktree
default = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
chore   = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]

[review]
command = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]   # {prompt} = review prompt text

[manager]                         # optional; unset command disables it
command = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]   # {prompt} = manager prompt file

[gate]
timeout = 1200
lock = "/tmp/factory.lock"

[[gate.check]]
name = "lint"
run = ["cargo", "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"]
exclusive = true

[[gate.check]]
name = "tests"
run = ["cargo", "test", "--workspace"]
exclusive = true

[leak_scan]
pattern = "internal|confidential|proprietary|private|jira|confluence|\\.corp|\\.internal"

[triage]
url = "http://127.0.0.1:11434/v1/chat/completions"
model = "qwen3:30b"
```

Labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`,
`factory-approved`, `chore`) and the `agent/<n>` branch scheme are fixed
conventions; `factory init` creates the labels.

## Operating it

- **Dashboard** (`factory dashboard`): **Inbox** opens a full **Understand →
  Compare → Decide** briefing for each case needing human judgment. The question,
  situation, FM recommendation, relevant earlier decisions, uncertainty, options,
  consequences, and next owner stay visible; raw evidence is expandable. **Ops**
  retains the board, telemetry, dispatcher runs, and task drawers.
  **Ask FM** works on a whole task or a specific source/log and returns cited
  answers. It requires an authenticated `omp` installation; `[manager].model`
  chooses the model (host-wide: `[defaults.manager]`). If unset, an existing
  `manager.command` supplies only its `--model` value, otherwise OMP's default
  model is used. The dashboard never executes that command: questions run a
  bounded, read-only, no-tools OMP process against server-collected evidence.
  Evidence is sent to the selected model provider; questions are not posted to
  GitHub. Errors remain visible and retryable, never replaced with canned advice.
  Decisions require rationale and an exact mutation preview; stale or incomplete
  snapshots block execution. Confirmed decisions leave GitHub rationale comments
  and a local `human-decision` audit event with success, partial, or failed outcome.
  Drafts and conversations survive refresh within the same browser session.
- **Spend**: the *Spend* KPI and each ticket's attempts tab total worker+gate
  wall clock from `events.jsonl`, plus dollars when `cost_pattern` matches
  your worker's log.
- **Learning loop**: after a batch of tickets, `factory learn --dry-run`,
  read the proposed lessons, then `factory learn` and commit
  `.factory-lessons.md`. Workers see it on every ticket. The eval signal is the
  dashboard's *first-gate pass* and *bounce rate* KPIs moving after the change;
  edit or delete lessons that don't earn their keep.
- **Audit trail**: `.factory/events.jsonl` retains the ticket outcome events
  (`claimed`, `attempt`, `pr-opened`, `review`, `approved`, `refreshed`,
  `merged`, `escalate`, `upstream-sync`, and `human-decision`) alongside
  versioned `lifecycle` records. See the [event contract](#execution-event-contract)
  below. A ticket's history is local; no GitHub call is needed.
- **Handoff notes**: each worker attempt ends by writing
  `.factory/wt-<n>/.factory/handoff-<n>.md` (what changed, what is unverified,
  what next). The next attempt gets it in its prompt; an escalation quotes it
  in the issue comment.
- **Logs**: `.factory/logs/<n>-attempt-<k>.log` per worker round;
  `.factory/wt-<n>/.factory/gate-report-<n>.md` per gate;
  `journalctl --user -u factory-<repo>.service` for dispatcher passes.
- **Re-run one ticket by hand**: `factory dispatch --ticket N` (bypasses the
  frontier and its label checks; respects the in-flight lock).
- **Stop everything**: `systemctl --user disable --now factory-<repo>.timer`.
  In-flight tickets finish their current pass; nothing new is claimed.
- **Tear down a ticket**: remove the worktree (`git worktree remove --force
  .factory/wt-<n>`), delete `agent/<n>`, and re-label the issue.

## Execution event contract

`.factory/events.jsonl` is the single append-only journal for legacy ticket
outcomes and authoritative execution evidence. An execution is one entered
scope, not a ticket's entire history. A dispatcher pass, an independent triage
run with no tickets, every worker attempt, and each later PR revisit have their
own identities. Parent/child scopes may overlap; never collapse them into the
newest ticket event.

### Version 1 lifecycle rows

Every `event: "lifecycle"` row includes all these keys. `null` means not
applicable or not known; it is not a fabricated ticket, run, round, or result.

| Key | JSON type | Meaning |
|---|---|---|
| `event` | string | Always `"lifecycle"`. |
| `schema_version` | integer | `1`; unversioned ticket events are not lifecycle version 1. |
| `event_id` | string (UUID) | Stable identity of this transition. Ordinary events use UUIDv4; reconciled interruption exits use a deterministic UUIDv5 per execution. |
| `sequence` | integer, ≥1 | Starts at 1, increases strictly within `execution_id`, allocated under the journal lock. |
| `execution_id` | string (UUID) | One stage invocation; never reused for a retry or revisit. |
| `parent_execution_id` | string (UUID) or null | Immediately enclosing execution, including across instrumented subprocess launches; null for an independent root. |
| `root_execution_id` | string (UUID) | Root execution of this causal tree; an independent execution names itself. |
| `dispatcher_run_id` | string (UUID) or null | Shared by a real dispatcher pass and its descendants. An independently invoked triage or gate has null, not an invented dispatcher run. |
| `ticket` | integer or null | Associated issue number; dispatcher, scheduling, and no-ticket triage scopes need no issue. |
| `attempt` | integer or null | Existing worker-attempt numbering, inherited by its gate. Review-bounce attempts retain the existing `max_attempts + bounce` numbering. |
| `review_round` | integer or null | Initial review is 1; revision worker/gate/review scopes use `bounce + 1`. Initial worker/gate scopes and unrelated activity have null. |
| `stage` | string | Actual entered scope, listed below; not inferred from labels or artifact times. |
| `kind` | string | `"enter"`, `"exit"`, or an evidence event listed below. |
| `at` | string | UTC source timestamp, ISO 8601 with microseconds and `Z`. A reconciled exit uses the time of observation, not an estimated crash time. |
| `outcome` | string or null | Terminal classification on `exit`; null on other kinds. |
| `reason` | string or null | Supported terminal reason or diagnostic text, not a closed enumeration; null when no reason is known. |
| `process` | object or null | Owner identity sampled on entry: `pid` (integer), `boot_id` (string or null), `start_ticks` (integer or null), `pid_namespace` (string or null), `uid` (integer or null), `state` (Linux process-state string or null), `ppid` (integer or null). Factory emits an object; the reader also accepts null as unavailable authority. This cached identity is not a live heartbeat. |
| `locks` | array of objects | Recorded exclusion evidence, possibly empty. Each object has `path` (absolute-path string), `device` (integer or null), and `inode` (integer or null). Null identity fields mean unavailable evidence. |

IDs and sequence numbers survive repeated reads. Sort a single execution by
`sequence`, use parent/root/run IDs for causality, and deduplicate by `event_id`.
Neither wall-clock timestamps nor physical append order establish a causal total
order across independent executions. A scope has one `enter` and at most one
terminal `exit`; an abrupt death can leave the exit absent until observation
has enough evidence to reconcile it. Extra keys and evidence kinds may be
added; consumers should ignore those they do not understand. Unsupported schema
versions are not interpreted as version 1 by the existing observer.

| Stage | Boundary |
|---|---|
| `dispatcher` | One real dispatcher pass, including passes with no ticket. Completion means the pass ended, not that any ticket merged. |
| `scheduling` | Frontier/capacity evaluation and scheduling; separate from ticket execution and merge eligibility. |
| `landing` | The existing nonblocking merge-lock request and, when acquired, upstream sync/merge pass through actual unlock. |
| `ticket` | A ticket admission/invocation, including nonblocking lock request and admission re-read. Admission refusal is not worker time. Later PR passes have separate `merge-eligibility` executions. |
| `triage`, `triage-ticket` | Whole triage invocation and each actual ticket decision. Standalone triage retains its own root identity and null dispatcher association. |
| `worker` | One worker subprocess attempt. |
| `gate`, `gate-check` | An invoked gate and each actually executed check. Skipped checks and a disabled leak scan do not enter a check scope. |
| `review` | One reviewer invocation, attributed to its review round. |
| `merge-eligibility` | Assessment/refresh of one candidate in this dispatcher pass; not a merge. |
| `merge` | Actual PR merge or upstream integration (which may contain a gate child). Only a successful PR merge or upstream push records `merged`. Approval alone does not. |
| `resource-observation`, `scheduling-observation` | Change-only local evidence records, not pipeline invocations. They have stable observation-scope execution/root IDs, no `enter`/`exit`, and null parent/dispatcher/ticket/attempt/round fields; execution occupancy excludes them. |

Evidence kinds retain the common keys above and add these kind-specific keys:

| Kind | Additional keys |
|---|---|
| `handoff` | `handoff_id`: UUID string, persisted before launching a child. |
| `child_start` | `child_process`: process identity object; `handoff_id`: UUID string or null. |
| `child_exit` | `child_process`: the recorded child identity object. |
| `lock_acquired`, `lock_released` | `lock`: path string; `locks` reflects the updated recorded set. |
| `check` | `check`: string check name. |
| `timeout` | `command`: array of argument strings; `timeout_seconds`: integer. |
| `result` | Depending on the mechanism: `returncode` (integer), `command` (argument-string array or string), `timed_out` (boolean), `timeout_seconds` (integer), `check` (string), `passed` (boolean), `verdict` (`"APPROVE"` or `"REVISE"`), and/or `parsed` (boolean). These keys are present only when that evidence was obtained. |
| Ordinary `exit` with unreaped children | `evidence`: object containing `children` (process identity/state objects as below). Default `completed` becomes `unknown`; a supported exception classification is retained. |
| Reconciled `exit` | `reconciled`: true; `observer_process`: process identity object; `evidence`: the observation object described below. |

### Outcomes and uncertainty

| `outcome` | Meaning |
|---|---|
| `completed` | The entered operation returned normally; not a claim that a ticket or PR is complete. |
| `product_feedback` | Configured checks found failing code, a parsed successful reviewer requested `REVISE`, or triage requested information/human attention/proposed wontfix. |
| `project_escalation` | The existing project escalation path ran; not a broken runtime mechanism. |
| `approved`, `merged`, `refreshed` | The corresponding operation succeeded; these are distinct outcomes. |
| `not_admitted`, `not_eligible` | Admission re-read refused the ticket, or merge prerequisites did not allow merging. No skipped downstream scope is invented. |
| `mechanism_failure` | Evidence that the configured mechanism could not run, such as a missing/permission-denied executable or unavailable triage endpoint. |
| `unknown` | The cause is unsupported or uncertain, including unexplained worker/reviewer nonzero exits, unparsed verdicts, command/API errors, and timeouts. A timeout alone is not proof of runtime failure. |
| `interrupted` | A previously unterminated execution was reconciled using authoritative liveness and lock evidence. |

Reasons include `configured_check_failed`, `conflict_markers`,
`leak_scan_matches`, `APPROVE`/`REVISE`, `check_timeout`,
`triage_endpoint_unavailable`, `no_tickets`, `state_changed`, `ci_pending`,
and `no_passing_ci`; exceptions may instead provide diagnostic text. Reasons
are evidence, not a replacement for `outcome`. Existing gate exit codes,
review retry behavior, claim locks, concurrency limits, and merge prerequisites
are unchanged.

### Observation, interrupted writes, and CLI semantics

The existing dashboard snapshot (`factory dashboard --json` or
`/api/snapshot`) includes an additive top-level `executions` array. It retains
every observed execution, including concurrent stages, no-ticket runs, and local
evidence when GitHub collection fails. Each entry contains
`execution_id`, `parent_execution_id`, `root_execution_id`,
`dispatcher_run_id`, `ticket`, `attempt`, `review_round`, and `stage` with the
types above, plus:

- `state`: `"active"`, `"completed"`, `"failed"`, `"interrupted"`, or `"unknown"`.
  Only `mechanism_failure` maps to `failed`; product feedback/escalation and
  other known ordinary outcomes map to `completed`.
- `entered_at`: source `enter` timestamp; `ended_at`: source/observed exit
  timestamp or null. An exit with unknown outcome has an `ended_at` but is
  not active.
- `outcome` and `reason`: terminal values or null while unterminated.
- `events`: source lifecycle rows in sequence order.
- `evidence`: normally null on ordinary terminal exits, or the partial
  `children` object above when the scope exited before reaping its children.
  An unterminated/reconciled observation instead has an object with
  `process` (`"alive"`, `"dead"`, `"unknown"`), `children` (objects with
  `process` identity and the same `state` values), `locks` (recorded lock
  objects plus `state`: `"held"`, `"free"`, or `"unknown"`), `descendants`
  (process identities), `scan_complete` (boolean: process-scan readability),
  `descendant_absence_proven` (boolean: authoritative absence of surviving
  descendants), and `pending_handoffs` (UUID-string array).
- `wait`: the current known wait object plus source `event_id` and `at`, or null. It is distinct from
  execution liveness: a live waiting process can have `state: "active"` without
  doing check/worker work. Unknown/dead/terminal executions do not retain a
  current wait; their source wait rows remain historical evidence.

Observation uses Linux `/proc`, boot identity, PID namespace, process start
ticks, recorded children/causal descendant context, and existing lock inodes.
A reused PID or an old artifact cannot prove activity. A positively identified
live owner or descendant is active, even if its parent ended. On the same boot,
a held lock alone prevents declaring interruption but does not identify an
active stage.
Inaccessible/incomplete process evidence, a missing/replaced lock inode, or an
unresolved launch-registration gap yields unknown when no live process can be
proven. For an execution that previously launched a process tree (including
through nested scopes), a same-boot scan cannot rule out a reparented orphan
that removed its lifecycle environment. It therefore remains unknown after
the last recorded/tagged survivor disappears; absence from the scan is not
proof that the entire tree ended. A still-live registered or tagged child
remains active. An abruptly killed scope that never launched descendants can
be reconciled when its recorded process is known dead, no pending handoff
remains, and its recorded locks are free. Interruption requires authoritative
evidence that every possible descendant ended, not merely that none was found.
A known boot-ID change proves the previous execution and its descendants ended,
even with a pending handoff or a lock held by a current-boot process. A same-boot
PID-namespace mismatch remains unknown. `scan_complete` alone never proves
descendant absence; this conservative limitation also applies to open ancestor
scopes of a launched tree.
Observation does not change scheduling or lock ownership.

The first conclusive observation appends one stable interruption exit under the
journal lock. Repeated observations reuse that record and its actual observation
time; they do not manufacture another transition. Thus dashboard observation can
write reconciliation evidence locally, but does not mutate GitHub. The existing
15-second HTTP snapshot cache remains; `?fresh=1` requests a fresh observation.
No separate lifecycle CLI or additional network probe is introduced.

The existing ticket `phase` is null unless there is one unambiguous active,
non-waiting leaf among its unresolved executions. Active wrappers do not hide their child
stage; an unknown child or simultaneous independent stages prevents selecting
one. When present, `phase` keeps `at` (source entry timestamp), `artifact`
(legacy field name, now the authoritative stage string), and `attempt`
(integer or null), and adds `execution_id` (UUID string). Logs/reports remain
available as evidence, never as stage truth. A ticket lock still drives the
existing in-flight/admission count, not proof of a particular executing phase.

All Factory producers serialize complete UTF-8 JSON lines under a shared file `flock`,
flush and fsync before returning, and allocate execution ordering under that
same lock. Readers accept only newline-terminated JSON objects and skip malformed
JSON, non-object rows, and unterminated tails. On the next append an unterminated
tail is invalidated with a NUL byte and newline before the new record; even a
syntactically complete but uncommitted tail is never promoted into activity.
Old unversioned rows retain their existing event names and fields without
retrofitted IDs, stages, or liveness. New legacy ticket events emitted within
an execution also carry `execution_id` (UUID string) and `dispatcher_run_id`
(UUID string or null) as causal references; they are still not lifecycle rows.
Dashboard spend counts only legacy `attempt` rows;
`learn` excludes lifecycle rows from finished-ticket selection and evidence;
`stats` still uses GitHub issue/PR comments, so lifecycle exits do not double
attempt, review, or outcome counts. `dispatch --dry-run` and
`triage --dry-run`/`--replay` record no lifecycle activity.
Dispatcher dry-run does not create claim/merge lock files or fetch remote refs.
With an upstream configured, it reports that a real pass would fetch and
evaluate upstream rather than claiming a fresh behind/ahead count.

### F02 waits and evidenced resource ownership

F02 adds evidence to the version 1 lifecycle journal, not a second telemetry
stream, controller, resource broker, or lock. All rows retain the identities,
sequence, source timestamps, and interruption rules above. The bounded F03
runtime CLI below reads these same producer records without persisting observations.

Known waits use `kind: "wait"` with a `wait` object:

| Key | Type and meaning |
|---|---|
| `reason` | Nonempty string for a known reason; absent knowledge is represented by no current wait, never guessed from elapsed duration. |
| `mode` | `"blocking"` (an actual acquisition can block), `"retry_next_pass"` (this pass skips), `"admission"` (capacity decision), or `"eligibility"` (observed merge prerequisite). |
| `resource` | Resource descriptor below, or null for non-resource waits. |
| `details` | Object containing only decision evidence available at that point. |

`wait_end` ends a blocking wait when acquisition succeeds; a request itself is
not acquisition. A terminal scope ends its current wait without claiming the
underlying prerequisite became satisfied. Nonblocking skips, capacity decisions,
and merge eligibility remain historical decision facts after their scope exits,
not indefinitely active stages.

| Reason | Existing observation point / details |
|---|---|
| `capacity_reached` | Admission count reached `max_active`; `active` and `max_active` integers. Demand does not prove dispatcher liveness. |
| `ticket_lock_contended` | Ticket preflight found the lock held or its nonblocking acquisition lost the race; retry next pass. |
| `merge_lock_contended` | Nonblocking landing lock miss; skip this pass, retry next pass, never convert to a blocking wait. |
| `exclusive_resource` | Gate observed its exclusive lock held before the unchanged blocking acquisition. |
| `ci_pending` | Existing `pr_checks` result contained pending checks; `pr` integer. No extra CI query or polling loop. |
| `no_passing_ci` | Existing result had no passing check; `pr` integer. The cause is unknown, not an inferred CI outage. |
| `scheduled_next_pass` | Local timer explicitly active, service explicitly idle, and a future `next_at` timestamp reported by the existing systemctl seam. |

Review revision, escalation, eligibility, execution-stage occupancy, and waits
are separate facts. None of these reasons, a held resource, or elapsed time
alone creates a machinery incident. Gate outcomes and CI/human-veto prerequisites
are unchanged.

Resource events distinguish `resource_requested`, enriched `lock_acquired`, and
enriched `lock_released`. Each includes a `resource` descriptor. Only an actual
successful flock acquisition supplies confirmed holder evidence. Acquired and
released rows share an `acquisition_id`, so delayed evidence for an older holder
cannot clear a newer acquisition. Inherited F01 `locks` support liveness only:
children and dispatcher parents do not thereby become resource owners.

The gate subprocess acquires its exclusive lock once, immediately before the
first non-skipped exclusive check, and retains it through all remaining checks.
Ticket locks span the pipeline; the landing lock spans upstream sync and merge.
There are no new exclusion locks, changes to acquisition order, retry policy,
capacity accounting, check execution, or scheduling.


Resource descriptors and observations have this serialized contract:

| Descriptor key | Type and supported scope |
|---|---|
| `id` | Opaque UUID string, stable for the canonical lock pathname and supported scope; not a ticket number or dependency name. |
| `scope` | `"repository"` for ticket/merge exclusion, or `"host"` for the configured exclusive gate lock. |
| `host_id` | Opaque host identity string derived from machine identity; without machine identity, limited to the current boot. If neither authority exists, a process-local opaque fallback prevents cross-host grouping and observations remain unknown. |
| `repository` | Canonical journal-directory string for repository scope; null for host scope. Different repository journals do not imply shared ticket/merge exclusion. |
| `lock` | F01-style `path`, `device`, `inode` evidence for the canonical pathname. Missing inode/device is unknown authority. |

Host-scoped IDs permit grouping only observations of the same configured lock
on the same supported host identity. Different lock paths are not the same
GPU or dependency merely because their check names match. Canonical symlink
paths coincide; hard-link aliases and independently configured paths are not
automatically unified. Identity names a lock pathname, not every past inode
unlinked from it. A replaced inode cannot confirm an old acquisition. A single
repository's observation does not prove every factory is affected; no journal
from another repository is read to guess its holder.

All three resource operation kinds add `blocking` (boolean: acquisition mode)
and `acquisition_id` (UUID string for acquired/matched released; null on
requested or an unmatched release, which cannot clear a holder).
The F01 `lock` path remains on acquired/released rows. The common execution,
root, parent, dispatcher, ticket, attempt, review-round, and process fields
identify the actual requester/holder; request identity is never substituted for
holder identity. `wait_end.wait_event_id` names the ended wait's event UUID.

| Resource observation key | Type and meaning |
|---|---|
| `resource` | Descriptor above. |
| `state`, `ownership` | Independent string enums described above. `none` is supported only by observed free state. |
| `owner` | Null or object with all common execution identity fields, recorded `process`, `acquisition_id`, and source `acquired_at` timestamp. |
| `requests` | Array of currently live, unterminated requesters not yet acquired/released: common execution identity fields, `process`, source `event_id`, `requested_at`, and `blocking`. A pending request is not ownership. |
| `evidence` | Object: `lock_state` (`held`/`free`/`unknown`), `attribution` (`proc_locks`/`unavailable`), `holder_pids` (integer array or null). Kernel PIDs alone are not confirmed execution identity. |
| `event_id`, `at` | Stable identity and source time of the last distinct local resource observation. |
| `observed_at` | UTC time of this local evidence collection, distinct from transition time. |

Changes are persisted as `kind: "resource_observation"` in the same lifecycle
journal, carrying `resource`, `state`, `ownership`, `owner`, `requests`, and
`evidence`. Observer rows have `reconciled: true` and the observer's `process`.
Acquisition/release history remains separate from current attribution.

`dispatcher.schedule` contains `wait` (the wait object above or null),
`timer_active` and `service_active` (boolean or null), `event_id`, `at`, and
`observed_at` with the same transition-versus-collection distinction.
Change-only `kind: "scheduling_observation"` rows carry `wait`, `timer_active`,
and `service_active`; scheduled wait details include `next_at` (UTC timestamp).

The full dashboard JSON adds `resources`. Each resource observation separates
`state` (`held`, `free`, `unknown`) from `ownership` (`confirmed`, `unknown`,
`none`). A held lock is not proof of ownership. Confirmation requires both a
live recorded process identity and matching kernel lock attribution; missing
authority, external processes, old F01-only lock rows, and attribution gaps
remain unknown. A dead recorded execution is never retained as a confirmed
current holder, even if its old lock remains held.

Resource observation timestamps describe when evidence was collected. A
reconciled ownership change or free observation does not invent the exact time
an unobserved process died or released its lock. Repeated unchanged observations
reuse transition identity/time rather than producing repeated release/wait events.

`dispatcher.timer.active` and `dispatcher.service_active` now accept null when
local authority is unavailable. False means explicitly inactive/failed, not a
missing command, inaccessible service manager, or absent output. The additive
`dispatcher.schedule` records timer/service evidence and a known scheduled wait
only when confirmed; otherwise its `wait` is null. Timer interval configuration
and an empty frontier do not establish next-pass intention or deliberate pause.
Dashboard status/configuration display unknown and suppress unsupported
countdowns. Schedule observations never create a dispatcher-run identity.

Because the journal may contain a torn row, a tolerant local ticket query is:

```sh
python - <<'PY'
import json
from pathlib import Path
from factory.lifecycle import read_events
for row in read_events(Path(".factory/events.jsonl")):
    if row.get("ticket") == 42:
        print(json.dumps(row))
PY
```

## Bounded runtime JSON (schema 1)

`factory dashboard --runtime-json` prints one JSON object and exits. It is a
separate local read path, **not** a filtered full snapshot. `--json`, HTTP
`/api/snapshot`, and the normal dashboard retain their existing slower GitHub,
triage-probe, and writable reconciliation behavior described above.

The runtime command accepts no server options (`--host`, `--port`, `--no-open`)
and cannot be combined with `--json`. Argument errors exit 2. Fatal repository
discovery/configuration errors exit nonzero with a sanitized diagnostic on stderr
and no runtime JSON. A usable projection, including partial or wholly unavailable
runtime sources, exits 0: inspect `errors` and observation quality, not just exit
status. Missing GitHub credentials are irrelevant; this command never invokes
`gh`, remote Git operations, model probes, or network APIs.

### Consumer contract

All listed keys are required unless explicitly described as kind-specific.
Nullable values mean unknown/not applicable, never zero, stopped, or a newly
observed transition. Times are UTC ISO 8601 strings. Source times remain unchanged
on repeated reads; `generated_at` and `observed_at` are collection times, not
event freshness. Consumers must ignore additive keys and reject unsupported
schema versions rather than interpreting them as version 1.

| Top-level key | Type / meaning |
|---|---|
| `schema_version` | Integer, exactly `1`; implemented runtime contract, independent of package version. |
| `generated_at` | UTC string, generation time of this projection. |
| `repo` | Configured `owner/repository` string from the main checkout. |
| `dispatcher` | Local service/timer/admission evidence object below. |
| `executions` | Independent execution objects below; overlapping stages are retained. |
| `resources` | Current resource evidence objects below. |
| `events` | Bounded deduplicated supported lifecycle records; never synthetic poll events. |
| `history` | Explicit retained-window coverage object below. |
| `errors` | At most 32 structured partial-error objects, `{source, scope, code}` strings. No exception text, credentials, configuration dumps, or log excerpts. |

`dispatcher` has nullable booleans `service_active`, `timer_active`, and `paused`;
nullable UTC `next_at`; UTC `observed_at`; string `observation`; `capacity`;
`run_ids` (sorted dispatcher-run identity strings); and `latest_transition`
(null or `{event_id, at, execution_id, kind}` from a returned `enter`/`exit`).
Latest means the last retained observed transition in journal order, not an
artifact modification. `capacity` has `configured` (integer), `active` (integer
or null, actual held ticket admission locks), and `complete` (boolean).
Stage count is not admission count. An unavailable service query is null,
not false; inactive service evidence alone does not establish unexpected stop.
`paused` is null because the current producer has no recorded pause intention.
No scheduled intention is inferred from a configured interval.

Each execution has the eight common F01 identity fields (`dispatcher_run_id`,
`root_execution_id`, `execution_id`, `parent_execution_id`, `ticket`, `attempt`,
`review_round`, `stage`) with their types above, plus `state` (`active`,
`completed`, `failed`, `interrupted`, `unknown`), nullable `entered_at`,
`ended_at`, `outcome`, `reason`, and `wait`; `latest_event_id`, `latest_at`;
`observation` and `observed_at`. `wait` uses the F02 object plus source `event_id`
and `at`. The events live only in the top-level array, not duplicated per scope.
An entry outside the bounded window has null `entered_at` and explicit partial
coverage. A missing stage is never reconstructed from logs or artifacts.
Resource/scheduling observation scopes do not become executions.

Recorded exits retain their actual source times and ordinary outcome semantics.
A locally proven interruption without a stored exit changes only the current
execution state: no event is appended, no completion UUID is manufactured, and
`ended_at` stays null. Direct process identity and registered children can prove
liveness. The runtime path does not scan every process environment; missing
descendant evidence remains partial/unknown, not a fabricated completion.
A known boot change can still prove interruption. Unsupported older records
never establish current execution activity.

Resources retain the F02 descriptor, `state`, `ownership`, `owner`, `requests`,
and kernel `evidence` types documented above, plus `observation` and UTC
`observed_at`. `event_id` and `at` are nullable: they identify a retained,
matching persisted observation, not the latest request or this poll. A current
kernel observation without such a record has no invented transition identity
or onset. Confirmation requires matching acquisition identity, inode, kernel
holder PID, and live process identity. A request, inherited lock, replaced inode,
or external holder never becomes a confirmed owner. Released/terminal holders
are removed; source request/acquisition/release events remain in `events`.

### Bounds and partial sources

Repository discovery runs local `git --no-optional-locks rev-parse`; only when a
slug is absent does it run local `git remote get-url origin`. These do not fetch
or contact remotes. The normal main-checkout lookup and host/repository
`merge`/`host_filter` precedence are retained. Only runtime configuration fields
are interpreted: slug, nonnegative integer `dispatch.max_active`, and gate lock.
Each repository/host TOML read is capped at 256 KiB. Invalid runtime configuration
produces `factory: runtime configuration unavailable or invalid`, without echoing
the input. Unrelated worker/model/check settings are not evaluated.

Three allowlisted `systemctl --user` queries read service state, timer state,
and JSON timers. Each local command has a 0.5-second deadline, stdout strictly
below 64 KiB, discarded stderr, and at most another 0.5 seconds for reap after
kill. At most five commands run (four with an explicit slug). The D-Bus address
is forced to a local Unix socket, never an inherited TCP address. Missing tools,
unavailable units, invalid output, overflow and timeouts yield partial errors.
Only explicit `ActiveState=active` is true; transitional states remain unknown.
`next_at` requires an active timer and an explicitly returned future timestamp.
Admission scans at most 1024 directory entries and a 256 KiB kernel lock window;
unreadable/incomplete evidence returns null capacity, not a false zero.

System query errors use source `systemctl`, scope `service`, `timer`, or
`schedule`, and codes `invalid_unit`, `timeout`, `output_limit`, `command_failed`,
`command_unavailable`, `unit_unavailable`, `transitioning`, `unit_failed`,
`invalid_state`, or `invalid_schedule`. Admission errors use source `admission`,
scope `repository`, with `missing`, `unreadable`, `unsupported_file`, `byte_limit`,
`entry_limit`, `changed`, or `invalid_kernel_locks`. Errors contain fixed codes
only; raw stderr and arbitrary stored diagnostic text are never returned.

The journal reader performs one `pread` of at most **1,048,576 bytes** at the
end of the regular file, with no journal lock. It drops the first clipped line,
accepts only newline-terminated UTF-8 JSON objects of at most **16,384 bytes**
(including newline), rejects nonfinite numbers/depth over 32, and returns at most
**512 newest supported unique event identities**. Both bounds clip the beginning,
never promote an uncommitted last line. No logs or full lifetime journal scan.
Concurrent size/mtime changes mark the read partial; it is not an atomic snapshot
across files, processes, or service queries. Ordinary local filesystem reads are
assumed responsive; byte limits do not promise recovery from a kernel-stalled
filesystem.

`history` has these required fields:

| Key | Type / semantics |
|---|---|
| `source` | String, `events.jsonl`. |
| `status` | `empty` for an existing zero-byte journal; `available` for a readable nonempty journal (even with no usable records); `missing`; or `unreadable`. |
| `start_at`, `end_at` | Nullable UTC strings: minimum/maximum **returned supported** source timestamps, not file age or an inferred lifetime interval. Both null when none survive. |
| `complete` | Boolean: the present file was fully covered without detected gaps; never a promise of exhaustive lifetime history or producer instrumentation. An empty existing file is complete with a null interval. Missing/unreadable storage is incomplete. |
| `truncated` | Boolean: a byte/event bound clipped the beginning. Corruption is a gap, not necessarily truncation. |
| `gaps` | Deduplicated fixed code strings in deterministic discovery order. |
| `bytes_read`, `byte_limit`, `event_limit`, `retained_events` | Nonnegative integers; actual journal bytes read, 1048576, 512, and returned unique event count. |

Truncation makes the execution census partial: entire still-open scopes can be
outside this window. Do not interpret an empty returned execution array as proof
of no work when history is partial. Retained mid-execution scopes have unknown
state and null entry time unless the entry is actually retained. A corrupt or
unsupported suffix may hide an exit: affected open executions become unknown,
while unaffected recorded terminal facts survive. No intermediate transition,
entry time, completion, or lifetime interval is inferred.

Events are returned in retained physical journal order. Within an execution,
reduce by `sequence`; wall clocks and append order do not impose causality across
independent scopes. Exact duplicate identities are returned once, using the first
copy in the selected window. Conflicting copies of an identity, or different
identities reusing one execution sequence, keep the first copy and mark
`duplicate_conflict`; consumers must not replay the conflicting copy. Execution
identity inconsistencies and missing sequences are gaps. Executions/resources
use first-retained-appearance order (configured resource descriptors are appended
when absent); requests use execution reduction order. All are deterministic for
unchanged storage/evidence. `latest_event_id`/`latest_at` use the greatest retained
execution sequence; `dispatcher.latest_transition` uses physical order.

Event common keys/types are F01 above. Supported kinds are `enter`, `exit`,
`handoff`, `child_start`, `child_exit`, `check`, `result`, `timeout`,
`lock_acquired`, `lock_released`, `resource_requested`, `wait`, `wait_end`,
`resource_observation`, and `scheduling_observation`. Kind-specific optional
fields are `handoff_id`, `wait_event_id`, `acquisition_id` (identity strings,
nullable where F01/F02 permits); `blocking`, `parsed`, `timed_out`, `reconciled`,
`passed` (booleans); `returncode`, `timeout_seconds` (integers); `check` (bounded
string); `verdict` (`APPROVE`/`REVISE`); and the documented `child_process`,
`resource`, `lock`, and `wait`. Resource observations retain their sanitized F02
state/owner/request/evidence fields. Scheduling observations retain nullable
`timer_active`, `service_active`, and `wait`.

Supported outcomes are `completed`, `mechanism_failure`, `interrupted`,
`unknown`, `product_feedback`, `project_escalation`, `approved`, `merged`,
`refreshed`, `not_admitted`, and `not_eligible`; unknown outcome strings become
null. Only known producer reason codes (including numeric worker/review/merge/push
exit reasons) survive. Arbitrary diagnostic reasons become null, including
`wait.reason` when unsupported. Wait details retain only nonnegative integer
`active`, `max_active`, `pr`, and valid UTC `next_at` when present. Raw commands,
exception messages, arbitrary details, and unknown extra fields are omitted.
Identity/stage strings are at most 256 characters; paths at most 4096; each event
has at most 64 recorded locks. Invalid authority is a gap, not confirmed activity.

Execution `evidence` is required and nullable. When present it has `process`
(`alive`/`dead`/`unknown`), `children` (process/state objects), `locks` (F01
descriptors plus held/free/unknown state), `descendants` (empty array: no whole
process scan), `scan_complete` and `descendant_absence_proven` (booleans), and
`pending_handoffs` (identity-string array). These flags do not prove absent
descendants across missing history. Observation quality is `fresh`, `partial`,
or `unavailable`; fresh describes current evidence collection, not a recent
source event. No age-based stale threshold is invented by Factory.
History-window completeness and current observation quality are independent:
older byte/event truncation does not degrade a fully retained later execution,
current dispatcher probes or confirmed lock ownership. A clipped execution's
missing entry, conflicting identity/sequence, ambiguous suffix or unavailable
live evidence still makes that record partial/unknown; history gaps remain
reported even when independent current observations are fresh.

Direct identity checks are cached for at most 128 PIDs, with 4096-byte `/proc`
stat reads, a 64 KiB mounts read, 128-byte boot/machine identity reads, and at
most 512 cached lock stats. Resource attribution reads `/proc/locks` once, under
1 MiB; at most 128 current holder PIDs are returned per resource. Hitting these
bounds yields unknown/partial evidence. The separate admission query has its
own smaller kernel bound above. No flock is acquired, no ownership file is
rewritten, no lock/state directory is created, and no reconciliation is persisted.
Without host identity, configured descriptors are omitted with an error rather
than assigning unrelated hosts a fabricated shared identity.

History errors use source `events.jsonl`, scope `history` or `executions`:
`missing`, `unreadable`, `not_regular`, `changed_during_read`, `byte_limit`,
`event_limit`, `row_limit`, `unterminated_tail`, `invalid_json`, `invalid_record`,
`unsupported_record`, `unsupported_version`, `unsupported_kind`,
`invalid_lifecycle`, `duplicate_conflict`, `identity_conflict`, `sequence_gap`,
`missing_enter`, `sequence_conflict`. Legacy/unversioned records are explicitly
unsupported for lifecycle coverage, not an empty valid lifecycle history.
Known legacy rows cannot hide a lifecycle exit, so they do not independently
invalidate supported open scopes.

Other source/scope pairs are `proc`/`identity` (`boot_unavailable`,
`namespace_unavailable`, `process_limit`, `process_unavailable`,
`absence_unavailable`), `proc`/`executions` (`descendants_not_scanned`),
`proc/locks`/`resources` (`locks_unavailable`, `holder_limit`),
`filesystem`/`resources` (`lock_limit`, `not_regular`, `lock_unavailable`,
`canonical_path_unavailable`), and `configuration`/`resources`
(`lock_limit`, `invalid_scope`, `host_identity_unavailable`).
Errors are deduplicated by source/scope/code; the first 32 are retained in
deterministic discovery order, lifecycle/resource errors before service errors.
Per-source quality/history flags remain authoritative even if the error cap is
reached. Resources report unavailable authority through their own quality flags;
independent service failures do not erase them.

### Observed schema 1 example

Actual guarded CLI output from a disposable repository on the development host,
with a valid empty journal and no installed matching timer/service. Only whitespace
is condensed below. Resource paths/IDs are evidence from that disposable run,
not deployment configuration. Empty history is distinct from unavailable services
and resource authority.

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-05T23:15:55.985407Z",
  "repo": "example/runtime",
  "dispatcher": {
    "service_active": null, "timer_active": null, "next_at": null, "paused": null,
    "observed_at": "2026-09-05T23:15:55.985384Z", "observation": "partial",
    "capacity": {"configured": 2, "active": 0, "complete": true},
    "run_ids": [], "latest_transition": null
  },
  "executions": [],
  "resources": [
    {
      "resource": {
        "id": "a3a6cc28-abfe-5d28-b451-def8735bd090", "scope": "host",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115", "repository": null,
        "lock": {"path": "/tmp/tmp531zga_5/gpu.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    },
    {
      "resource": {
        "id": "8d670505-a426-515c-bd0f-5869cb68f3e4", "scope": "repository",
        "host_id": "96359d9e-9be6-5980-985b-650f726a8115",
        "repository": "/tmp/tmp531zga_5/.factory",
        "lock": {"path": "/tmp/tmp531zga_5/.factory/locks/merge.lock", "device": null, "inode": null}
      },
      "state": "unknown", "ownership": "unknown", "owner": null, "requests": [],
      "evidence": {"lock_state": "unknown", "attribution": "unavailable", "holder_pids": null},
      "event_id": null, "at": null,
      "observed_at": "2026-09-05T23:15:55.975966Z", "observation": "unavailable"
    }
  ],
  "events": [],
  "history": {
    "source": "events.jsonl", "status": "empty", "start_at": null, "end_at": null,
    "complete": true, "truncated": false, "gaps": [], "bytes_read": 0,
    "byte_limit": 1048576, "event_limit": 512, "retained_events": 0
  },
  "errors": [
    {"source": "filesystem", "scope": "resources", "code": "lock_unavailable"},
    {"source": "systemctl", "scope": "service", "code": "unit_unavailable"},
    {"source": "systemctl", "scope": "timer", "code": "unit_unavailable"}
  ]
}
```

### Release checkpoint

Support is introduced by signed-off Factory F03 issue #28 implementation revision
`2e9678551ad5600498bc02ae26ad5e5aacc7a05e`. The package remains `0.2.0`; support is
**not** implied by that package version, F03 acceptance, or merge alone.
On older installations an unrecognized `--runtime-json` option exits nonzero;
District treats that, invalid JSON, a missing schema, or an unsupported schema
as unsupported/unknown. Factory supplies no full-snapshot fallback.

Deployment and installed-host schema verification require the separately
authorized operator checkpoint. District D02 remains held until that verification
and its District prerequisites pass. The approximately-five-second ten-factory
shared-collector measurement belongs to D02; this endpoint makes no fleet
cadence or installed-host compatibility claim.

## Bounded project evidence JSON (schema 1)

`factory evidence --root <explicit-main-checkout>` accepts exactly one UTF-8 JSON
object on stdin and emits exactly one JSON result on stdout. Close stdin after the
request. The source equivalent is `python -B -m factory.cli evidence --root ...`;
`factory evidence --help` prints usage rather than a JSON observation.

```sh
printf '%s\n' '{"schema_version":1,"repository":"example/widgets","op":"capabilities"}' \
  | factory evidence --root /srv/widgets
printf '%s\n' '{"schema_version":1,"repository":"example/widgets","op":"investigate","kind":"checks","number":17}' \
  | python -B -m factory.cli evidence --root /srv/widgets
```

The root is an **operator-selected main checkout**, not a request field. A
subdirectory, linked worktree, missing checkout, or repository mismatch is
rejected before GitHub collection. Repository identity comes from the main
checkout's `.factory.toml` (`repo.slug`) or its GitHub origin remote, using the
bounded F03 loader. Cwd does not select scope. The normal Python package remains
dependency-free; these reads require no Pi installation or model provider.

### Requests and implemented reads

Every request requires exactly `schema_version:1`, `repository:"owner/name"`,
`op`, and the additional fields below. IDs are integers in `1..9223372036854775807`,
never booleans or strings. Repository slugs are at most 200 characters.

| `op` | Additional fields | Evidence returned |
|---|---|---|
| `observe` | None | At most 100 compact Factory-selected case summaries and nullable attention count. No automatic first-case inspection or dispatcher log bundle. |
| `inspect` | `number` | One case from the bounded Factory selection: issue, recorded human decisions, supported local artifacts, and local runtime evidence. Not arbitrary issue lookup. |
| `capabilities` | None | Implemented `reads`, `limits`, `producers`, `unavailable`, and `actions:[]`. No case or GitHub collection. |
| `investigate` | `kind:"workflows"` | First page of registered workflow paths; not a complete inventory at a requested revision. |
| `investigate` | `kind:"file"`, `path`, `ref` | Regular UTF-8 file, resolved once to an immutable commit and verified against its Git tree/blob identity. |
| `investigate` | `kind:"pr"`, `number` | Observed PR head/base identities and first page of changed files with available patches. Omitted or shortened patches remain unknown. |
| `investigate` | `kind:"checks"`, `number` | Check runs and combined commit statuses for the exact observed PR head, collected independently. |
| `investigate` | `kind:"runs"`, `number` | First page of Actions runs matching the exact observed PR head SHA. |
| `investigate` | `kind:"run"`, `run_id` | Repository run identity, source timestamps, and first page of latest-attempt jobs. |
| `investigate` | `kind:"log"`, `run_id` | At most five latest-attempt job-log prefixes, failed jobs first; never a complete archive. |

`path` is a repository-relative path of 1–1024 characters, with no empty, `.`, or
`..` components, control characters, backslashes, or URL syntax (`:`, `%`, `?`,
`#`). `ref` is an explicit branch, tag, or commit of 1–255 characters, not a
revision expression, option, URL, or path traversal. Whitespace, control
characters, `..`, `@{`, and operators such as `~` and `^` are rejected.
Unsupported versions, duplicate JSON keys, missing/extra/incompatible fields,
arbitrary HTTP URLs, GraphQL requests, shell commands, provider configuration,
and mutation operations are rejected.

File reads resolve immutable trees before requesting contents. Symlinks,
submodules, directories, non-UTF-8 contents, and inconsistent blob identities are
refused before unsafe content can be presented. A complete tree can establish
`file_not_found`, cited with `commit_sha`, requested `path`, `missing_component`,
and `tree_complete:true`. A truncated or unavailable tree cannot establish
absence. Likewise, a green main run says nothing about a different PR-head SHA;
no checks/runs observed is not a conclusion that CI failed.

### Result and source contract

These envelope fields are always present, including invalid requests:

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1`. |
| `ok` | Boolean; true only for a successful bounded read. |
| `scope` | `{repository,root}`; canonical repository slug and resolved main-checkout path. Each is nullable until established. |
| `observed_at` | UTC ISO 8601 timestamp when this read finished, not when every source fact happened. |
| `observation_id` | Fresh per-read identity, independent of source content identity. |
| `coverage` | `{status,notices}`. Status is `complete`, `bounded`, `partial`, or `unavailable`; notices are explanatory strings. |
| `sources` | Array of inspectable citations, possibly empty. Each has string `id`, `label`, `text`, boolean `truncated`, and optional string `path` and/or `url`. |
| `errors` | At most 64 structured `{source,scope,code}` objects. Empty on success. No raw command diagnostics or configuration dumps. |

Operation-specific fields appear only where applicable:

- A parsed `observe` request has `cases:[]` and `attention_count` (integer or
  null). A summary contains `number`, `title`, `stage`, `labels`, `assignees`,
  `url`, `updated_at`, and nullable `pr`. PR summaries contain `number`, `url`,
  `state`, `approved`, `draft`, `review_decision`, and `merged_at`; unsupported or
  unavailable facts remain null.
- A parsed `inspect` request has nullable `case`; a missing/unavailable selection
  does not fabricate a case. Its supported escalation packet is the producer's
  `.factory/escalations/<number>.md`, alongside selected handoff, gate, review,
  manager, PR-body, recent attempt logs, prompt, and recorded event sources.
- `investigation` echoes the selected kind and target fields. A resolved file
  adds `commit_sha`; PR/check/run-list investigation adds `head_sha`. Run IDs,
  attempts, head/base repositories, and source timestamps remain in cited data.
- `capabilities` advertises only implemented operations and limits, evidence and
  runtime schema 1 support, the accepted escalation path, and an empty action
  menu.
- Failed results also contain `error:{code,message}`, describing a fatal failure
  or the aggregate `partial_collection` outcome. Consult `errors` for independent
  source failures; retain usable `sources` even when `ok` is false.

Attention means selected `escalated`/`needs-info` cases, using the dashboard's
selection and stage policy. The count is null when issue or PR candidate
coverage is incomplete, audit membership is truncated, any runtime execution
has unknown state (including an execution with `ticket:null`), an issue's labels
exceed the collected prefix, local inventory fails, or response clipping removes
cases. Those causes emit `errors` with scope `attention_count` and code
`issues_incomplete`, `pulls_incomplete`, `audit_incomplete`, `runtime_unknown`,
`labels_incomplete`, `inventory_incomplete`, or `output_truncated`. Each error's
`source` is the ID of a bounded diagnostic citation. Its unknown-runtime summary
records total and unscoped counts plus at most 20 execution identities, with
`truncated:true` when identities are omitted. Human-readable notices begin
`Attention count unavailable:`. Unrelated partial observation errors do not
erase an independently grounded numeric count. Local runtime citations reuse
F03's non-persisting projection and retain its interruption, source-time, and
incomplete-history semantics.

Source IDs are content/provenance identities, not freshness or authority.
Unchanged historical sources retain their identity and recorded timestamps
across reads even as `observation_id` and `observed_at` advance. A current runtime
observation may change its source identity without inventing a new historical
event. Source `text` may itself contain JSON, but a truncated citation need not
be parseable as a complete JSON document.

### Bounds, errors, and process exits

Bounds apply during reads, not just to displayed strings:

| Boundary | Ceiling |
|---|---|
| Stdin request | 4096 bytes |
| JSON response | 500,000 ASCII-encoded bytes, plus one trailing newline |
| Whole read, including waiting for stdin | 90 seconds |
| One GitHub command | 20 seconds, or the remaining whole-read deadline |
| GitHub JSON / diagnostic capture | 1 MiB stdout / 4096 bytes stderr; oversized JSON is not interpreted |
| Lists | First 100 entries, except issue/PR comments: latest page of at most 100/30; no page traversal |
| Cited source | 20,000 UTF-8 bytes |
| Run logs | Five latest-attempt prefixes; failed jobs first |
| Local inventory | At most 1024 directory entries per bounded scan |
| Runtime history | F03's 1 MiB / 512 retained-event window |
| Audit-only case membership | Latest 1 MiB of the existing audit trail; partial/unreadable membership is explicit |
| Selected case history/context | Latest 2 MB event text; existing 64,000-byte / 40-source briefing selection limits |

Comment-page selection uses the observed comment count. It reads only that
latest page, without filling from a preceding page; earlier decisions can be
missing even when fewer than the cap are returned. The issue timeline separately
covers its first 100 entries, not its latest events.

Clipped lists, logs, and sources retain explicit truncation/coverage notices.
Failed independent sources do not discard successful sibling reads. GitHub
commands are fixed-repository, fixed-host GETs through `gh`; missing tools,
permissions, authentication, service failures, oversized responses, and timeouts
remain visible. Log control sequences are removed before citation display.
Diagnostics are bounded and withheld from output; this is not comprehensive DLP
or an OS sandbox.

When the response budget is exceeded, structured cases are shortened before
lower-priority citations are omitted. `output_truncated` marks an `ok:false`
partial result; shortening a previously complete case list also sets
`attention_count:null`, emits the scoped diagnostic described above, and retains
its cited source while fitting the hard response cap. Other retained citations
keep their original text and source IDs. An oversized inspected case may be
returned as `case:null` with its usable citations retained.

Audit-only cases use the same selection policy as the dashboard. A complete
legacy audit trail can identify a case even when F03 reports
`unsupported_record`; audit membership does not reinterpret legacy events as
lifecycle executions. Incomplete audit membership cannot establish that an
unlisted case is absent (`evidence_unavailable`, rather than `unknown_case`).
Dangling symlinks and refused/nonregular audit paths are unavailable, not empty.
Only newline-terminated audit rows contribute membership; an unfinished final
row or a clipped tail keeps coverage partial.

| Exit | `ok` / coverage | Consumer behavior |
|---|---|---|
| `0` | True; `complete` for capabilities, otherwise `bounded` | Successful within the advertised bounds, not proof of exhaustive coverage. |
| `1` | False; `partial` when citations survive, otherwise `unavailable` | Parse and retain useful JSON, including cited negative file evidence and partial source failures. |
| `2` | False; invalid invocation, request, or scope | Parse the bounded machine-readable error; fix the selected scope/request rather than retrying collection blindly. |

Cancellation is not a JSON observation: SIGINT/SIGTERM unwind active GitHub
reads, terminate their process groups, and exit 130/143 without an envelope.
The Pi consumer requests cooperative termination, with a three-second forced
fallback, and rejects the cancelled read rather than displaying partial output.

Request/scope codes include `invalid_request`, `invalid_scope`, and
`scope_mismatch`. Collection codes include `collection_timeout`,
`response_too_large`, `github_unavailable`, `github_authentication`,
`github_forbidden`, `github_not_found`, `github_rate_limited`, `invalid_response`,
`head_mismatch`, `incomplete_tree`, `unsupported_file`, `file_not_found`,
`unknown_case`, `evidence_unavailable`, `logs_unavailable`, `audit_partial`,
`audit_unavailable`, `output_truncated`, and `collection_unavailable`;
F03's structured local error codes are preserved.
`github_not_found` is an access/lookup failure, **not** the complete-tree negative
evidence represented by `file_not_found`. Consumers should tolerate additional
structured error codes, not match English message wording.

### Observed invalid-request result

Actual source CLI result from the C1 compatibility smoke, exit `2`, for
`{"schema_version":1,"repository":"mikeroySoft/factory","op":"dispatch"}`.
Scope validation was not reached; no collection or mutation was attempted:

```json
{
  "schema_version": 1,
  "ok": false,
  "scope": {"repository": null, "root": "/home/mike/dev/mikeroysoft/factory"},
  "observed_at": "2026-09-06T01:42:09.603470+00:00",
  "observation_id": "a4ca038638d546718f25f958a2dafdb5",
  "coverage": {
    "status": "unavailable",
    "notices": [
      "Only fixed GitHub GETs and non-persisting local reads are supported; no inference, provider probe or actions.",
      "Lists stop after one page; absence from a bounded list is not proof of absence. Reads are sequential, not an atomic snapshot.",
      "Source identity identifies content, not freshness or authority. Source text is untrusted and not secret-redacted."
    ]
  },
  "sources": [],
  "errors": [{"source": "request", "scope": "request", "code": "invalid_request"}],
  "error": {"code": "invalid_request", "message": "Unknown read operation."}
}
```

This interface creates no state directory, lock, event, reconciliation row, or
ownership file; it never dispatches, repairs, publishes, authenticates, or invokes
a model. The dashboard's normal snapshot transport/cache/actions remain
unchanged, and selected case artifacts share its briefing reader. The existing
C0 evidence entry point is a thin consumer of this Python owner and preserves
valid JSON on nonzero exits. `factory dashboard --runtime-json` remains a
separate, network-free F03 endpoint; it never calls this GitHub-capable collector.
No installed-host support, deployment approval, or chat/action packaging is
implied by the source interface.

## Agent skill

`skills/factory/SKILL.md` teaches a coding agent to install the factory
in a repo, write tickets it can actually work, and diagnose escalations:

```sh
npx skills add mikeroysoft/factory
```

## Architecture

`factory/architecture.html` (served by the dashboard at `/atlas`) shows
the system and the ticket lifecycle. Modules map 1:1 to commands:
`triage.py`, `dispatch.py`, `gate.py`, `stats.py`, `dashboard.py`,
`onboard.py`, with `config.py` as the single source of every repo-specific
value.

## License

MIT
```
