"""`factory mcp`: stdio MCP server exposing registered factories' evidence reads.

A thin adapter over `factory evidence`: newline-delimited JSON-RPC 2.0 on
stdin/stdout, one evidence subprocess per tool call, no state. Stdout carries
only MCP messages; diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from factory import __version__, config, evidence

PROTOCOL = "2025-06-18"
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602

REPOSITORY = {"type": "string", "maxLength": 200, "pattern": "^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$",
              "description": "Registered owner/name slug; see factory_repositories."}
IDENTIFIER = {"type": "integer", "minimum": 1, "maximum": 2**63 - 1}
FIELDS = {
    "number": IDENTIFIER,
    "run_id": IDENTIFIER,
    "path": {"type": "string", "minLength": 1, "maxLength": 1024, "description": "Relative repository file path."},
    "ref": {"type": "string", "minLength": 1, "maxLength": 255, "description": "Explicit branch, tag or commit."},
    "head": {"type": "string", "pattern": "^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$"},
    "offset": {"type": "integer", "minimum": 0, "maximum": evidence.RESULT_BYTES},
    "experiment": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
    "run": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"},
}


def _schema(**fields: dict) -> dict:
    return {"type": "object", "properties": {"repository": REPOSITORY, **fields},
            "required": ["repository", *fields], "additionalProperties": False}


def _investigate_schema() -> dict:
    alternatives = [
        _schema(kind={"const": kind}, **{name: FIELDS[name] for name in names})
        for kind, names in evidence.INVESTIGATION_FIELDS.items()
    ]
    return {"type": "object",
            "properties": {"repository": REPOSITORY, "kind": {"type": "string", "enum": list(evidence.INVESTIGATION_FIELDS)}, **FIELDS},
            "required": ["repository", "kind"], "oneOf": alternatives}


TOOLS = [
    {"name": "factory_repositories", "description": "List repositories registered in this host's factory config with their main checkouts.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "factory_observe", "description": "Compact Factory-selected cases and nullable attention count for one registered repository.",
     "inputSchema": _schema()},
    {"name": "factory_inspect", "description": "One ticket's selected evidence: case summary, decisions, runtime history.",
     "inputSchema": _schema(number=IDENTIFIER)},
    {"name": "factory_investigate", "description": "One bounded investigation by kind: workflows, file, result, pr, checks, runs, run, log, roadmap, initiative, drift, experiments, experiment.",
     "inputSchema": _investigate_schema()},
    {"name": "factory_capabilities", "description": "Implemented evidence reads and their fields for one registered repository.",
     "inputSchema": _schema()},
]
OPS = {"factory_observe": "observe", "factory_inspect": "inspect",
       "factory_investigate": "investigate", "factory_capabilities": "capabilities"}


def registry() -> dict[str, str]:
    """Slug → absolute main checkout from `[repo."owner/name"] path`; read once."""
    repos = config.host_config().get("repo", {})
    if not isinstance(repos, dict):
        raise config.ConfigError("host config [repo] must be a table")
    out = {}
    for slug, table in repos.items():
        if not evidence.valid_repository(slug) or not isinstance(table, dict) or "path" not in table:
            continue
        path = table["path"]
        if not isinstance(path, str) or not path or not os.path.isabs(path):
            raise config.ConfigError(f'[repo."{slug}"] path must be a nonempty absolute string')
        out[slug] = path
    return dict(sorted(out.items()))


def _text(text: str, *, error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def run_evidence(root: str, request: dict) -> dict:
    payload = json.dumps(request, separators=(",", ":")).encode()
    try:
        proc = subprocess.run([sys.executable, "-P", "-m", "factory", "evidence", "--root", root],
                              input=payload, capture_output=True, timeout=2 * evidence.READ_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return _text("Evidence subprocess could not be launched or did not finish.", error=True)
    out = proc.stdout
    if proc.returncode not in (0, 1, 2):
        return _text(f"Evidence subprocess ended abnormally (status {proc.returncode}) without a result.", error=True)
    if len(out) > evidence.RESPONSE_CAP + 1:
        return _text("Evidence subprocess produced an oversized result.", error=True)
    try:
        text = out.decode("utf-8")
        if not isinstance(json.loads(text), dict):
            raise ValueError
    except ValueError:
        return _text("Evidence subprocess produced no valid JSON result.", error=True)
    return _text(text, error=proc.returncode == 2)


def call_tool(name: str, arguments: dict, repos: dict[str, str]) -> dict:
    if name == "factory_repositories":
        if arguments:
            return _text("factory_repositories takes no arguments.", error=True)
        rows = [{"repository": slug, "path": path} for slug, path in repos.items()]
        return _text(json.dumps({"repositories": rows}, separators=(",", ":")))
    for key in ("schema_version", "op"):
        if key in arguments:
            return _text(f"{key} is fixed by the server; remove it from arguments.", error=True)
    slug = arguments.get("repository")
    if not isinstance(slug, str) or slug not in repos:
        return _text("repository must be a registered owner/name slug; call factory_repositories.", error=True)
    return run_evidence(repos[slug], {"schema_version": 1, "op": OPS[name], **arguments})


def _error(id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def handle(line: bytes, repos: dict[str, str]) -> dict | None:
    """One response per request; None for a valid notification."""
    try:
        message = json.loads(line)
    except (ValueError, RecursionError):
        return _error(None, PARSE_ERROR, "Parse error")
    valid = isinstance(message, dict)
    id = message.get("id") if valid and type(message.get("id")) in (int, str) else None
    if not valid or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str) or ("id" in message and id is None):
        return _error(id, INVALID_REQUEST, "Invalid request")
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return _error(id, INVALID_PARAMS, "params must be an object")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": id, "result": {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                                                       "serverInfo": {"name": "factory", "version": __version__}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(id, INVALID_PARAMS, "tools/call needs a string name and object arguments")
        if name != "factory_repositories" and name not in OPS:
            return _error(id, INVALID_PARAMS, f"Unknown tool {name!r}")
        return {"jsonrpc": "2.0", "id": id, "result": call_tool(name, arguments, repos)}
    return _error(id, METHOD_NOT_FOUND, f"Unknown method {method!r}")


def serve(repos: dict[str, str]) -> None:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        response = handle(line, repos)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        print("usage: factory mcp\n\nServe registered repositories' evidence reads over stdio MCP (2025-06-18).", file=sys.stderr)
        return 0 if argv in (["--help"], ["-h"]) else 2
    try:
        repos = registry()
    except (config.ConfigError, OSError) as exc:
        print(f"factory mcp: {exc}", file=sys.stderr)
        return 2
    serve(repos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
