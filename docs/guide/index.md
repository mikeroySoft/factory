# Factory architecture

Grounded in `origin/main` at `5484cc7b3c77f30a57e69e68e3b516aa997af371` (2026-10-02 19:34 PT).

This guide is for an operator running the factory and for an agent reading how it is organized. It does not replace `docs/index.html`.

## What this system is

Factory is a set of one-shot CLI passes over one GitHub repository. A pass reads labels, comments, and pull requests, plus the gitignored `.factory/` directory, then writes the same places. It does not keep a session between invocations.

The issue, pull request, and merge sequence is what those passes do when the evidence allows it. It is not a guarantee that every issue becomes a merged pull request.

## Spine

1. [State](state.md). GitHub labels, comments, and pull requests, plus gitignored `.factory/`.
2. [Dispatcher](dispatcher.md). One stateless pass. Safe to run again.
3. [Agents](agents.md). Worktrees, locks, worker, reviewer, manager, and what fails closed.
4. [Flow](flow.md). Issue to pull request to merged pull request, as a consequence of the above.

## Also read

Operator command detail already lives in `README.md`. Agent setup and diagnosis already live in `skills/factory/SKILL.md`. This guide points at those files. It does not restate them.

## Suggested static layout

Keep `docs/index.html` as the marketing page. Add this guide beside it, for example:

- `docs/guide/index.md`
- `docs/guide/state.md`
- `docs/guide/dispatcher.md`
- `docs/guide/agents.md`
- `docs/guide/flow.md`

A static site can render `docs/guide/` without replacing `docs/index.html`. Do not publish that change from this draft.
