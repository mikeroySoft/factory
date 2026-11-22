"""Decode GitHub CLI JSON without silently accepting capped listings."""
import json


def json_output(args: list[str] | tuple[str, ...], output: str) -> object:
    data = json.loads(output)
    # ponytail: bounded lists fail closed; paginate if a repository reaches its limit.
    if len(args) >= 2 and args[0] in {"pr", "issue", "label", "search"} and args[1] in {"list", "prs", "issues"}:
        command = "gh " + " ".join(args[:2])
        if "--limit" not in args:
            raise RuntimeError(f"{command} requires an explicit --limit to detect truncation")
        limit = int(args[args.index("--limit") + 1])
        if not isinstance(data, list):
            raise RuntimeError(f"{command} requires a JSON array to detect truncation")
        if len(data) >= limit:
            raise RuntimeError(
                f"{command} returned {len(data)} results at --limit {limit}; possible truncation. "
                "Increase --limit or paginate until exhausted."
            )
    return data
