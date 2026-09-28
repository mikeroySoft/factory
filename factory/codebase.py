"""Commit-pinned codebase history extracted from local Git objects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any

from factory import config

SCHEMA = 1
GRAPHIFY_VERSION = "0.9.56"
EXTRACTOR = f"graphifyy=={GRAPHIFY_VERSION}"
MAX_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_SNAPSHOT_FILES = 5000
EXCLUDED_DIRS = frozenset(
    {
        ".cache",
        ".factory",
        ".git",
        ".gradle",
        ".mypy_cache",
        ".next",
        ".nox",
        ".nuxt",
        ".pytest_cache",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "deps",
        "dist",
        "env",
        "external",
        "graphify-out",
        "node_modules",
        "out",
        "target",
        "third-party",
        "third_party",
        "vendor",
        "venv",
    }
)
# Graphify runs host `cpp` for these suffixes. Keep them visible, but never hand
# historical, repository-controlled directives to that preprocessor.
PREPROCESSED_FORTRAN = frozenset({".F", ".F90", ".F95", ".F03", ".F08"})
SHA_RE = re.compile(rb"[0-9a-f]{40,64}")


def _git(
    root: Path,
    *args: str,
    input_data: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_data,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"git {' '.join(args)} failed: {exc}") from exc
    if check and proc.returncode:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail or f'exit {proc.returncode}'}")
    return proc


def default_ref(root: Path, main: str) -> str:
    """Prefer the locally available origin default branch, then its local branch."""
    root = Path(root).resolve()
    for full, short in (
        (f"refs/remotes/origin/{main}", f"origin/{main}"),
        (f"refs/heads/{main}", main),
    ):
        if _git(root, "show-ref", "--verify", "--quiet", full, check=False).returncode == 0:
            return short
    raise RuntimeError(
        f"no local default-branch ref found for {main!r}; fetch origin/{main} or create {main}"
    )


def _load_graphify():
    hint = (
        f"install `graphifyy=={GRAPHIFY_VERSION}` in Factory's Python environment "
        "or run `uv sync --extra atlas` from the Factory source checkout"
    )
    try:
        version = metadata.version("graphifyy")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(f"Graphify {GRAPHIFY_VERSION} is unavailable; {hint}") from exc
    if version != GRAPHIFY_VERSION:
        raise RuntimeError(
            f"Graphify {GRAPHIFY_VERSION} is required, but {version} is installed; {hint}"
        )
    try:
        from graphify.extract import extract
    except Exception as exc:
        raise RuntimeError(
            f"Graphify {GRAPHIFY_VERSION} could not be loaded: {exc}; {hint}"
        ) from exc
    return extract


def _resolve_tip(root: Path, ref: str) -> str:
    raw = _git(
        root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"
    ).stdout.strip()
    if not SHA_RE.fullmatch(raw):
        raise RuntimeError(f"Git returned an invalid object id for {ref!r}")
    return raw.decode("ascii")


def _commit_rows(root: Path, tip: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
    raw = _git(
        root,
        "log",
        "--first-parent",
        f"--max-count={limit + 1}",
        "--format=%H%x00%cI%x00%s%x00%P",
        tip,
    ).stdout
    newest: list[dict[str, Any]] = []
    for row in raw.split(b"\n"):
        if not row:
            continue
        fields = row.split(b"\0")
        if len(fields) != 4 or not SHA_RE.fullmatch(fields[0]):
            raise RuntimeError("could not parse local Git history")
        newest.append(
            {
                "sha": fields[0].decode("ascii"),
                "date": fields[1].decode("utf-8", errors="replace"),
                "subject": fields[2].decode("utf-8", errors="replace"),
                "parents": fields[3].decode("ascii").split(),
            }
        )
    bounded = len(newest) > limit
    return list(reversed(newest[:limit])), bounded


def _decode_path(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    path = PurePosixPath(text)
    normalized = path.as_posix()
    if (
        not text
        or path.is_absolute()
        or normalized != text
        or any(part in ("", ".", "..") for part in path.parts)
        or "\\" in text
        or any(ord(char) < 32 for char in text)
    ):
        return None
    return normalized


def _included_path(path: str) -> bool:
    return not any(part.lower() in EXCLUDED_DIRS for part in PurePosixPath(path).parts[:-1])


def _examples(paths: list[str], limit: int = 4) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (f" (+{len(paths) - limit} more)" if len(paths) > limit else "")


def _inventory(root: Path, sha: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    raw = _git(root, "ls-tree", "-r", "-z", "-l", "--full-tree", sha).stdout
    entries: list[dict[str, Any]] = []
    unsafe = 0
    nonregular: list[str] = []
    excluded: list[str] = []
    oversized: list[str] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            head, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size_raw = head.split(b" ", 3)
        except ValueError as exc:
            raise RuntimeError(f"could not parse tree for {sha[:12]}") from exc
        path = _decode_path(raw_path)
        if path is None:
            unsafe += 1
            continue
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            nonregular.append(path)
            continue
        if not _included_path(path):
            excluded.append(path)
            continue
        try:
            size = int(size_raw)
        except ValueError as exc:
            raise RuntimeError(f"invalid blob size for {path} at {sha[:12]}") from exc
        if size > MAX_FILE_BYTES:
            oversized.append(path)
            continue
        entries.append({"path": path, "oid": oid.decode("ascii"), "size": size})

    entries.sort(key=lambda item: item["path"])
    kept: list[dict[str, Any]] = []
    total = 0
    capped: list[str] = []
    file_capped: list[str] = []
    for entry in entries:
        if len(kept) >= MAX_SNAPSHOT_FILES:
            file_capped.append(entry["path"])
        elif total + entry["size"] > MAX_SNAPSHOT_BYTES:
            capped.append(entry["path"])
        else:
            total += entry["size"]
            kept.append(entry)

    warnings: list[str] = []
    if unsafe:
        warnings.append(
            f"Excluded {unsafe} tracked path(s) that are not safe portable UTF-8 paths."
        )
    if nonregular:
        warnings.append(
            f"Excluded {len(nonregular)} symlink, submodule, or non-regular tracked entry(s): "
            f"{_examples(nonregular)}."
        )
    if excluded:
        warnings.append(
            f"Excluded {len(excluded)} file(s) under vendored or runtime directories: "
            f"{_examples(excluded)}."
        )
    if oversized:
        warnings.append(
            f"Excluded {len(oversized)} file(s) larger than "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MiB: {_examples(oversized)}."
        )
    if file_capped:
        warnings.append(
            f"Excluded {len(file_capped)} file(s) after the {MAX_SNAPSHOT_FILES}-file "
            f"snapshot limit: {_examples(file_capped)}."
        )
    if capped:
        warnings.append(
            f"Excluded {len(capped)} file(s) after the "
            f"{MAX_SNAPSHOT_BYTES // (1024 * 1024)} MiB snapshot limit: "
            f"{_examples(capped)}."
        )
    return kept, warnings, sorted(nonregular + excluded + oversized + capped + file_capped)


def _materialize(
    root: Path, entries: list[dict[str, Any]], destination: Path
) -> list[dict[str, Any]]:
    if not entries:
        return []
    request = b"".join(entry["oid"].encode("ascii") + b"\n" for entry in entries)
    stream = _git(root, "cat-file", "--batch", input_data=request).stdout
    offset = 0
    files: list[dict[str, Any]] = []
    for entry in entries:
        end = stream.find(b"\n", offset)
        if end < 0:
            raise RuntimeError(f"truncated Git blob stream for {entry['path']}")
        header = stream[offset:end].split()
        offset = end + 1
        if (
            len(header) != 3
            or header[0].decode("ascii", errors="ignore") != entry["oid"]
            or header[1] != b"blob"
        ):
            raise RuntimeError(f"unexpected Git object for {entry['path']}")
        size = int(header[2])
        data = stream[offset : offset + size]
        offset += size
        if len(data) != size or stream[offset : offset + 1] != b"\n":
            raise RuntimeError(f"truncated Git blob for {entry['path']}")
        offset += 1
        target = destination.joinpath(*PurePosixPath(entry["path"]).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines = 0 if b"\0" in data else data.count(b"\n") + bool(data and not data.endswith(b"\n"))
        files.append(
            {
                "path": entry["path"],
                "blob": entry["oid"],
                "lines": int(lines),
                "symbols": [],
            }
        )
    return files


def _source_path(value: object, snapshot_root: Path, paths: set[str]) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            value = candidate.resolve().relative_to(snapshot_root.resolve()).as_posix()
        except (OSError, ValueError):
            return None
    else:
        value = value.replace("\\", "/")
        parsed = PurePosixPath(value)
        if parsed.is_absolute() or any(part == ".." for part in parsed.parts):
            return None
        value = parsed.as_posix().removeprefix("./")
    return value if value in paths else None


def _line(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _normalize_graph(
    extracted: dict[str, Any],
    files: list[dict[str, Any]],
    snapshot_root: Path,
    blocked: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    paths = {item["path"] for item in files}
    nodes = extracted.get("nodes")
    raw_edges = extracted.get("edges")
    if not isinstance(nodes, list) or not isinstance(raw_edges, list):
        raise TypeError("Graphify returned an invalid extraction payload")

    node_paths: dict[str, set[str]] = {}
    covered: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            continue
        path = _source_path(node.get("source_file"), snapshot_root, paths)
        if path:
            node_paths.setdefault(node["id"], set()).add(path)
            covered.add(path)

    structural = {
        edge.get("target")
        for edge in raw_edges
        if isinstance(edge, dict) and edge.get("relation") in ("contains", "defines", "method")
    }
    symbols: dict[str, set[tuple[int, str]]] = {path: set() for path in paths}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        owners = node_paths.get(node_id, set())
        label = node.get("label")
        line = _line(node.get("source_location"))
        marked = bool(node.get("_callable")) and not bool(node.get("_callable_class"))
        shaped = (
            isinstance(label, str) and label.endswith(("()", " (macro)")) and node_id in structural
        )
        if len(owners) != 1 or not isinstance(label, str) or line is None or not (marked or shaped):
            continue
        name = label.removeprefix(".").removesuffix("()")
        symbols[next(iter(owners))].add((line, name))
    for item in files:
        item["symbols"] = [
            {"name": name, "line": line} for line, name in sorted(symbols[item["path"]])
        ]

    edges: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    unresolved = 0
    for edge in raw_edges:
        if not isinstance(edge, dict):
            unresolved += 1
            continue
        sources = node_paths.get(edge.get("source"), set())
        targets = node_paths.get(edge.get("target"), set())
        evidence = _source_path(edge.get("source_file"), snapshot_root, paths)
        relation, confidence = edge.get("relation"), edge.get("confidence")
        line = _line(edge.get("source_location"))
        if (
            len(sources) != 1
            or len(targets) != 1
            or evidence is None
            or line is None
            or not isinstance(relation, str)
            or not isinstance(confidence, str)
        ):
            unresolved += 1
            continue
        source, target = next(iter(sources)), next(iter(targets))
        if source == target:
            continue
        key = (source, target, relation, confidence, evidence, line)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "source_path": source,
                "target_path": target,
                "relation": relation,
                "confidence": confidence,
                "path": evidence,
                "line": line,
            }
        )
    edges.sort(
        key=lambda edge: (
            edge["path"],
            edge["line"] or 0,
            edge["source_path"],
            edge["target_path"],
            edge["relation"],
            edge["confidence"],
        )
    )

    warnings: list[str] = []
    if blocked:
        warnings.append(
            f"Kept {len(blocked)} preprocessed Fortran file(s) as inventory only because the "
            f"Graphify extractor invokes host cpp: {_examples(blocked)}."
        )
    uncovered = sorted(paths - covered - set(blocked))
    if uncovered:
        suffixes: dict[str, int] = {}
        for path in uncovered:
            suffix = PurePosixPath(path).suffix.lower() or "[no extension]"
            suffixes[suffix] = suffixes.get(suffix, 0) + 1
        kinds = ", ".join(f"{suffix} ({count})" for suffix, count in sorted(suffixes.items()))
        warnings.append(
            f"Graphify produced no structural coverage for {len(uncovered)} file(s); kept as "
            f"inventory only ({kinds}): {_examples(uncovered)}."
        )
    if unresolved:
        warnings.append(
            f"Omitted {unresolved} unresolved, ambiguous, or external Graphify relationship(s)."
        )
    return files, edges, warnings


def _snapshot_cache_path(cache_dir: Path, sha: str) -> Path:
    return cache_dir / "snapshots" / EXTRACTOR.replace("==", "-") / f"{sha}.json"


def _read_snapshot_cache(path: Path, sha: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("extractor") != EXTRACTOR
        or value.get("sha") != sha
        or not isinstance(value.get("files"), list)
        or not isinstance(value.get("edges"), list)
        or not isinstance(value.get("warnings"), list)
        or not isinstance(value.get("omitted_paths"), list)
    ):
        return None
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _extract_snapshot(
    root: Path,
    sha: str,
    cache_dir: Path,
    extract,
) -> dict[str, Any]:
    cache_path = _snapshot_cache_path(cache_dir, sha)
    cached = _read_snapshot_cache(cache_path, sha)
    if cached is not None:
        return cached

    entries, warnings, omitted_paths = _inventory(root, sha)
    blocked = [
        entry["path"]
        for entry in entries
        if PurePosixPath(entry["path"]).suffix in PREPROCESSED_FORTRAN
    ]
    with tempfile.TemporaryDirectory(prefix="factory-codebase-") as directory:
        snapshot_root = Path(directory).resolve()
        files = _materialize(root, entries, snapshot_root)
        extraction_paths = [
            snapshot_root.joinpath(*PurePosixPath(item["path"]).parts)
            for item in files
            if item["path"] not in blocked
        ]
        try:
            extracted = extract(
                extraction_paths,
                cache_root=cache_dir / "graphify-cache",
                root=snapshot_root,
                parallel=False,
            )
        except Exception as exc:
            raise RuntimeError(f"Graphify extraction failed at {sha[:12]}: {exc}") from exc
        if not isinstance(extracted, dict):
            raise TypeError(f"Graphify returned an invalid payload at {sha[:12]}")
        failed = extracted.get("failed_sources") or []
        if failed:
            names = []
            file_paths = {item["path"] for item in files}
            for value in failed:
                names.append(
                    _source_path(value, snapshot_root, file_paths) or Path(str(value)).name
                )
            raise RuntimeError(
                f"Graphify failed to extract {len(failed)} tracked file(s) at {sha[:12]}: "
                f"{_examples(sorted(names))}"
            )
        files, edges, graph_warnings = _normalize_graph(extracted, files, snapshot_root, blocked)

    value = {
        "schema": SCHEMA,
        "extractor": EXTRACTOR,
        "sha": sha,
        "files": files,
        "edges": edges,
        "warnings": warnings + graph_warnings,
        "omitted_paths": omitted_paths,
    }
    _atomic_json(cache_path, value)
    return value


def _read_old_history(cache_dir: Path, repo: str) -> dict[str, Any] | None:
    try:
        value = json.loads((cache_dir / "history.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("extractor") != EXTRACTOR
        or value.get("repo") != repo
        or not isinstance(value.get("snapshots"), list)
        or not isinstance(value.get("slots"), list)
    ):
        return None
    return value


def _new_identity(sha: str, path: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{sha}\0{path}".encode()).hexdigest()[:32]
    parent = PurePosixPath(path).parent.as_posix()
    return f"f-{digest}", parent if parent != "." else "(root)"


def _identity_maps(
    root: Path,
    tip: str,
    wanted: dict[str, set[str]],
    old: dict[str, Any] | None,
) -> dict[str, dict[str, tuple[str, str]]]:
    anchors: dict[str, dict[str, tuple[str, str]]] = {}
    if old:
        for snapshot in old["snapshots"]:
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sha"), str):
                continue
            for item in snapshot.get("files", []):
                if all(isinstance(item.get(key), str) for key in ("path", "id", "group")):
                    anchors.setdefault(snapshot["sha"], {})[item["path"]] = (
                        item["id"],
                        item["group"],
                    )

    # ponytail: replay name history for deterministic identity; checkpoint lineage if
    # very long repositories make incremental refresh slow.
    stream = _git(
        root,
        "log",
        "--first-parent",
        "--reverse",
        "--root",
        "--diff-merges=first-parent",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames=50%",
        "--format=COMMIT:%H",
        "--name-status",
        "-z",
        tip,
    ).stdout
    tokens = stream.split(b"\0")
    state: dict[str, tuple[str, str]] = {}
    result: dict[str, dict[str, tuple[str, str]]] = {}
    index = 0
    current: str | None = None

    def finish() -> None:
        if current is None:
            return
        for path, identity in anchors.get(current, {}).items():
            if path in state:
                state[path] = identity
        if current in wanted:
            selected: dict[str, tuple[str, str]] = {}
            for path in wanted[current]:
                identity = state.get(path)
                if identity is None:
                    identity = _new_identity(current, path)
                    state[path] = identity
                selected[path] = identity
            result[current] = selected

    while index < len(tokens):
        token = tokens[index]
        if not token:
            index += 1
            continue
        if token.startswith(b"COMMIT:"):
            finish()
            raw_sha = token.removeprefix(b"COMMIT:")
            if not SHA_RE.fullmatch(raw_sha):
                raise RuntimeError("could not parse Git rename history")
            current = raw_sha.decode("ascii")
            index += 1
            continue
        if current is None:
            raise RuntimeError("could not parse Git rename history")
        status = token.lstrip(b"\n")
        if not status or chr(status[0]) not in "ACDMRTUXB":
            raise RuntimeError("could not parse Git rename history")
        index += 1
        path_count = 2 if chr(status[0]) in "RC" else 1
        if index + path_count > len(tokens):
            raise RuntimeError("truncated Git rename history")
        paths = [_decode_path(tokens[index + offset]) for offset in range(path_count)]
        index += path_count
        kind = chr(status[0])
        if kind == "R":
            old_path, new_path = paths
            identity = state.pop(old_path, None) if old_path else None
            if new_path:
                state[new_path] = identity or _new_identity(current, new_path)
        elif kind == "C":
            new_path = paths[1]
            if new_path:
                state[new_path] = _new_identity(current, new_path)
        else:
            path = paths[0]
            if not path:
                continue
            if kind == "D":
                state.pop(path, None)
            elif kind == "A":
                state[path] = _new_identity(current, path)
            else:
                state.setdefault(path, _new_identity(current, path))
    finish()
    missing = set(wanted) - set(result)
    if missing:
        raise RuntimeError(f"rename history omitted {len(missing)} selected commit(s)")
    return result


def build_history(
    root: Path,
    ref: str,
    repo: str,
    cache_dir: Path,
    limit: int = 80,
) -> dict[str, Any]:
    """Build schema-1 history from local Git objects and publish it atomically."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("history limit must be a positive integer")
    if not isinstance(ref, str) or not ref:
        raise ValueError("ref must be nonempty")
    if not isinstance(repo, str) or not repo:
        raise ValueError("repo must be nonempty")
    root, cache_dir = Path(root).resolve(), Path(cache_dir).resolve()
    extract = _load_graphify()
    tip = _resolve_tip(root, ref)
    commits, bounded = _commit_rows(root, tip, limit)
    if not commits:
        raise RuntimeError(f"no commits found at {ref!r}")
    shallow = _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true"
    old = _read_old_history(cache_dir, repo)

    raw_snapshots = [
        _extract_snapshot(root, commit["sha"], cache_dir, extract) for commit in commits
    ]
    wanted = {
        commit["sha"]: {item["path"] for item in raw["files"]} | set(raw["omitted_paths"])
        for commit, raw in zip(commits, raw_snapshots)
    }
    identities = _identity_maps(root, tip, wanted, old)

    snapshots: list[dict[str, Any]] = []
    first_group: dict[str, str] = {}
    for commit, raw in zip(commits, raw_snapshots):
        identity = identities[commit["sha"]]
        files = []
        path_to_id: dict[str, str] = {}
        for item in raw["files"]:
            file_id, group = identity[item["path"]]
            first_group.setdefault(file_id, group)
            path_to_id[item["path"]] = file_id
            files.append(
                {
                    "id": file_id,
                    "path": item["path"],
                    "blob": item["blob"],
                    "lines": item["lines"],
                    "group": group,
                    "symbols": item["symbols"],
                }
            )
        edges = [
            {
                "source": path_to_id[edge["source_path"]],
                "target": path_to_id[edge["target_path"]],
                "relation": edge["relation"],
                "confidence": edge["confidence"],
                "path": edge["path"],
                "line": edge["line"],
            }
            for edge in raw["edges"]
            if edge["source_path"] in path_to_id and edge["target_path"] in path_to_id
        ]
        snapshots.append(
            {
                **commit,
                "files": files,
                "edges": edges,
                "warnings": list(raw["warnings"]),
                "unmapped": [identity[path][0] for path in raw["omitted_paths"]],
            }
        )

    if bounded:
        snapshots[0]["warnings"].insert(
            0, f"History is limited to the newest {limit} commits; earlier commits are omitted."
        )
    if shallow:
        snapshots[0]["warnings"].insert(
            0, "Repository is shallow; history before the local shallow boundary is unavailable."
        )

    old_slots = {
        slot["id"]: slot
        for slot in (old["slots"] if old else [])
        if isinstance(slot, dict)
        and isinstance(slot.get("id"), str)
        and isinstance(slot.get("group"), str)
        and isinstance(slot.get("order"), int)
    }
    union = {item["id"] for snapshot in snapshots for item in snapshot["files"]}
    next_order = max((slot["order"] for slot in old_slots.values()), default=-1) + 1
    slots: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for snapshot in snapshots:
        for item in snapshot["files"]:
            file_id = item["id"]
            if file_id in seen_ids:
                continue
            seen_ids.add(file_id)
            if file_id in old_slots:
                slot = {
                    "id": file_id,
                    "group": old_slots[file_id]["group"],
                    "order": old_slots[file_id]["order"],
                }
            else:
                slot = {"id": file_id, "group": first_group[file_id], "order": next_order}
                next_order += 1
            slots.append(slot)
    slots = sorted((slot for slot in slots if slot["id"] in union), key=lambda slot: slot["order"])
    order = {slot["id"]: slot["order"] for slot in slots}
    for snapshot in snapshots:
        snapshot["files"].sort(key=lambda item: (order[item["id"]], item["path"]))

    history = {
        "schema": SCHEMA,
        "extractor": EXTRACTOR,
        "repo": repo,
        "ref": ref,
        "tip": tip,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "truncated": bounded or shallow,
        "snapshots": snapshots,
        "slots": slots,
    }
    _atomic_json(cache_dir / "history.json", history)
    return history


def _positive(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="factory codebase", description="build local codebase history"
    )
    parser.add_argument(
        "--ref", help="local Git ref to inspect (default: origin/<main>, then <main>)"
    )
    parser.add_argument(
        "--limit",
        type=_positive,
        default=80,
        help="newest first-parent commits (default: 80)",
    )
    args = parser.parse_args(argv)
    cfg = config.load()
    ref = args.ref
    cache_dir = cfg.factory / "codebase"
    try:
        ref = ref or default_ref(cfg.root, cfg.main)
        history = build_history(cfg.root, ref, cfg.repo, cache_dir, args.limit)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        print(f"factory codebase: {exc}", file=sys.stderr)
        return 1
    print(f"built {len(history['snapshots'])} snapshots at {cache_dir / 'history.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
