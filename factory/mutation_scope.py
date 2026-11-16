"""The one applicability rule for `proof = "mutation-v1"` mutation checks.

`scripts/mutation.py` (the producer) mutates exactly these lines and records the scope in
its completion record; `factory/gate.py` (the reader) recomputes it with this same code
and refuses any record whose scope differs. The rule is the runner's historical diff:

    git diff --unified=0 --no-renames --diff-filter=AM <base>...HEAD -- 'factory/*.py'

i.e. destination lines added or modified in added/modified `factory/*.py` files, with
renames split into delete + add. Both refs are resolved to commits first and the diff
runs between `git merge-base <base> <head>` and `<head>` (what `A...B` means to git
diff), so the record names commits, never labels. A file whose hunks only delete lines
contributes no lines and is dropped, so a deletion-only change has an empty scope.

The fingerprint names committed blobs, so in proof mode both sides also require every
tracked path of the checkout (and of each prepared runner copy) to hold exactly the
pinned head's bytes (`tracked_mismatches`): a run never certifies bytes it did not use.
A path the pinned head does not list cannot be checked that way, so the runner removes
every such path from its disposable prepared copies (`tracked_paths`), never from the
checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

RULE = "factory-py-am-u0-v1"
PATHSPEC = "factory/*.py"
DEFAULT_BASE = "origin/main"
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_INDEX = re.compile(r"index [0-9a-f]+\.\.([0-9a-f]+)(?: \d+)?")
_HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class ScopeError(Exception):
    """The scope cannot be determined; callers fail closed."""


@dataclass(frozen=True)
class Scope:
    base: str  # commit the caller's base ref resolved to
    merge_base: str  # left side of the diff: merge-base(base, head)
    head: str
    files: tuple[tuple[str, str | None, tuple[int, ...]], ...]  # (path, head blob, lines), by path

    @property
    def lines(self) -> dict[str, frozenset[int]]:
        return {path: frozenset(lines) for path, _, lines in self.files}

    @property
    def line_count(self) -> int:
        return sum(len(lines) for _, _, lines in self.files)

    @property
    def fingerprint(self) -> str:
        """sha256 of the canonical JSON `{"rule", "files": [[path, blob, [lines]], ...]}`."""
        canonical = json.dumps(
            {"rule": RULE, "files": [[path, blob, list(lines)] for path, blob, lines in self.files]},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def receipt(self) -> dict:
        """The `scope` object of a completion record; the gate compares it field by field."""
        return {"rule": RULE, "base": self.base, "merge_base": self.merge_base, "head": self.head,
                "fingerprint": self.fingerprint, "lines": self.line_count}


def _git(cwd: Path | None, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        raise ScopeError(f"git unavailable: {exc}") from exc
    if proc.returncode:
        raise ScopeError(f"`git {' '.join(args)}` failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode("utf-8")  # Git counts LF only; universal-newline decoding loses CR bytes.


def resolve(ref: str, cwd: Path | None = None) -> str:
    """The commit `ref` names now; a label is never accepted as identity on its own."""
    if not ref or ref.startswith("-"):
        raise ScopeError(f"invalid ref {ref!r}")
    oid = _git(cwd, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    if not _OID.fullmatch(oid):
        raise ScopeError(f"{ref!r} did not resolve to a commit")
    return oid


def diff_command(*revs: str) -> list[str]:
    """The rule's one `git diff` invocation over `revs` (`A...B`, or `<merge-base> <head>`)."""
    return ["git", "diff", "--unified=0", "--no-renames", "--diff-filter=AM", "--full-index",
            "--no-color", "--text", "--no-textconv", "--no-ext-diff",
            "--src-prefix=a/", "--dst-prefix=b/", *revs, "--", PATHSPEC]


def parse(diff: str) -> tuple[tuple[str, str | None, tuple[int, ...]], ...]:
    """`(path, head blob, sorted destination lines)` per file with at least one line.

    The blob comes from the `--full-index` header git writes for every file with hunks;
    it is None only for input without one (never from `compute`, whose git output has it)."""
    files: dict[str, tuple[str | None, set[int]]] = {}
    path = blob = None
    old_remaining = new_remaining = 0
    for line in diff.removesuffix("\n").split("\n"):
        if old_remaining or new_remaining:
            if line.startswith("+"):
                new_remaining -= 1
            elif line.startswith("-"):
                old_remaining -= 1
            elif line.startswith(" "):
                old_remaining -= 1
                new_remaining -= 1
            elif line.startswith("\\"):
                continue
            else:
                raise ScopeError(f"unparseable hunk line: {line!r}")
            if old_remaining < 0 or new_remaining < 0:
                raise ScopeError(f"unparseable hunk line: {line!r}")
            continue
        if line.startswith("diff --git "):
            path = blob = None
        elif match := _INDEX.fullmatch(line):
            blob = match.group(1)
        elif line.startswith("+++ "):
            if not line.startswith("+++ b/") or (blob is not None and not _OID.fullmatch(blob)):
                raise ScopeError(f"unparseable diff header: {line!r}")
            path = line[6:]
            files[path] = (blob, set())
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if path is None or match is None:
                raise ScopeError(f"unparseable hunk: {line!r}")
            old_remaining = int(match.group(1) or 1)
            start, new_remaining = int(match.group(2)), int(match.group(3) or 1)
            files[path][1].update(range(start, start + new_remaining))
    return tuple((p, b, tuple(sorted(lines))) for p, (b, lines) in sorted(files.items()) if lines)


