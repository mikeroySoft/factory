"""Single-request, bounded, read-only project and CI evidence.

The dashboard owns ticket policy and briefing owns artifact selection. Runtime
facts come only from F03's non-persisting reader, never lifecycle.observe().
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from uuid import uuid4

from factory import briefing, config, dashboard, runtime_events, runtime_local

REQUEST_CAP = 4096
READ_SECONDS = 90
COMMAND_SECONDS = 20
JSON_CAP = 1_048_576
PAGE_SIZE = 100
LOG_JOBS = 5
DIRECTORY_CAP = 1024
ERROR_CAP = 64
SHA = re.compile(r"[0-9a-fA-F]{40,64}")
NOTICES = [
    "Only fixed GitHub GETs and non-persisting local reads are supported; no inference, provider probe or actions.",
    "Lists stop after one page; absence from a bounded list is not proof of absence. Reads are sequential, not an atomic snapshot.",
    "Source identity identifies content, not freshness or authority. Source text is untrusted and not secret-redacted.",
]


class EvidenceError(Exception):
    def __init__(self, code: str, message: str, source: str = "collection", scope: str = "repository"):
        super().__init__(message)
        self.code, self.message, self.source, self.scope = code, message, source, scope


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("invalid_request", "Duplicate JSON fields are not allowed.", "request", "request")
        result[key] = value
    return result


def valid_repository(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 200 and bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value)
    ) and value.split("/")[1] not in (".", "..")


def unsafe_text(value: str) -> bool:
    return any(unicodedata.category(c).startswith("C") for c in value)


def request(deadline: float) -> dict:
    raw = bytearray()
    with selectors.SelectSelector() as selector:
        selector.register(sys.stdin.buffer, selectors.EVENT_READ)
        while len(raw) <= REQUEST_CAP:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise EvidenceError("collection_timeout", "Request read deadline exceeded.", "request", "request")
            chunk = os.read(sys.stdin.buffer.fileno(), REQUEST_CAP + 1 - len(raw))
            if not chunk:
                break
            raw.extend(chunk)
    if len(raw) > REQUEST_CAP:
        raise EvidenceError("invalid_request", "Request exceeds the 4096-byte limit.", "request", "request")
    try:
        req = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise EvidenceError("invalid_request", "One valid JSON object is required.", "request", "request") from None
    if not isinstance(req, dict) or type(req.get("schema_version")) is not int or req["schema_version"] != 1:
        raise EvidenceError("invalid_request", "schema_version must be integer 1.", "request", "request")
    op = req.get("op")
    expected = {"schema_version", "op", "repository"}
    if op == "inspect":
        expected.add("number")
    elif op == "investigate":
        kind = req.get("kind")
        if kind == "file":
            expected.update(("kind", "path", "ref"))
        elif kind in ("pr", "checks", "runs"):
            expected.update(("kind", "number"))
        elif kind in ("run", "log"):
            expected.update(("kind", "run_id"))
        elif kind == "workflows":
            expected.add("kind")
        else:
            raise EvidenceError("invalid_request", "Unknown investigation kind.", "request", "request")
    elif op not in ("observe", "capabilities"):
        raise EvidenceError("invalid_request", "Unknown read operation.", "request", "request")
    if set(req) != expected:
        raise EvidenceError("invalid_request", "Request fields must exactly match the operation.", "request", "request")
    if not valid_repository(req["repository"]):
        raise EvidenceError("invalid_request", "repository must be an owner/name slug, at most 200 characters.", "request", "request")
    for field in ("number", "run_id"):
        if field in req and (type(req[field]) is not int or not 0 < req[field] < 2**63):
            raise EvidenceError("invalid_request", f"{field} must be a positive integer below 9223372036854775808.", "request", "request")
    if op == "investigate" and req["kind"] == "file":
        path, ref = req["path"], req["ref"]
        if (not isinstance(path, str) or not 0 < len(path) <= 1024 or unsafe_text(path)
                or any(part in ("", ".", "..") for part in path.split("/"))
                or any(c in "\\:%?#" for c in path)):
            raise EvidenceError("invalid_request", "path must be a safe relative repository file path.", "request", "request")
        if (not isinstance(ref, str) or not 0 < len(ref) <= 255 or unsafe_text(ref)
                or ref.startswith(("-", "/")) or ref.endswith(("/", "."))
                or ".." in ref or "@{" in ref or "//" in ref
                or any(c.isspace() or c in "\\:%?#~^*[" for c in ref)):
            raise EvidenceError("invalid_request", "ref must be an explicit branch, tag or commit, not a revision expression.", "request", "request")
    return req


def clean_text(text: str) -> str:
    # OSC/DCS strings can carry hyperlinks or terminal commands; strip them as
    # units, including unfinished strings at the bounded prefix boundary.
    text = re.sub(r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\|$)|[PX^_].*?(?:\x1b\\|$)|\[[0-?]*[ -/]*[@-~]?|[@-_])", "", text, flags=re.S)
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))


def source(label: str, value: object, *, url: str | None = None, path: str | None = None, truncated: bool = False) -> dict:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    data = clean_text(text).encode("utf-8")
    result = {"label": clean_text(label)[:2048], "text": data[:briefing.SOURCE_CAP].decode("utf-8", errors="ignore"),
              "truncated": bool(truncated or len(data) > briefing.SOURCE_CAP)}
    if url:
        result["url"] = clean_text(url)
    if path:
        result["path"] = clean_text(path)
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).digest()
    result["id"] = "S" + str(int.from_bytes(digest[:8], "big"))
    return result


def case_summary(ticket: dict) -> dict:
    result = {key: ticket.get(key) for key in ("number", "title", "stage", "labels", "assignees", "url", "updated_at")}
    pr = ticket.get("pr")
    result["pr"] = {key: pr.get(key) for key in ("number", "url", "state", "approved", "draft", "review_decision", "merged_at")} if pr else None
    for record in (result, result["pr"] or {}):
        for key, value in record.items():
            if isinstance(value, str):
                record[key] = clean_text(value)
            elif isinstance(value, list):
                record[key] = [clean_text(item) for item in value]
    return result


def failed(result: dict, exc: EvidenceError) -> None:
    error = {"source": exc.source, "scope": exc.scope, "code": exc.code}
    if error not in result["errors"] and len(result["errors"]) < ERROR_CAP:
        result["errors"].append(error)
    result["ok"] = False


def github_read(endpoint: str, deadline: float, *, text: bool = False) -> tuple[object, bool]:
    """Bound both pipes and kill the process group on clipping or timeout."""
    stop = min(deadline, time.monotonic() + COMMAND_SECONDS)
    if stop <= time.monotonic():
        raise EvidenceError("collection_timeout", "Evidence read deadline exceeded.", endpoint)
    cap = briefing.SOURCE_CAP if text else JSON_CAP
    command = ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint]
    if text:
        command.append("--allow-escape-sequences")
    data, diagnostic = bytearray(), bytearray()
    proc = None
    truncated = False
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True,
                                env={**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_OPTIONAL_LOCKS": "0", "GH_PAGER": "cat"})
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, data)
            selector.register(proc.stderr, selectors.EVENT_READ, diagnostic)
            while selector.get_map():
                remaining = stop - time.monotonic()
                if remaining <= 0 or not (ready := selector.select(remaining)):
                    raise EvidenceError("collection_timeout", "A GitHub read exceeded its deadline.", endpoint)
                for key, _ in ready:
                    target = key.data
                    limit = cap if target is data else REQUEST_CAP
                    chunk = os.read(key.fd, min(65536, limit + 1 - len(target)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target.extend(chunk)
                    if len(target) > limit:
                        if target is data and text:
                            truncated = True
                            break
                        raise EvidenceError("response_too_large", "A GitHub response exceeded its bounded read limit.", endpoint)
                if truncated:
                    break
        if not truncated:
            status = proc.wait(timeout=max(0.001, stop - time.monotonic()))
            if status:
                match = re.search(rb"HTTP[ /](\d{3})", diagnostic)
                code = {b"401": "github_authentication", b"403": "github_forbidden", b"404": "github_not_found", b"429": "github_rate_limited"}.get(match[1] if match else b"", "github_unavailable")
                raise EvidenceError(code, "GitHub source is unavailable; access failures do not establish absence. Raw diagnostics withheld.", endpoint)
    except subprocess.TimeoutExpired:
        raise EvidenceError("collection_timeout", "A GitHub read exceeded its deadline.", endpoint) from None
    except OSError:
        raise EvidenceError("github_unavailable", "GitHub command unavailable; raw diagnostics withheld.", endpoint) from None
    finally:
        if proc is not None:
            # Descendants may retain pipe descriptors after the direct child exits.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()
    if text:
        return clean_text(bytes(data[:cap]).decode("utf-8", errors="replace")), truncated
    try:
        return json.loads(data), False
    except (ValueError, UnicodeError, RecursionError):
        raise EvidenceError("github_unavailable", "GitHub returned unreadable JSON.", endpoint) from None


def capabilities() -> dict:
    return {
        "reads": [
            {"op": "observe", "fields": [], "description": "Compact Factory-selected cases and nullable attention count."},
            {"op": "inspect", "fields": ["number"], "description": "Selected issue, recorded decisions, F03 runtime and bounded artifacts."},
            {"op": "investigate", "kind": "workflows", "fields": [], "description": "Registered workflow paths, not a revision inventory."},
            {"op": "investigate", "kind": "file", "fields": ["path", "ref"], "description": "Regular UTF-8 file at an immutable resolved commit."},
            {"op": "investigate", "kind": "pr", "fields": ["number"], "description": "Observed head/base and available diff patches."},
            {"op": "investigate", "kind": "checks", "fields": ["number"], "description": "Checks and statuses for the exact observed PR head."},
            {"op": "investigate", "kind": "runs", "fields": ["number"], "description": "Actions runs for the exact observed PR head."},
            {"op": "investigate", "kind": "run", "fields": ["run_id"], "description": "Run and latest-attempt jobs."},
            {"op": "investigate", "kind": "log", "fields": ["run_id"], "description": "At most five latest-attempt log prefixes, failed jobs first."},
            {"op": "capabilities", "fields": [], "description": "Implemented schema and producer support; no case collection."},
        ],
        "limits": {"request_bytes": REQUEST_CAP, "list_entries": PAGE_SIZE, "json_bytes": JSON_CAP,
                   "source_bytes": briefing.SOURCE_CAP, "log_jobs": LOG_JOBS, "command_seconds": COMMAND_SECONDS,
                   "read_seconds": READ_SECONDS, "directory_entries": DIRECTORY_CAP, "path_characters": 1024,
                   "ref_characters": 255, "repository_characters": 200, "id_exclusive_max": 2**63,
                   "runtime_bytes": runtime_events.BYTE_LIMIT, "runtime_events": runtime_events.EVENT_LIMIT},
        "producers": {"evidence_schema": 1, "runtime_schema": 1, "runtime_reader": "F03 non-persisting",
                      "escalation_packet": "escalations/{number}.md"},
        "actions": [],
        "unavailable": ["arbitrary shell, API URLs or host paths", "writes, dispatch or action execution",
                        "provider configuration", "full pagination and complete log archives", "configured worker attribution"],
    }


class GitHub:
    """One request's fixed-repository GETs and independent source failures."""
    def __init__(self, req: dict, result: dict, deadline: float):
        self.prefix = f"repos/{req['repository']}"
        self.result, self.deadline = result, deadline

    def fetch(self, path: str, *, text: bool = False):
        return github_read(f"{self.prefix}/{path}", self.deadline, text=text)[0]

    def cite(self, label: str, path: str, value: object, truncated: bool = False):
        item = source(label, value, url=f"https://api.github.com/{self.prefix}/{path}", truncated=truncated)
        self.result["sources"].append(item)
        if item["truncated"]:
            self.result["coverage"]["notices"].append(f"{label}: bounded page, prefix or source text; later evidence may be absent.")

    def page(self, label: str, path: str, key: str | None = None, *, cite: bool = True, limit: int = PAGE_SIZE, expected_sha: str | None = None):
        value = self.fetch(path)
        items = value.get(key) if isinstance(value, dict) and key else value
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise EvidenceError("invalid_response", "GitHub list response has an unexpected shape.", f"{self.prefix}/{path}")
        total = value.get("total_count", len(items)) if isinstance(value, dict) else len(items)
        if type(total) is not int or total < 0:
            raise EvidenceError("invalid_response", "GitHub list total is invalid.", f"{self.prefix}/{path}")
        cut = len(items) >= limit or total > len(items)
        items = items[:limit]
        if cite:
            bounded = {**value, key: items} if key else items
            self.cite(label, path, bounded, cut)
        if expected_sha is not None and (
            isinstance(value, dict) and "sha" in value and value["sha"] != expected_sha
            or any("head_sha" in item and item["head_sha"] != expected_sha for item in items)
        ):
            raise EvidenceError("head_mismatch", "Returned evidence identifies another SHA; it is not PR-head success evidence.", f"{self.prefix}/{path}")
        if cut:
            self.result["coverage"]["notices"].append(f"{label}: only the first {limit} entries are covered.")
        return items, cut

    def optional_page(self, label: str, path: str, key: str | None = None, **kwargs):
        try:
            return self.page(label, path, key, **kwargs)
        except EvidenceError as exc:
            failed(self.result, exc)
            return [], True


