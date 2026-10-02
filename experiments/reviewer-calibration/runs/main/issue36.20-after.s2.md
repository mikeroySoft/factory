# Review: #20 — when-rules, ci-fix/conflict profiles (head `ce5568e`)

## Spec coverage

| Criterion | Evidence | Status |
|---|---|---|
| `[workers.<label>]` table with optional `when`; array form still loads | `factory/config.py:288-298` handles dict-or-list per label; `tests/test_factory.py:147-165` covers both forms plus malformed entries | Met |
| Prompt lists labels with `when` | `factory/manage.py:199-201` emits `- <label>: <when or '(no when rule)'>` | Met |
| ROUTE/FIX only for listed labels | `factory/manage.py:54`, `:63-66`; `parse` now receives the filtered `workers` dict (`:213`) | Met |
| ROUTE chore without worker rejected and recorded | Falls through to HUMAN (`manage.py:71`); `record("manage", …)` precedes `apply` (`:219`); `tests/test_factory.py:652-676` | Met |
| FIX on red CI runs selected argv; re-gate, push only on PASS, re-review, APPROVE restores label | `factory/manage.py:104-131`; `tests/test_factory.py:678-734` asserts push/no-push, label gating, rebase case | Met |
| Default profiles in template | `factory/templates/factory.toml:32-47` — commented-out, human-applied | See Optional 1 |
| Out of scope: manager writes host config | No config writes in diff | Met |
| README updated | `README.md:88-102` | Met |

## Required fixes

None.

## Optional suggestions

1. **Profiles shipped as comments, not as active defaults** — `factory/templates/factory.toml:36-47`. Issue text says "Ship two default profiles in `templates/factory.toml`"; "New profiles remain host-config, human-applied" most naturally refers to profiles *beyond* these two. As committed, a fresh `factory init` has no `ci-fix` worker, so `FIX {"worker":"ci-fix"}` is rejected until a human copies the block into host config. The agent brief's wording ("these remain host-config that humans apply") supports the diff's reading, so this is an ambiguity, not a blocking defect. Confirm intent with the issue author; if active defaults were intended, note that enabling them in the template also requires an active `default` entry (`config.py:286`).

2. **`{prompt}` embedded inside a longer argv string** — `factory/templates/factory.toml:41`, `:45`. The only substitution behavior exercised in the diff is whole-element replacement (`tests/test_factory.py:156`: `"{cwd}"`, `"{prompt}"` as standalone args). `[INFERENCE]` Whether `Config.worker()` substitutes substrings inside `"Read the ticket prompt at {prompt}. …"` is not visible in this packet. If it is equality-based, the shipped `ci-fix`/`conflict` commands would hand the literal text `{prompt}` to `omp`. Worth a one-line check against `Config.worker()` before anyone copies the profile.

3. **Redundant reserved-label filtering** — `factory/manage.py:54`, `:65` subtract/check `RESERVED_LABELS` again even though `manage_pass` already passes a filtered dict (`:199`, `:213`). Harmless; `parse` is also a public function with its own callers in tests, so keeping the guard is defensible. No action needed.

4. **Behavior tightening for legacy arrays** — `factory/config.py:291` now rejects empty/non-string argv for array-form workers that previously loaded unchecked. Reasonable, and the exit gate's "existing array-form still loads" holds for well-formed arrays. Mention only so the stricter error is a known change.

## Abstractions

One net-new name: `RESERVED_LABELS` (`factory/manage.py:33`) replaces an inline set literal that is now used at three sites (`:54`, `:65`, `:199`). Justified by deduplication of a correctness-bearing set; no further abstraction introduced.

VERDICT: APPROVE
