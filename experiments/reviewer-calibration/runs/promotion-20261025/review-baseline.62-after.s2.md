## Standards

No `AGENTS.md`/`CONTRIBUTING.md`/`docs/` coding standard exists in the snapshot; the only documented conventions are README (CHANGELOG referenced at `README.md:50`) and the issue template. Baseline-smell pass only.

**Required:** none.

**Optional**
- *Duplicated Code (judgement call):* the `omp` + bare `{prompt}` check appears twice — `factory/config.py:84-85` (diff lines 84-85) and `factory/onboard.py` (diff lines 188-189). Doctor could call `cfg.manager_cmd(Path("x"), Path("y"))` inside `try/except ConfigError` and reuse the message. Two sites with the same predicate will drift if the rule changes (e.g. `-f {prompt}` for droid). Not blocking: both sites currently agree and are covered by tests (diff lines 278-280, 305).
- *Side effect on lifecycle outcome:* `dispatch.record("escalate", …, reason="manager_failed")` (diff lines 159-160) runs inside the `manage` lifecycle scope; `dispatch.record` (snapshot `factory/dispatch.py:245-248`) therefore stamps the manage execution's exit row with `outcome: project_escalation, reason: manager_failed`, where a failed manager command previously left `completed`. Lifecycle maps this to state `completed` either way, so nothing breaks; just note that the manage execution now reports itself as an escalation.

## Spec (issue #62 + agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `manager_cmd(prompt_path, cwd)` expands `{prompt}` with a path; prompt written to `<factory>/manager-prompt-<n>.md` | `factory/config.py` diff 81-86; `factory/manage.py` diff 149-151 | ✓ (file write lives in caller, not `manager_cmd`; exit-gate test asserts argv `@<path>` with readable content, diff 275-277) |
| Default manager command is worker shape (`@{prompt}`) | `factory/templates/factory.toml` diff 251-253; `README.md` diff 65-66 | ✓ (no `DEFAULT_MANAGER` constant exists in base; `manager` defaults to unset) |
| Bare `{prompt}` for `omp` → `ConfigError` naming `@{prompt}`; other programs not second-guessed | `factory/config.py` diff 84-85; test diff 278-280, 307 | ✓ |
| `doctor` row: PASS / FAIL naming missing placeholder or executable / WARN when unset | `factory/onboard.py` diff 180-192; `report(None,…)` → WARN per snapshot `onboard.py:168`; tests diff 298-324 | ✓ |
| Nonzero exit posts `Manager command exited N (argv: …unexpanded…)` + last 5 stderr lines | `factory/manage.py` diff 154-158; test diff 369-372 | ✓ (`json.dumps(cfg.manager)` is the unexpanded argv) |
| Records `escalate` event with `reason: manager_failed`, countable by `factory stats` | `factory/manage.py` diff 159-160; `factory/stats.py` diff 211-212, 220; test diff 373-379 | ✓ |
| README §Configuration + init template document file-for-workers-and-manager, text-for-reviewer | `README.md` diff 38-47; template diff 232, 241, 251-252 | ✓ |
| Unit tests pass | Gate report `test: PASS` | ✓ |
| Manual District #26 replay | Not gateable; handoff reports a prior-attempt replay yielding `DECISION: FIX`, not repeated this revision | unverified (manual) |

Out-of-scope guard: decision semantics, packet format, model unchanged — no diff hunks touch `parse`/menu/packet layout.

**Required:** none.

**Optional**
- `factory/manage.py` diff 155: only stderr is tailed. When a CLI prints its error to stdout and exits nonzero (previous code fell back to `proc.stderr or proc.stdout`), the body is just the one-line cause with no tail. Matches the spec text literally ("last 5 stderr lines"), so not blocking; consider `(proc.stderr or proc.stdout)` for the tail if that case matters.

## Plan drift

Plan status: not recorded — no plan file to diff against. Against the issue's "Touches" list (`config.py`, `manage.py`, doctor, template, README, tests):

- `factory/learn.py` (diff 104-128): required — sole other caller of `manager_cmd`; would otherwise break on the signature change.
- `factory/dispatch.py` (diff 97-101), `factory/stats.py` (diff 204-220), `manage.py` escalation lookup (diff 139-140): required consequence of emitting `escalate` rows with `reason: manager_failed`; without them the new event would inflate round numbers (`escalation_packet`), make `manage_pass` select the failure as the current escalation, and double-count escalations. Handoff comment on the issue attributes these to the round-1 reviewer's required fixes.
- `CHANGELOG.md` (diff 8-10): consistent with `README.md:50` pointing releases at the changelog.

No drift exposes a defect. Proof missing from the gate: only the manual District #26 replay, which is explicitly a manual exit-gate item and cannot be gated.

VERDICT: APPROVE
