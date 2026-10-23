"""Prepare and execute the frozen Jev coverage replay.

Usage:
  python experiments/jev-review-coverage/run.py prepare
  python experiments/jev-review-coverage/run.py execute

`prepare` is offline. `execute` makes paid calls, at most once per case/repeat,
without retries. Source issue and diff text is untrusted model state.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import math
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CORPUS = REPO / "experiments" / "reviewer-calibration"
RUN = ROOT / "runs" / "main"
ATTEMPTS = ROOT / "attempts.jsonl"
MODEL = "jev-latest"
REPEATS = 3
MAX_REQUEST_BYTES = 100_000
MAX_CALLS = 33
MAX_SECONDS = 45 * 60
MAX_SPEND_USD = 0.25
RESERVED_INPUT_TOKENS = 100_000
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def _heading(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
    if match:
        return len(match.group(1)), match.group(2).strip(" *")
    match = re.match(r"^\*\*(.+?)\*\*\s*$", line)
    return (2, match.group(1).strip()) if match else None


def _selected_heading(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title.lower())
    return normalized == "scope" or normalized.endswith("/ scope") or "exit gate" in normalized or "acceptance criteria" in normalized


def _split_prose(text: str) -> list[str]:
    return [part.strip() for part in re.split(r";\s+|(?<=[.!?])\s+(?=[A-Z`])", text) if part.strip()]


def _criteria_from_section(title: str, level: int, lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if line.strip().lower().rstrip(":") == "after this change":
            lines = lines[index + 1:]
            break
    units: list[tuple[str, str, bool]] = []
    context = title
    current: list[str] = []
    current_list = False
    fenced = False

    def flush() -> None:
        nonlocal current, current_list
        if not current:
            return
        text = " ".join(part.strip() for part in current).strip()
        if text and not text.endswith(":"):
            units.append((context, text, current_list))
        current = []
        current_list = False

    for line in lines:
        if line.strip().startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if line.strip().lower().startswith("blocked by:"):
            flush()
            continue
        heading = _heading(line)
        if heading:
            flush()
            if heading[0] > level:
                context = heading[1]
            continue
        item = re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$", line)
        if item:
            flush()
            current = [re.sub(r"^\[[ xX]\]\s*", "", item.group(1))]
            current_list = True
        elif not line.strip():
            flush()
        else:
            current.append(line.strip())
    flush()

    out: list[str] = []
    for context, text, is_list in units:
        parts = [text] if is_list else _split_prose(text)
        for part in parts:
            prefix = "" if context.lower() in {"scope", "what to build / scope"} else f"{context} — "
            out.append(prefix + part)
    return out


def parse_acceptance_criteria(issue: str) -> list[dict[str, str]]:
    """Extract declared Scope/Acceptance/Exit criteria before issue comments."""
    issue = re.split(r"(?m)^##+\s+Comment by\b", issue, maxsplit=1)[0]
    lines = issue.splitlines()
    sections: list[tuple[str, int, list[str]]] = []
    active: tuple[str, int, list[str]] | None = None
    for line in lines:
        heading = _heading(line)
        if heading and active and heading[0] <= active[1]:
            sections.append(active)
            active = None
        if heading and _selected_heading(heading[1]):
            if active:
                sections.append(active)
            active = (heading[1], heading[0], [])
        elif active:
            active[2].append(line)
    if active:
        sections.append(active)
    texts: list[str] = []
    for title, level, body in sections:
        texts.extend(_criteria_from_section(title, level, body))
    deduped = list(dict.fromkeys(texts))
    if not deduped:
        raise ValueError("No acceptance criteria found")
    return [{"id": f"c{index:03d}", "text": text} for index, text in enumerate(deduped, 1)]


def split_diff(patch: str) -> list[dict[str, str]]:
    """Return each changed file's hunk headers and +/- lines, omitting context."""
    files: list[dict[str, str]] = []
    current: dict[str, object] | None = None
    in_hunk = False
    for line in patch.splitlines():
        match = re.match(r"^diff --git a/(.+) b/(.+)$", line)
        if match:
            if current:
                current["hunks"] = "\n".join(current.pop("lines"))
                files.append(current)  # type: ignore[arg-type]
            current = {"path": match.group(2), "lines": []}
            in_hunk = False
        elif current and line.startswith("@@"):
            current["lines"].append(line)  # type: ignore[union-attr]
            in_hunk = True
        elif current and in_hunk and line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            current["lines"].append(line)  # type: ignore[union-attr]
    if current:
        current["hunks"] = "\n".join(current.pop("lines"))
        files.append(current)  # type: ignore[arg-type]
    if not files or any(not item["hunks"] for item in files):
        raise ValueError("Diff has a file without changed hunk lines")
    return files


