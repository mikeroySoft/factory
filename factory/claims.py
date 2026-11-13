"""Repository-independent host-claims capability probe."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--supported"]:
        print("usage: factory claims --supported", file=sys.stderr)
        return 2
    print("host-claims 1")
    return 0