def _blob_id(data: bytes, oid: str) -> str:
    """Git's blob id for `data`, in the hash algorithm `oid` was written with."""
    digest = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    return digest(b"blob %d\0" % len(data) + data).hexdigest()


def _tree(head: str, cwd: Path | None) -> list[tuple[str, str, str]]:
    """`(mode, oid, path)` for every path in commit `head`'s tree (git ls-tree -r)."""
    try:
        listing = _git(cwd, "ls-tree", "-r", "-z", "--full-tree", head)
    except UnicodeDecodeError as exc:
        raise ScopeError(f"undecodable tracked path in {head}: {exc}") from exc
    entries = []
    for entry in filter(None, listing.split("\0")):
        meta, _, path = entry.partition("\t")
        mode, _, oid = meta.split(" ")
        entries.append((mode, oid, path))
    return entries


def tracked_paths(head: str, cwd: Path | None = None) -> frozenset[str]:
    """Every path commit `head` lists (files, symlinks, gitlinks); never the index."""
    return frozenset(path for _, _, path in _tree(head, cwd))


def linked_ancestors(paths, directory: Path) -> list[str]:
    """The parent directories of `paths` (relative, posix) that are symlinks under `directory`.

    Shallow-first, with lstat only: an ancestor is probed only once its own parent is
    known to be a real directory under `directory` (never anything above it), so nothing
    at or below a symlinked, missing or non-directory ancestor is followed or probed. A
    missing or non-directory ancestor is not reported here (reading below it fails anyway).
    A path's own final component is never probed: committed symlink leaves are the
    callers' business."""
    ancestors = {parent.as_posix() for path in paths for parent in PurePosixPath(path).parents} - {"."}
    linked, real = [], {"."}
    for rel in sorted(ancestors, key=lambda rel: (rel.count("/"), rel)):
        if PurePosixPath(rel).parent.as_posix() not in real:
            continue
        try:
            mode = os.lstat(Path(directory) / rel).st_mode
        except OSError:
            continue
        if stat.S_ISLNK(mode):
            linked.append(rel)
        elif stat.S_ISDIR(mode):
            real.add(rel)
    return linked


def tracked_mismatches(head: str, directory: Path, cwd: Path | None = None) -> list[str]:
    """Paths tracked in commit `head` whose bytes under `directory` are not that commit's.

    Every tracked path counts (sources, tests, the runner, config), compared byte for byte
    with no index, stat cache or filter involved: a regular file must hold the blob, a
    symlink must point where the blob says, and a missing or retyped path or a gitlink
    (submodule bytes cannot be verified here) is a mismatch. A path below a symlinked
    ancestor directory (`linked_ancestors`) is a mismatch without being read through it.
    Untracked files are not inspected (the runner excludes them from its prepared copies
    instead). [] means every tracked execution input under `directory` is `head`'s."""
    mismatched = []
    entries = _tree(head, cwd)
    linked = linked_ancestors((path for _, _, path in entries), directory)
    for mode, oid, path in entries:
        if any(path.startswith(f"{ancestor}/") for ancestor in linked):
            mismatched.append(path)  # never read through a symlinked ancestor
            continue
        target = Path(directory) / path
        try:
            if mode == "120000":
                same = target.is_symlink() and _blob_id(os.fsencode(os.readlink(target)), oid) == oid
            elif mode in {"100644", "100755"}:
                same = not target.is_symlink() and target.is_file() and _blob_id(target.read_bytes(), oid) == oid
            else:
                same = False
        except OSError:
            same = False
        if not same:
            mismatched.append(path)
    return mismatched


def compute(base_ref: str = DEFAULT_BASE, cwd: Path | None = None) -> Scope:
    """The scope of HEAD against `base_ref` (a label or a commit) in the repository at `cwd`."""
    base = resolve(base_ref, cwd)
    head = resolve("HEAD", cwd)
    merge_base = _git(cwd, "merge-base", base, head).strip()
    if not _OID.fullmatch(merge_base):
        raise ScopeError(f"no merge-base between {base} and {head}")
    files = parse(_git(cwd, *diff_command(merge_base, head)[1:]))
    if any(blob is None for _, blob, _ in files):
        raise ScopeError("git diff omitted a --full-index blob")
    return Scope(base, merge_base, head, files)