def build_request(criteria: list[dict[str, str]], files: list[dict[str, str]]) -> dict:
    questions = {}
    for criterion_index, criterion in enumerate(criteria):
        for file_index in range(len(files)):
            questions[f"{criterion['id']}__f{file_index:03d}"] = {
                "type": "noul",
                "instructions": (
                    f"Does `files[{file_index}].hunks` by itself show that "
                    f"`criteria[{criterion_index}].text` is satisfied by this change?"
                ),
            }
        questions[f"{criterion['id']}__whole"] = {
            "type": "noul",
            "instructions": (
                f"Is `criteria[{criterion_index}].text` satisfied by the diff as a whole? "
                "Judge only the changed evidence in `files`; missing evidence means no."
            ),
        }
    return {"model": MODEL, "state": {"criteria": criteria, "files": files}, "questions": questions}


def wire_bytes(request: dict) -> bytes:
    return gzip.compress(json_bytes(request), mtime=0)


def cases_from_splits() -> list[str]:
    splits = json.loads((CORPUS / "splits.json").read_text())["splits"]
    return [case for split in ("train", "lockbox") for case in splits[split]]


def split_of(case: str) -> str:
    splits = json.loads((CORPUS / "splits.json").read_text())["splits"]
    return next(split for split, cases in splits.items() if case in cases)


def request_for_case(case: str) -> dict:
    source = CORPUS / "cases" / case / "sources"
    criteria = parse_acceptance_criteria((source / "issue.md").read_text())
    files = split_diff((source / "diff.patch").read_text())
    return build_request(criteria, files)


def _mapping() -> dict:
    return json.loads((ROOT / "adjudication.json").read_text())


def _validate_mapping() -> None:
    mapping = _mapping()["cases"]
    if set(mapping) != set(cases_from_splits()):
        raise ValueError("Adjudication cases do not match frozen splits")
    for case in cases_from_splits():
        criteria = request_for_case(case)["state"]["criteria"]
        criterion_ids = {item["id"] for item in criteria}
        record = mapping[case]
        if record["criteria_sha256"] != sha(json_bytes(criteria)):
            raise ValueError(f"Adjudication criteria hash does not match {case}")
        if not set(record["defects"]) <= criterion_ids:
            raise ValueError(f"Adjudication names an unknown criterion for {case}")
        oracle = json.loads((CORPUS / "cases" / case / "oracle.json").read_text())
        defect_ids = {finding["id"] for finding in oracle["findings"]}
        mapped = {defect for defects in record["defects"].values() for defect in defects}
        if not mapped <= defect_ids:
            raise ValueError(f"Adjudication names an unknown defect for {case}")


