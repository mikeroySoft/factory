"""Repository-independent host-claims capability and setup checks."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from datetime import datetime


class HostSetupError(RuntimeError):
    """Repository host evidence could not be read safely."""


_CLAIM = re.compile(r"Factory claim: host=(\S+) nonce=(\S+) at=(\S+)")


def _valid_claim(comment: dict) -> bool:
    body = comment.get("body")
    if not isinstance(body, str):
        return False
    match = _CLAIM.fullmatch(body.partition("\n")[0])
    if match is None:
        return False
    try:
        timestamp = datetime.fromisoformat(match[3])
        return timestamp.tzinfo is not None and timestamp.utcoffset() is not None
    except ValueError:
        return False


def host_setup_problem(
    host_name: str | None,
    login: str,
    labels: list[dict],
    comments: list[dict],
) -> str | None:
    """Return the unsafe repository host setup, if any."""
    expected = login.casefold()
    claims = [
        comment for comment in comments if _valid_claim(comment) and (
            comment.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")
            or (expected and (comment.get("user") or {}).get("login", "").casefold() == expected)
        )
    ]
    host_labels = [
        label for label in labels
        if isinstance(label.get("name"), str) and label["name"].startswith("host:")
    ]
    if host_name is None and (host_labels or claims):
        return "host_name is required because the repository contains host claims"
    for comment in claims:
        user = comment.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        if not isinstance(author, str) or not author:
            return "Factory claim is missing its author login"
        if author.casefold() != expected:
            return f"Factory claim author login {author} does not match dispatcher login {login or '?'}"
    return None


def _flatten_pages(value: object, source: str) -> list[dict]:
    if not isinstance(value, list):
        raise HostSetupError(f"could not parse {source}: expected paginated JSON")
    rows: list[dict] = []
    for page in value:
        if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
            raise HostSetupError(f"could not parse {source}: expected paginated JSON objects")
        rows.extend(page)
    return rows


def repository_host_problem(
    repo: str,
    host_name: str | None,
    gh_json: Callable[[list[str]], object],
) -> str | None:
    """Read all repository host evidence and return an unsafe setup."""
    def read(endpoint: str) -> list[dict]:
        try:
            value = gh_json(["api", "--paginate", "--slurp", endpoint])
        except Exception as exc:
            raise HostSetupError(f"could not read {endpoint}: {exc}") from exc
        return _flatten_pages(value, endpoint)

    labels = read(f"repos/{repo}/labels?per_page=100")
    comments = read(f"repos/{repo}/issues/comments?per_page=100")
    login = ""
    if any(_valid_claim(comment) for comment in comments):
        try:
            user = gh_json(["api", "user"])
        except Exception as exc:
            raise HostSetupError(f"could not read dispatcher GitHub login: {exc}") from exc
        login = user.get("login") if isinstance(user, dict) else ""
        if not isinstance(login, str) or not login:
            raise HostSetupError("could not parse dispatcher GitHub login")
    return host_setup_problem(host_name, login, labels, comments)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--supported"]:
        print("usage: factory claims --supported", file=sys.stderr)
        return 2
    print("host-claims 1")
    return 0