def checked_sha(value, path: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise EvidenceError("invalid_response", "GitHub returned an invalid immutable identity.", path)
    return value


def investigate(req: dict, result: dict, deadline: float) -> None:
    gh = GitHub(req, result, deadline)
    kind = req["kind"]
    detail = result["investigation"] = {key: req[key] for key in ("kind", "number", "run_id", "path", "ref") if key in req}
    notices = result["coverage"]["notices"]
    if kind == "workflows":
        gh.page("Registered Actions workflows and paths", f"actions/workflows?per_page={PAGE_SIZE}&page=1", "workflows")
        notices.append("Registered workflows are not a complete file inventory at every ref; newly added workflows may not be registered.")
        return
    if kind == "file":
        revision = f"commits/{quote(req['ref'], safe='')}"
        commit = gh.fetch(revision)
        sha = detail["commit_sha"] = checked_sha(commit.get("sha"), revision)
        tree_sha = commit.get("commit", {}).get("tree", {}).get("sha")
        gh.cite("Resolved immutable repository revision", revision,
                {"ref": req["ref"], "commit_sha": sha, "commit": commit.get("commit")})
        parts = req["path"].split("/")
        for index, name in enumerate(parts):
            tree_path = f"git/trees/{checked_sha(tree_sha, revision)}"
            tree = gh.fetch(tree_path)
            if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                raise EvidenceError("invalid_response", "GitHub returned an unreadable tree.", tree_path)
            if not all(isinstance(item, dict) and isinstance(item.get("path"), str) for item in tree["tree"]):
                raise EvidenceError("incomplete_tree", "Malformed tree entries cannot establish path absence.", tree_path)
            # Unlike list pages, a non-recursive tree must be complete before a
            # missing entry is evidence. JSON_CAP bounds traversal work in bytes.
            if tree.get("truncated") is not False:
                gh.cite("Incomplete repository tree", tree_path, {"commit_sha": sha, "tree_complete": False}, True)
                raise EvidenceError("incomplete_tree", "Tree completeness is unknown; absence cannot be determined.", tree_path)
            entry = next((item for item in tree["tree"] if isinstance(item, dict) and item.get("path") == name), None)
            if entry is None:
                gh.cite("Repository path absent at observed revision", tree_path,
                        {"commit_sha": sha, "path": req["path"], "missing_component": "/".join(parts[:index + 1]), "tree_complete": True})
                raise EvidenceError("file_not_found", "Repository path does not exist at the observed commit.", tree_path)
            final = index == len(parts) - 1
            if entry.get("mode") not in (("100644", "100755") if final else ("040000",)) or entry.get("type") != ("blob" if final else "tree"):
                raise EvidenceError("unsupported_file", "Symlinks, submodules, directories and non-regular files are refused.", tree_path)
            tree_sha = entry.get("sha")
        blob_sha = checked_sha(tree_sha, revision)
        path = f"contents/{quote(req['path'], safe='/')}?{urlencode({'ref': sha})}"
        value = gh.fetch(path)
        if not isinstance(value, dict) or value.get("type") != "file" or value.get("sha") != blob_sha or value.get("encoding") != "base64":
            raise EvidenceError("unsupported_file", "Regular-file contents at the resolved tree identity are unavailable.", path)
        try:
            data = base64.b64decode("".join(value["content"].split()), validate=True)
            text = data.decode("utf-8")
        except (ValueError, UnicodeError, KeyError, AttributeError):
            raise EvidenceError("unsupported_file", "File is not available as regular UTF-8 text.", path) from None
        gh.cite(f"{req['path']} at {sha}", path, text, len(data) > briefing.SOURCE_CAP)
        return
    if kind in ("pr", "checks", "runs"):
        pr_path = f"pulls/{req['number']}"
        pr = gh.fetch(pr_path)
        if not isinstance(pr, dict) or pr.get("number") != req["number"]:
            raise EvidenceError("invalid_response", "GitHub returned a different PR identity.", pr_path)
        sha = detail["head_sha"] = checked_sha(pr.get("head", {}).get("sha"), pr_path)
        base_repo = (pr.get("base", {}).get("repo") or {}).get("full_name")
        if base_repo != req["repository"]:
            raise EvidenceError("invalid_response", "PR base repository does not match the selected repository.", pr_path)
        summary = {key: pr.get(key) for key in ("number", "title", "state", "draft", "merged", "mergeable", "mergeable_state", "updated_at", "changed_files")}
        for side in ("head", "base"):
            summary[side] = {key: pr[side].get(key) for key in ("ref", "sha", "label")}
            summary[side]["repository"] = (pr[side].get("repo") or {}).get("full_name")
        gh.cite("Observed PR head and base", pr_path, summary)
        if kind == "pr":
            gh.page("PR changed files and available diff patches", f"{pr_path}/files?per_page={PAGE_SIZE}&page=1")
            notices.append("GitHub may omit or shorten individual diff patches, especially binary/large files; file entries do not guarantee a complete diff.")
        elif kind == "checks":
            gh.optional_page("Check runs returned for PR-head query", f"commits/{sha}/check-runs?per_page={PAGE_SIZE}&page=1", "check_runs", expected_sha=sha)
            gh.optional_page("Combined statuses returned for PR-head query", f"commits/{sha}/status?per_page={PAGE_SIZE}&page=1", "statuses", expected_sha=sha)
        else:
            gh.page("Actions runs returned for PR-head query", f"actions/runs?head_sha={sha}&per_page={PAGE_SIZE}&page=1", "workflow_runs", expected_sha=sha)
        return
    run_path = f"actions/runs/{req['run_id']}"
    run = gh.fetch(run_path)
    if not isinstance(run, dict) or run.get("id") != req["run_id"] or (run.get("repository") or {}).get("full_name") != req["repository"]:
        raise EvidenceError("invalid_response", "GitHub returned a different run or repository identity.", run_path)
    gh.cite("Actions run details", run_path, run)
    jobs_path = f"{run_path}/jobs?filter=latest&per_page={PAGE_SIZE}&page=1"
    jobs, _ = gh.page("Latest-attempt run jobs", jobs_path, "jobs")
    if kind == "log":
        selected = sorted(jobs, key=lambda job: job.get("conclusion") not in ("failure", "timed_out", "action_required"))[:LOG_JOBS]
        notices.append(f"Selected {len(selected)} of {len(jobs)} observed jobs; log prefixes, failed jobs first, not a full run archive.")
        if not selected:
            raise EvidenceError("logs_unavailable", "No job logs are selectable from the bounded latest-attempt list.", jobs_path)
        for job in selected:
            job_id = job.get("id")
            path = f"actions/jobs/{job_id}/logs"
            try:
                if type(job_id) is not int or not 0 < job_id < 2**63:
                    raise EvidenceError("invalid_response", "GitHub returned an invalid job identifier.", jobs_path)
                if job.get("run_id", req["run_id"]) != req["run_id"] or job.get("run_attempt", run.get("run_attempt")) != run.get("run_attempt"):
                    raise EvidenceError("invalid_response", "Job does not belong to the observed latest run attempt.", jobs_path)
                text, cut = github_read(f"{gh.prefix}/{path}", deadline, text=True)
                gh.cite(f"Job {job_id} ({job.get('name', '')}) log prefix", path, text, cut)
            except EvidenceError as exc:
                failed(result, exc)


def local_inventory(cfg: config.Config, result: dict) -> tuple[set[int], dict[int, list]]:
    """Bound directory enumeration itself, never recursively scan worktrees."""
    numbers, attempts = set(), {}
    for rel in (".", "logs"):
        directory = None
        try:
            directory = os.open(cfg.factory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if rel != ".":
                child = os.open(rel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index == DIRECTORY_CAP:
                        result["coverage"]["notices"].append(f"Local {rel} inventory stops after {DIRECTORY_CAP} directory entries.")
                        failed(result, EvidenceError("entry_limit", "Local inventory is incomplete.", rel, "inventory"))
                        break
                    if rel == ".":
                        match = re.fullmatch(r"wt-([0-9]{1,19})", entry.name)
                        if match and entry.is_dir(follow_symlinks=False):
                            numbers.add(int(match[1]))
                    else:
                        match = re.fullmatch(r"([0-9]{1,19})-attempt-([0-9]{1,9})\.log", entry.name)
                        if match and entry.is_file(follow_symlinks=False):
                            number, attempt = int(match[1]), int(match[2])
                            info = entry.stat(follow_symlinks=False)
                            numbers.add(number)
                            attempts.setdefault(number, []).append({"attempt": attempt, "path": f"logs/{entry.name}",
                                "mtime": dashboard.iso(info.st_mtime), "size": info.st_size})
        except FileNotFoundError:
            pass
        except OSError:
            failed(result, EvidenceError("unreadable", "Local inventory unavailable.", rel, "inventory"))
        finally:
            if directory is not None:
                os.close(directory)
    return numbers, attempts


def issue_record(value: dict) -> dict:
    return {"number": value["number"], "title": value["title"], "state": value["state"].upper(),
            "url": value["html_url"], "body": value.get("body") or "", "createdAt": value.get("created_at"),
            "updatedAt": value.get("updated_at"), "closedAt": value.get("closed_at"),
            "labels": {"nodes": [{"name": label["name"]} for label in value.get("labels", [])[:20]]},
            "assignees": {"nodes": [{"login": user["login"]} for user in value.get("assignees", [])[:5]]}}


def pull_record(value: dict) -> dict:
    # Convert REST wire shape once; the dashboard's PR policy remains authoritative.
    return dashboard.pr_record({"number": value["number"], "state": value["state"].upper(),
        "url": value["html_url"], "body": value.get("body") or "", "isDraft": value.get("draft", False),
        "createdAt": value.get("created_at"), "closedAt": value.get("closed_at"), "mergedAt": value.get("merged_at"),
        "additions": value.get("additions"), "deletions": value.get("deletions"), "changedFiles": value.get("changed_files"),
        "labels": {"nodes": value.get("labels", [])[:20]}})


def collect_cases(req: dict, result: dict, cfg: config.Config, deadline: float) -> None:
    gh = GitHub(req, result, deadline)
    runtime = runtime_events.project(cfg.factory / "events.jsonl", [(cfg.lock, "host"), (cfg.factory / "locks" / "merge.lock", "repository")])
    result["sources"].append(source("Local runtime observation", runtime))
    for error in runtime["errors"]:
        failed(result, EvidenceError(error["code"], "Runtime observation is incomplete.", error["source"], error["scope"]))
    on_disk, attempts = local_inventory(cfg, result)
    by_ticket = {}
    for execution in runtime["executions"]:
        if execution["ticket"] is not None:
            by_ticket.setdefault(execution["ticket"], []).append(execution)
    page = f"state=all&sort=updated&direction=desc&per_page={PAGE_SIZE}&page=1"
    issues, issues_cut = gh.optional_page("Factory issue candidates", f"issues?{page}", cite=False)
    pulls, pulls_cut = gh.optional_page("Factory PR candidates", f"pulls?{page}", cite=False)
    prs = {}
    for value in pulls:
        try:
            match = dashboard.AGENT_BRANCH.fullmatch(value.get("head", {}).get("ref", ""))
            if match:
                number, rec = int(match[1]), pull_record(value)
                if number not in prs or (rec["merged_at"] and not prs[number]["merged_at"]):
                    prs[number] = rec
        except (KeyError, TypeError, AttributeError, ValueError):
            failed(result, EvidenceError("invalid_response", "A PR record is unreadable.", "pulls"))
            pulls_cut = True
    tickets, comment_counts = [], {}
    for value in issues:
        try:
            if "pull_request" in value:
                continue
            issue = issue_record(value)
            number = issue["number"]
            if type(number) is not int or not 0 < number < 2**63:
                raise ValueError
            if not dashboard.selected_issue(issue, prs, on_disk, by_ticket):
                continue
            count = value.get("comments", 0)
            comment_counts[number] = count if type(count) is int and count >= 0 else 0
            executions = by_ticket.get(number, [])
            disk = {"lock_held": any(e["state"] == "active" for e in executions),
                    "attempts": sorted(attempts.get(number, []), key=lambda a: a["attempt"]),
                    "gate": None, "review": None, "prompt": None, "pr_body": None, "worktree": None}
            ticket = dashboard.build_ticket(issue, prs.get(number), disk, executions=executions, include_worker=False)
            # Spend is not recomputed from a partial journal; unknown is not zero.
            ticket["spend"] = None
            tickets.append(ticket)
        except (KeyError, TypeError, AttributeError, ValueError):
            failed(result, EvidenceError("invalid_response", "An issue record is unreadable.", "issues"))
            issues_cut = True
    tickets.sort(key=lambda ticket: ticket["number"], reverse=True)
    result["coverage"]["notices"].extend([
        "Cases use the dashboard's Factory labels/agent-branch/local-evidence selection and stage policy; issue lists also contain PRs.",
        "Label/assignee reads are bounded to 20/5 entries; configured worker attribution, spend totals and full dispatcher bundles are not collected.",
        "Runtime stage evidence is F03's non-persisting projection, not writable reconciliation or an artifact heuristic.",
    ])
    if req["op"] == "observe":
        cases = result["cases"] = [case_summary(ticket) for ticket in tickets[:PAGE_SIZE]]
        unknown = (issues_cut or pulls_cut or any(e["state"] == "unknown" for e in runtime["executions"])
                   or any(len(value.get("labels", [])) > 20 for value in issues)
                   or any(error["scope"] == "inventory" for error in result["errors"]))
        result["attention_count"] = None if unknown else sum(c["stage"] in ("escalated", "needs-info") for c in cases)
        result["sources"].insert(0, source("Current bounded Factory case summaries", cases))
        return
    ticket = next((ticket for ticket in tickets if ticket["number"] == req["number"]), None)
    if ticket is None:
        raise EvidenceError("evidence_unavailable" if issues_cut else "unknown_case", "Case is not available in the current bounded Factory selection; no broader search was performed.", "cases", f"ticket:{req['number']}")
    result["case"] = case_summary(ticket)
    number = ticket["number"]
    last_page = max(1, (comment_counts[number] + PAGE_SIZE - 1) // PAGE_SIZE)
    comments, comments_cut = gh.optional_page("Issue comments", f"issues/{number}/comments?per_page={PAGE_SIZE}&page={last_page}", cite=False)
    timeline, timeline_cut = gh.optional_page("Issue timeline", f"issues/{number}/timeline?per_page={PAGE_SIZE}&page=1", cite=False)
    for comment in comments:
        ticket["events"].append({"at": comment.get("created_at") or "", "kind": "comment", "body": comment.get("body") or "", "url": comment.get("html_url") or ticket["url"]})
    for item in timeline:
        if item.get("event") != "commented":
            ticket["events"].append({"at": item.get("created_at") or "", "kind": item.get("event") or "unknown", "detail": (item.get("label") or {}).get("name", "")})
    ticket["timeline_truncated"] = comments_cut or timeline_cut or last_page > 1
    ticket["timeline_coverage"] = "Issue timeline covers its first 100 entries; comments cover only the latest page of at most 100. Earlier/later decisions outside these pages may be missing."
    if ticket["pr"]:
        pr = ticket["pr"]
        pr_page = 1
        try:
            metadata = gh.fetch(f"pulls/{pr['number']}")
            count = metadata.get("comments", 0)
            if type(count) is int and count >= 0:
                pr_page = max(1, (count + 29) // 30)
        except EvidenceError as exc:
            failed(result, exc)
        rows, cut = gh.optional_page("PR comments", f"issues/{pr['number']}/comments?per_page=30&page={pr_page}", cite=False, limit=30)
        pr["comments"] = [{"at": row.get("created_at") or "", "body": row.get("body") or "", "url": row.get("html_url") or pr["url"]} for row in rows]
        pr["comments_truncated"] = cut or pr_page > 1
        for row in pr["comments"]:
            if match := dashboard.VERDICT.search(row["body"]):
                ticket["events"].append({**row, "kind": "verdict", "detail": match[1]})
    read_errors = []
    items = briefing.sources_for(cfg, ticket, [], read_errors=read_errors)
    for item in items:
        result["sources"].append(source(item["label"], item["text"], url=item.get("url"), path=item.get("path"), truncated=item["truncated"]))
    for error in read_errors:
        failed(result, EvidenceError(error["code"], "Selected evidence unavailable.", error["source"], error["scope"]))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["--help"], ["-h"]):
        print("usage: factory evidence --root <explicit-main-checkout>\n\nRead one schema_version:1 JSON object from stdin; emit one bounded JSON result.\nExit: 0 successful/bounded, 1 partial/unavailable, 2 invalid request/scope.")
        return 0
    result = {"schema_version": 1, "ok": True, "scope": {"repository": None, "root": None},
              "observed_at": datetime.now(UTC).isoformat(), "observation_id": uuid4().hex,
              "coverage": {"status": "unavailable", "notices": list(NOTICES)}, "sources": [], "errors": []}
    deadline = time.monotonic() + READ_SECONDS
    exit_code = 0
    try:
        if len(argv) != 2 or argv[0] != "--root" or not argv[1]:
            raise EvidenceError("invalid_scope", "Supply --root and one operator-selected main checkout.", "invocation", "scope")
        try:
            root = Path(argv[1]).resolve(strict=True)
            if not root.is_dir():
                raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise EvidenceError("invalid_scope", "Selected root must be an existing directory.", "root", "scope") from None
        result["scope"]["root"] = str(root)
        req = request(deadline)
        result["scope"]["repository"] = req["repository"]
        if req["op"] == "observe":
            result.update(cases=[], attention_count=None)
        elif req["op"] == "inspect":
            result["case"] = None
        cfg = runtime_local.load(root)
        if cfg.root.resolve() != root or not valid_repository(cfg.repo):
            raise EvidenceError("invalid_scope", "Select the main checkout explicitly, not a subdirectory or worktree.", "root", "scope")
        if cfg.repo != req["repository"]:
            raise EvidenceError("scope_mismatch", "Requested repository does not match the selected repository configuration.", "repository", "scope")
        result["coverage"]["status"] = "bounded"
        if req["op"] == "capabilities":
            result["capabilities"] = capabilities()
            result["sources"].append(source("Implemented Factory read capabilities", result["capabilities"]))
            result["coverage"]["status"] = "complete"
        elif req["op"] == "investigate":
            investigate(req, result, deadline)
        else:
            collect_cases(req, result, cfg, deadline)
        if time.monotonic() >= deadline:
            raise EvidenceError("collection_timeout", "Evidence collection exceeded its deadline.")
    except EvidenceError as exc:
        failed(result, exc)
        result["error"] = {"code": exc.code, "message": exc.message}
        exit_code = 2 if exc.code in ("invalid_request", "invalid_scope", "scope_mismatch") else 1
    except config.ConfigError:
        failed(result, EvidenceError("invalid_scope", "Repository configuration is unavailable.", "configuration", "scope"))
        result["error"] = {"code": "invalid_scope", "message": "Repository configuration could not be loaded; raw diagnostics withheld."}
        exit_code = 2
    except Exception:
        failed(result, EvidenceError("collection_unavailable", "Evidence collection failed."))
        result["error"] = {"code": "collection_unavailable", "message": "Evidence collection failed; raw file, configuration and command errors withheld."}
        exit_code = 1
    if not result["ok"]:
        exit_code = exit_code or 1
        result["coverage"]["status"] = "partial" if result["sources"] else "unavailable"
        result.setdefault("error", {"code": "partial_collection", "message": "Some sources were unavailable; usable evidence is retained. See errors for source and scope."})
    if any(item["truncated"] for item in result["sources"]):
        result["coverage"]["notices"].append("One or more cited sources are truncated; omission is not evidence of absence.")
    result["observed_at"] = datetime.now(UTC).isoformat()
    # Structured case labels/titles are also untrusted display text. JSON escaping
    # keeps stdout a single record; per-source bytes were bounded before encoding.
    print(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
