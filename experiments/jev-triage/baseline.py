#!/usr/bin/env python3
"""Replay frozen issue cases through Factory's configured triage implementation.

Usage: python experiments/jev-triage/baseline.py <new-output-directory>
This runner reads no GitHub state and never applies triage decisions.
"""
from __future__ import annotations

import argparse
import copy
import errno
import fcntl
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import sys
import time
import urllib.parse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory import config, triage  # noqa: E402


class ModelUnavailable(Exception):
    def __init__(self, detail: dict, attempted: bool) -> None:
        super().__init__(detail["message"])
        self.detail = detail
        self.attempted = attempted


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def public_endpoint(url: str) -> str:
    """Keep endpoint identity while dropping userinfo, query credentials, and fragments."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))


@contextmanager
def bounded_host_lock(path: Path, timeout: float):
    lock = path.open("a+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"host lock remained busy for {timeout:g}s: {path}"
                    ) from exc
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        yield
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def validate_cases(value: object) -> list[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("cases"), list):
        raise ValueError("cases file must contain a 'cases' list")
    cases = value["cases"]
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError(f"case {index} needs a string id")
        if case.get("split") not in ("development", "evaluation"):
            raise ValueError(f"case {case['id']} has an invalid split")
        if not isinstance(case.get("issue"), dict):
            raise ValueError(f"case {case['id']} needs an issue object")
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay frozen cases through Factory triage")
    parser.add_argument("output", type=Path, help="new directory for immutable run output")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.json")
    parser.add_argument("--lock-timeout", type=float, default=120.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    if args.lock_timeout < 0:
        parser.error("--lock-timeout must be non-negative")

    cases_path = args.cases.resolve()
    cases_bytes = cases_path.read_bytes()
    cases = validate_cases(json.loads(cases_bytes))
    cfg = config.load(ROOT)
    triage.configure(cfg)
    endpoint = public_endpoint(triage.LLM_URL)
    frozen_prompt = triage.system_prompt()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "cases.json").write_bytes(cases_bytes)
    (out / "prompt.txt").write_text(frozen_prompt)

    manifest = {
        "started_at": utc(),
        "status": "running",
        "cases_path": str(cases_path),
        "frozen_cases_file": "cases.json",
        "cases_sha256": sha(cases_bytes),
        "runner_sha256": sha(Path(__file__).read_bytes()),
        "prompt_sha256": sha(frozen_prompt.encode()),
        "model": triage.LLM_MODEL,
        "endpoint": endpoint,
        "host_lock": str(cfg.lock),
        "lock_timeout_seconds": args.lock_timeout,
        "model_unavailable": None,
    }
    write_json(out / "manifest.json", manifest)
    write_json(out / "results.json", [])

    original_call_llm = triage.call_llm
    current_capture: dict | None = None
    outage: dict | None = None

    def capturing_call_llm(messages: list[dict]) -> str:
        nonlocal outage
        assert current_capture is not None
        current_capture["requests"].append(copy.deepcopy(messages))
        if outage is not None:
            raise ModelUnavailable(outage, attempted=False)
        current_capture["network_attempts"] += 1
        try:
            response = original_call_llm(messages)
        except SystemExit as exc:
            if exc.code != 2:
                raise
            cause = exc.__cause__ or exc
            message = str(getattr(cause, "reason", cause)).replace(
                triage.LLM_URL, endpoint
            )
            outage = {
                "cause_type": type(cause).__name__,
                "message": message,
                "observed_at": utc(),
            }
            raise ModelUnavailable(outage, attempted=True) from exc
        current_capture["raw_responses"].append(response)
        return response

    triage.call_llm = capturing_call_llm
    results: list[dict] = []
    try:
        try:
            with bounded_host_lock(cfg.lock, args.lock_timeout):
                for case in cases:
                    current_capture = {
                        "requests": [],
                        "raw_responses": [],
                        "network_attempts": 0,
                    }
                    started = time.monotonic()
                    error = None
                    decision = None
                    try:
                        decision = triage.triage_issue(case["issue"])
                        if decision is None:
                            error = {"type": "unparseable_model_response"}
                    except ModelUnavailable as exc:
                        error = {
                            "type": (
                                "model_unavailable"
                                if exc.attempted
                                else "model_unavailable_not_retried"
                            ),
                            **exc.detail,
                        }
                    except Exception as exc:
                        # Preserve every case outcome without masking later cases.
                        error = {"type": type(exc).__name__, "message": str(exc)}
                    result = {
                        "id": case["id"],
                        "split": case["split"],
                        "issue_number": case["issue"].get("number"),
                        "input_sha256": sha(encoded(case["issue"])),
                        "case_sha256": sha(encoded(case)),
                        "model": triage.LLM_MODEL,
                        "endpoint": endpoint,
                        "seconds": round(time.monotonic() - started, 6),
                        "decision": decision,
                        "error": error,
                        **current_capture,
                    }
                    result["request_sha256"] = [
                        sha(encoded(request)) for request in result["requests"]
                    ]
                    results.append(result)
                    write_json(out / "results.json", results)
        except TimeoutError as exc:
            manifest["status"] = "lock_timeout"
            manifest["error"] = {"type": "lock_timeout", "message": str(exc)}
            return_code = 2
        else:
            manifest["status"] = "model_unavailable" if outage else "complete"
            return_code = 2 if outage else int(any(result["error"] for result in results))
    finally:
        triage.call_llm = original_call_llm
        manifest["completed_at"] = utc()
        manifest["model_unavailable"] = outage
        manifest["case_count"] = len(results)
        manifest["model_network_attempts"] = sum(
            result["network_attempts"] for result in results
        )
        write_json(out / "manifest.json", manifest)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