def prepare() -> int:
    if RUN.exists():
        raise FileExistsError(f"Run already exists: {RUN}")
    _validate_mapping()
    (RUN / "raw").mkdir(parents=True)
    jobs = []
    for sample in range(1, REPEATS + 1):
        for case in cases_from_splits():
            ident = f"{case}.s{sample}"
            request = request_for_case(case)
            raw = json_bytes(request)
            wire = wire_bytes(request)
            if len(wire) > MAX_REQUEST_BYTES:
                raise ValueError(f"{ident} request is {len(wire)} bytes, above cap")
            (RUN / "raw" / f"{ident}.request.json").write_bytes(raw)
            jobs.append({"id": ident, "case": case, "sample": sample, "split": split_of(case),
                         "request_sha256": sha(raw), "request_json_bytes": len(raw),
                         "request_wire_bytes": len(wire), "question_count": len(request["questions"])})
    source_paths = [ROOT / "PROTOCOL.md", ROOT / "adjudication.json", Path(__file__), ROOT / "score.py",
                    CORPUS / "splits.json", CORPUS / "runs" / "rerun-20261016" / "adjudication.json"]
    for case in cases_from_splits():
        source_paths.extend([CORPUS / "cases" / case / "sources" / "issue.md",
                             CORPUS / "cases" / case / "sources" / "diff.patch",
                             CORPUS / "cases" / case / "oracle.json"])
    manifest = {
        "schema_version": 1, "experiment": "jev-review-coverage", "run_id": "main",
        "prepared_at": now(), "model": MODEL, "repeats": REPEATS, "jobs": jobs,
        "limits": {"max_calls": MAX_CALLS, "max_request_bytes": MAX_REQUEST_BYTES,
                   "reserved_input_tokens_per_call": RESERVED_INPUT_TOKENS,
                   "max_spend_usd": MAX_SPEND_USD, "max_seconds": MAX_SECONDS, "retries": 0},
        "price_per_input_token_usd": PRICE_PER_INPUT_TOKEN,
        "source_sha256": {str(path.relative_to(REPO)): sha(path.read_bytes()) for path in source_paths},
    }
    save_json(RUN / "manifest.json", manifest)
    print(json.dumps({"run": str(RUN), "jobs": len(jobs),
                      "max_wire_bytes": max(job["request_wire_bytes"] for job in jobs)}))
    return 0


def _key() -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    values = []
    env_files = (REPO / ".env", Path.home() / "dev" / "mikeroysoft" / "factory" / ".env")
    env_file = next((path for path in env_files if path.is_file()), None)
    if env_file is None:
        raise ValueError("TYPESAFE_API_KEY is unset and no repository .env was found")
    for line in env_file.read_text().splitlines():
        if line.strip().startswith(("TYPESAFE_API_KEY=", "export TYPESAFE_API_KEY=")):
            parsed = shlex.split(line.split("=", 1)[1], comments=True)
            if len(parsed) == 1:
                values.append(parsed[0])
    if len(values) != 1 or not values[0]:
        raise ValueError("Expected exactly one TYPESAFE_API_KEY")
    return values[0]


def _reserve(job: dict) -> float:
    ATTEMPTS.touch(exist_ok=True)
    with ATTEMPTS.open("r+") as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        entries = [json.loads(line) for line in ledger if line.strip()]
        if any(entry["id"] == job["id"] for entry in entries):
            raise ValueError(f"Paid request already attempted; no retry allowed: {job['id']}")
        if len(entries) >= MAX_CALLS:
            raise ValueError("Paid call cap exhausted")
        reservation = (len(entries) + 1) * RESERVED_INPUT_TOKENS * PRICE_PER_INPUT_TOKEN
        if reservation > MAX_SPEND_USD:
            raise ValueError("Spend reservation exceeds cap")
        started = entries[0]["run_started_epoch"] if entries else time.time()
        if time.time() - started > MAX_SECONDS:
            raise ValueError("Elapsed-time cap exceeded")
        entry = {"id": job["id"], "attempted_at": now(), "run_started_epoch": started,
                 "reserved_usd": RESERVED_INPUT_TOKENS * PRICE_PER_INPUT_TOKEN,
                 "request_sha256": job["request_sha256"]}
        ledger.seek(0, 2)
        ledger.write(json.dumps(entry) + "\n")
        ledger.flush()
        os.fsync(ledger.fileno())
        return started


