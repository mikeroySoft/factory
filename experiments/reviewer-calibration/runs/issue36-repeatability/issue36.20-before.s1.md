## Standards

README.md is the only convention document at this head; the diff updates it consistently with the new behavior (README.md:88-102). No documented rule is contravened.

## Spec (issue #20 / agent brief)

| Criterion | Evidence | Status |
|---|---|---|
| `when` on `[workers.<label>]`, array form still loads | factory/config.py:288-298 handles dict or list entries; tests/test_factory.py:147-165 covers both forms and rejects malformed tables | Met |
| Prompt lists labels with `when` | factory/manage.py:199-201 | Met |
| ROUTE/FIX only for listed labels; rejection recorded | factory/manage.py:53-54, 63-66; unparsed → HUMAN, `record("manage", …)` at manage.py:219 precedes `apply`; tests/test_factory.py:652-676, 719-729 | Met |
| FIX on red CI runs selected argv, re-gates, pushes on PASS, re-reviews, approves only on fresh APPROVE | factory/manage.py:104-131; tests/test_factory.py:678-717 | Met |
| `ci-fix` / `conflict` profiles in template, human-applied | factory/templates/factory.toml:31-47 (commented, opt-in) | Met |
| dashboard exposes `worker_when` | factory/dashboard.py:847 | Met |

## Required fixes

None.

## Optional suggestions

1. factory/templates/factory.toml:37-38 directs users to place table-form profiles under `[defaults.workers.<label>]` / `[repo."owner/name".workers.<label>]` in host config. The host-layer merge is outside this diff; whether it carries a sub-table through to the loop at factory/config.py:289 is `[INFERENCE]` only. Worth a one-line test loading a table-form worker from the host layer, since the template now advertises that path.
2. factory/config.py:290-291: unknown keys in a worker table (e.g. a typo of `when`) are silently ignored. A `ConfigError` on unexpected keys would catch misconfiguration early. Not required by any acceptance criterion.
3. factory/manage.py:107-109: `reviewDecision == "CHANGES_REQUESTED"` is checked, but `pr["state"] != "OPEN"` and a missing PR both surface as `ValueError`/`CalledProcessError` → `mechanism_failure` (manage.py:224-226). Acceptable, but the escalation reason text will not say *why* FIX was refused; consider including the PR state in the message.

No net-new abstractions beyond the brief; `RESERVED_LABELS` (manage.py:33) replaces a duplicated literal set and is used in three places.

VERDICT: APPROVE
