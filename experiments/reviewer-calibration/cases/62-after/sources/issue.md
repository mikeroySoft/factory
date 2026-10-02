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
