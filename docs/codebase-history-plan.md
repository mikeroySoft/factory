# Codebase history

## Goal and boundaries

A read-only project view answering how the codebase is organized and how that organization evolved. Git is the historical authority; Graphify supplies structural evidence. No attention scoring, agent-generated architecture claims, PR previews, pipeline/gate changes, remote publication, or changes to the existing console programme holds.

The implementation preserves the existing `/atlas` architecture diagrams and adds `/codebase`. The current checkout has unrelated uncommitted work and is behind its remote-tracking branch; neither is modified or reset.

## Plan

1. Extract commit-pinned snapshots from Git objects using pinned `graphifyy==0.9.56`, declared in an optional `atlas` extra. Never execute historical source or check it out over the working tree. Store generated data beneath `.factory/codebase/`, not in tracked source.
2. Walk first-parent history in chronological order. Backfill the latest 80 commits by default, expose the history limit, and cache snapshots by commit and extractor version. Track Git-detected file renames independently of Graphify's path-derived symbol IDs. Store measured file sizes, symbols, directed file relationships, confidence, and revision-specific citations. Disclose excluded/unsupported files and incomplete extraction rather than inventing relationships.
3. Present a stable, branded flat map with folder grouping and file/symbol inspection. Keep positions and selection stable while scrubbing. Support keyboard-operable timeline and previous/next controls, selectable baseline comparison, addition/modification/move/deletion markers, and source/commit links pinned to the chosen SHA. No force-layout rearrangement on timeline movement.
4. Integrate an asynchronous local-history monitor with the dashboard, plus `factory codebase` for explicit backfill. Observe the locally available default-branch ref every 30 seconds and cache completed histories atomically. The dispatcher or an operator's normal fetch advances remote-tracking refs; this view never fetches, pushes, switches branches, or mutates GitHub. Clearly label the observed ref and freshness. Retain the last good history on update errors.
5. Verify with actual Factory history, then a disposable Git repository exercising rename, dependency changes, deletion, comparison boundaries, and automatic refresh. Exercise desktop/mobile browser rendering, timeline, baseline, file selection, source citations, loading/error states, and keyboard controls.

## Ownership and data contract

- `factory/codebase.py`: Git extraction, Graphify adapter, cache, command entry point. `build_history(root, ref, repo, cache_dir, limit=80)` returns the complete display dataset and atomically writes `history.json` beneath the cache directory. `default_ref(root, main)` selects an existing remote-tracking default branch, otherwise the local default branch. No network access.
- `factory/codebase.html`: standalone dashboard page, consuming `GET /api/codebase`.
- Dashboard integration, CLI registration, optional dependency, documentation and verification belong to the integration owner.

`GET /api/codebase` returns `{status: "building"|"ready"|"error", error: string|null, data: History|null}`. Existing data remains available while updating or after failure.

History schema 1:

- `schema`, `extractor`, `repo` (GitHub owner/name), `ref`, `tip`, `generated_at` (ISO UTC), `truncated` (earlier commits omitted).
- `snapshots`: oldest first, each `{sha, date, subject, parents, files, edges, warnings, unmapped}`. `unmapped` records stable identities of tracked paths excluded from the map; crossing an extraction boundary is a coverage change, not a file deletion.
- Each file: `{id, path, blob, lines, group, symbols: [{name, line}]}`. IDs survive detected renames. Group is the stable original directory grouping; current path remains explicit. Files are present only in revisions where they exist.
- Each edge: `{source, target, relation, confidence, path, line}`. Endpoints are file IDs; self-edges omitted. Evidence path and line are pinned to that snapshot. Only real resolved internal relationships; unresolved/excluded coverage is disclosed.
- `slots`: append-stable ordering `{id, group, order}` across snapshots. The page uses the union of slots, never independently relayouts a selected revision.

## Acceptance criteria

