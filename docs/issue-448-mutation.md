# #448 mutation evidence

The follow-up changes only add tests. Production behavior, the shipped
`eval_schedule = False` default, decided-ON declarations, live configuration,
mutation sampling, and deadlines stay unchanged. The two changelog entries
were already consolidated before this follow-up.

## Reported survivors

Isolated Cosmic Ray trials against `tests.test_watch_expectations` kill these
behavior-changing mutations:

| Site | Mutation | Consumer-visible regression |
| --- | --- | --- |
| `factory/config.py:335` | `value != "undecided"` → `value < "undecided"` | Doctor must reject `"yes"` rather than treating an invalid declaration as decided intent. |
| `factory/watch.py:70` | `start_from_journal = False` → `True` | An expectation without a matching event or explicit start must not borrow unrelated journal history. Once its event arrives, the age deadline applies. |
| `factory/watch.py:83` | `event == "lifecycle"` → `event is not "lifecycle"` | A diagnostic row with the same ticket, stage, and kind must not satisfy a missing lifecycle exit. |
| `factory/watch.py:178` | `signals[-1]` → `signals[not 1]` | Each overdue ticket's signal and issue body must retain only its own queue-entry evidence. |
| `factory/watch.py:1056` | filing identity `==` → `>=` | A closed newer filing must not suppress evidence on an older open filing with the same fingerprint. The comment and journal row must name the replacement. |

Two other sampled mutations at line 307 change the selected split element
from `[0]` to `[-1]` or `[1]`. The new inspection regression executes the
reported command in a fixture repository and requires it to find the live
`[eval]` table alongside the intended declaration, without editing either
file. Both mutations fail this behavioral test; it does not pin shell spelling.

### Equivalent survivor

`factory/watch.py:307`, `core/NumberReplacer`:

```python
key.split(".", 1)[0]
# becomes
key.split(".", 2)[0]
```

Both expressions return the prefix before the first dot, or the whole key
when there is no dot. Increasing a positive split limit cannot change element
zero. This holds for every string, not just the two currently supported switch
names. There is no consumer-visible difference to assert. An isolated trial
confirms survival; production code is not rewritten just to remove this mutant.

## Verification

- `uv run python -m unittest discover -s tests`: 1,211 tests, four skipped, PASS.
- `uv run --frozen python -m unittest tests.test_watch_expectations tests.test_watch`:
  82 tests, PASS.
- `uvx ruff@0.16.10 check .`: PASS after correcting the new test's nested-context
  lint error.
- The preserved undecided-switch regression fails at its test-only commit and
  passes at HEAD when selected through `dispatch.gate_flags`.
  The changed-live-state test is not used as the reproduction ID because its
  assertions changed after its original test-only commit.

The complete configured blocking trial uses the unchanged seed, cap, workers,
and deadline:

```sh
uv run --frozen --no-managed-python --group mutation python scripts/mutation.py --blocking
```

The full-sample result is recorded in the worker handoff after that trial
finishes. The isolated trials above are diagnostic evidence, not a substitute
for the blocking check.