def _validate_response(response: dict, questions: set[str]) -> dict[str, float]:
    if not isinstance(response.get("model"), str) or not response["model"]:
        raise ValueError("Missing resolved model")
    answers = response.get("answers")
    if not isinstance(answers, dict) or set(answers) != questions:
        raise ValueError("Response answer keys do not match request")
    values = {}
    for key, answer in answers.items():
        value = answer.get("noul")
        if answer.get("type") != "noul" or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"Invalid Noul answer: {key}")
        values[key] = value
    usage = response.get("usage", {})
    if type(usage.get("input_tokens")) is not int or not 0 <= usage["input_tokens"] <= RESERVED_INPUT_TOKENS:
        raise ValueError("Input usage absent or over reservation")
    if type(usage.get("output_tokens")) is not int or usage["output_tokens"] < 0:
        raise ValueError("Invalid output usage")
    return values


def _call(job: dict, secret: str) -> dict:
    request_path = RUN / "raw" / f"{job['id']}.request.json"
    raw = request_path.read_bytes()
    if sha(raw) != job["request_sha256"]:
        raise ValueError(f"Request hash changed: {job['id']}")
    payload = json.loads(raw)
    wire = gzip.compress(raw, mtime=0)
    if len(wire) != job["request_wire_bytes"] or len(wire) > MAX_REQUEST_BYTES:
        raise ValueError(f"Request wire bytes changed: {job['id']}")
    _reserve(job)
    request = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone", data=wire,
        headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json", "Content-Encoding": "gzip"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            response_raw = response.read()
        (RUN / "raw" / f"{job['id']}.response.json").write_bytes(response_raw)
        parsed = json.loads(response_raw)
        answers = _validate_response(parsed, set(payload["questions"]))
        usage = parsed["usage"]
        result = {**{key: job[key] for key in ("id", "case", "sample", "split")}, "ok": True,
                  "model": parsed["model"], "usage": usage, "answers": answers,
                  "estimated_usd": usage["input_tokens"] * PRICE_PER_INPUT_TOKEN,
                  "seconds": round(time.perf_counter() - started, 3),
                  "request_sha256": job["request_sha256"], "response_sha256": sha(response_raw),
                  "request_wire_bytes": len(wire)}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        (RUN / "raw" / f"{job['id']}.response.json").write_bytes(body)
        result = {**{key: job[key] for key in ("id", "case", "sample", "split")}, "ok": False,
                  "error": {"type": "HTTPError", "status": exc.code},
                  "seconds": round(time.perf_counter() - started, 3)}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        result = {**{key: job[key] for key in ("id", "case", "sample", "split")}, "ok": False,
                  "error": {"type": type(exc).__name__, "message": str(exc)},
                  "seconds": round(time.perf_counter() - started, 3)}
    save_json(RUN / "raw" / f"{job['id']}.result.json", result)
    return result


def execute() -> int:
    manifest = json.loads((RUN / "manifest.json").read_text())
    if manifest["model"] != MODEL or len(manifest["jobs"]) != MAX_CALLS:
        raise ValueError("Unexpected manifest")
    expected_sources = manifest["source_sha256"]
    for relative, expected in expected_sources.items():
        if sha((REPO / relative).read_bytes()) != expected:
            raise ValueError(f"Frozen source changed: {relative}")
    existing = {row["id"]: row for row in json.loads((RUN / "results.json").read_text())} if (RUN / "results.json").exists() else {}
    if any(not row.get("ok") for row in existing.values()):
        raise ValueError("A prior paid attempt failed; retries are forbidden")
    secret = _key()
    results = list(existing.values())
    for job in manifest["jobs"]:
        if job["id"] in existing:
            continue
        result = _call(job, secret)
        results.append(result)
        save_json(RUN / "results.json", results)
        print(f"{job['id']}: {'ok' if result['ok'] else 'failed'} {result['seconds']}s", flush=True)
        if not result["ok"]:
            return 1
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "execute"}:
        raise SystemExit(__doc__)
    return prepare() if sys.argv[1] == "prepare" else execute()


if __name__ == "__main__":
    raise SystemExit(main())