- Real history can be generated from an explicit ref without touching worktree files, local branches, or remotes.
- Cache reuse avoids re-extraction for unchanged commit/extractor pairs; a missing or failed extractor reports an actionable error rather than a fabricated map.
- Scrubbing changes observed revision and map contents, not node geography; a selected renamed file keeps its identity and correct historical source link.
- Comparison is meaningful across additions, equal-size edits, moves, dependency changes, and deletions, with non-colour status labels.
- A new commit reaching the observed local ref appears automatically without restarting the dashboard. Browsing an older revision does not jump to latest on refresh.
- Incomplete/shallow/bounded history and extraction coverage are visible. Missing history and update errors do not masquerade as an empty or current codebase.
- New UI is responsive, keyboard usable, escaped against untrusted repository strings, and visually checked in the browser.

## Evidence before implementation

Graphify 0.9.56 was loaded in an isolated uv environment and its real extraction API was exercised against `factory/cli.py` and `factory/config.py`. It returns path/line provenance, symbols, relationship kinds and confidence labels. Factory has zero core dependencies and an existing independent `/atlas` static page. No language server is configured in this checkout.

## Implementation and verification

- Implemented the optional extractor, CLI backfill, asynchronous dashboard monitor and `/codebase` viewer. Existing `/atlas` and console programme holds are unchanged.
- Real backfill: 47 first-parent commits through `4ebc8177391df331e3c8b2bdecf83dd7ba544df0`, 69 current files, 71 historical file slots and 247 resolved cross-file relationships. The local ref advanced during implementation; no fetch or branch change was issued by this feature.
- Browser geometry checks traversed all 47 revisions, with and without a baseline. Comparison badges initially stretched file rows; fixed-height slots and reserved timeline space eliminated that shift.
- A real disposable Git repository exercised same-size edits, a rename, deletion and new commits. Background refresh advanced the timeline from three to five commits while retaining the second revision, its baseline and selected renamed file. No page reload or server restart was needed for those updates.
- Removing only the disposable ref produced a visible update error while retaining the selected map. A dependency-free dashboard displayed actionable installation guidance instead of a fake or empty map.
- A regression scenario proved that a file crossing the extraction size limit, then moving into a vendored directory, retains its identity as unmapped rather than deleted. The browser distinguishes that coverage change from an actual deletion in the same comparison.
- Desktop and 390px mobile views were visually inspected; keyboard revision navigation, pointer input, pinned source links, non-colour comparison markers and literal rendering of a hostile HTML-like commit subject were exercised.
- `uv run --extra atlas --locked python -m unittest discover -s tests`: 33 passed. `python3 -m unittest discover -s tests`: passed, with the three optional Graphify scenarios skipped. The optional dependency is installed in CI so those scenarios run there.
- The wheel builds with the codebase page and extractor included. README documents installation, commands, cache location, first-parent/local-ref semantics, polling and coverage boundaries.
- Ruff is clean for the new extractor and regression tests. Dashboard lint still reports existing style findings and the deliberately broad background-error boundary; no suppressions were added.

The evidence above came from a preview implementation that made no commit, push, publication, installed-service restart, or unrelated working-tree cleanup. Issue #67 now authorizes the normal Factory commit, PR, CI, review, and merge path; the installed deployment remains the linked District #44 rollout from a clean pinned merged snapshot, never this checkout.

## Current-main integration

- Reconciled the prepared contribution with current main, preserving the evidence CLI, bounded runtime JSON mode, dashboard request protections, and existing console navigation.
- The dependency-enabled normal Factory gate passed; the integrated CLI built a three-commit real-history window and the 0.3.0 wheel built successfully.
- The integrated browser traversed all 49 revisions through `29d21d625bd92b9f3aa6d8ac0c8da1386233f284`: all 73 historical slots retained identical geometry. Baseline comparison and real pointer selection produced revision-pinned config.py source/symbol links and relationship evidence. A 390px viewport had no horizontal overflow.
- Independent pipeline review, GitHub CI, merge acceptance, and installed rollout evidence belong to Factory #67 and District #44; the local preview is not deployment evidence.
