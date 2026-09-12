"""Deterministic per-ticket brief: files, recent PRs, triage brief, lessons. No LLM.

Written once to `<worktree>/.factory/brief-<n>.md` before attempt 1 and appended
to every worker prompt as `## Brief`. Evidence is grep and git only.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

MAX_NOUNS = 12
MAX_FILES = 15
MAX_PRS = 3
MAX_CHARS = 6_000  # ~1.5k tokens; the prompt already carries the ticket and lessons

BACKTICK = re.compile(r"`([^`\n]+)`")
TOKEN = re.compile(r"[A-Za-z_][\w./-]*")
IDENT = re.compile(r"[_./]|[a-z][A-Z]")  # snake_case, dotted, paths, CamelCase
PR_NUMBER = re.compile(r"pull request #(\d+)|\(#(\d+)\)")
PUNCT = ".,:;()[]{}\"'/"


def nouns(text: str) -> list[str]:
    """Identifier-like tokens: every backticked word, plus snake/Camel/dotted prose words."""
    quoted = [w.strip(PUNCT) for span in BACKTICK.findall(text) for w in span.split()]
    prose = [w for w in (w.strip(PUNCT) for w in TOKEN.findall(BACKTICK.sub(" ", text))) if IDENT.search(w)]
    found: list[str] = []
    for word in quoted + prose:
        if len(word) >= 3 and not word.startswith("http") and word not in found:
            found.append(word)
    return found[:MAX_NOUNS]


def git(cwd: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return [line for line in proc.stdout.splitlines() if line] if proc.returncode == 0 else []


def files_for(cwd: Path, words: list[str]) -> list[str]:
    """Tracked files ranked by how many nouns they mention or once introduced."""
    score: dict[str, int] = {}
    for word in words:
        hits = set(git(cwd, "grep", "-lIw", "-F", "-e", word))
        # ponytail: -S walks full history per noun; bound with -n if repos grow large.
        hits |= set(git(cwd, "log", "-S", word, "-n", "20", "--name-only", "--format="))
        for path in hits:
            score[path] = score.get(path, 0) + 1
    ranked = sorted(score, key=lambda p: (-score[p], p))
    return ranked[:MAX_FILES]


def prs_for(cwd: Path, paths: list[str]) -> list[str]:
    """Last merged PRs touching paths, from first-parent subjects of the base branch."""
    seen: list[str] = []
    for subject in git(cwd, "log", "--first-parent", "-n", "100", "--format=%s", "--", *paths):
        match = PR_NUMBER.search(subject)
        if not match:
            continue
        number = match.group(1) or match.group(2)
        if all(not line.startswith(f"#{number} ") for line in seen):
            seen.append(f"#{number} {subject}")
        if len(seen) == MAX_PRS:
            break
    return seen


def triage_brief(comments: list[dict]) -> str:
    for comment in comments:
        body = comment.get("body") or ""
        if "Agent brief:" in body:
            return body.split("Agent brief:", 1)[1].strip()
    return ""


def matching_lessons(lessons: str, words: list[str]) -> list[str]:
    lowered = [w.lower() for w in words]
    return [line for line in lessons.splitlines()
            if line.startswith("- ") and any(w in line.lower() for w in lowered)]


def compose(cwd: Path, issue: dict, lessons: str) -> str:
    """Brief markdown, or "" when nothing matched."""
    words = nouns(f"{issue.get('title', '')}\n{issue.get('body') or ''}")
    paths = files_for(cwd, words) if words else []
    sections = []
    if paths:
        sections.append("### Files mentioning ticket terms\n" + "\n".join(f"- {p}" for p in paths))
        prs = prs_for(cwd, paths)
        if prs:
            sections.append("### Last merged PRs touching them\n" + "\n".join(f"- {p}" for p in prs))
    triage = triage_brief(issue.get("comments") or [])
    if triage:
        sections.append("### Triage brief\n" + triage)
    hits = matching_lessons(lessons, words) if words else []
    if hits:
        sections.append("### Matching lessons\n" + "\n".join(hits))
    return "\n\n".join(sections)[:MAX_CHARS]


def ensure(path: Path, cwd: Path, issue: dict, lessons: str) -> str:
    """Write the brief once; later attempts reuse it. Returns its text ("" = none)."""
    if path.exists():
        return path.read_text()
    text = compose(cwd, issue, lessons)
    if text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return text
