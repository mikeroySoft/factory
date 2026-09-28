"""Read-only Jev replay. python experiments/jev-triage/run.py smoke|replay OUT

Uses public frozen inputs, never GitHub mutation. Paid calls reserve budget before
sending and never retry automatically. .env is parsed as data, never executed.
"""
import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shlex
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))
from factory.triage import deterministic_needs_info  # noqa: E402

RATE = 0.042 / 1_000_000
RESERVE_TOKENS = 1_000_000
MAX_CALLS = 41
MODEL = "jev-latest"


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key():
    values = []
    for line in (REPO / ".env").read_text().splitlines():
        if line.strip().startswith(("TYPESAFE_API_KEY=", "export TYPESAFE_API_KEY=")):
            parts = shlex.split(line.split("=", 1)[1], comments=True)
            if len(parts) == 1:
                values.append(parts[0])
    if len(values) != 1 or not values[0]:
        raise ValueError("Expected exactly one nonempty TYPESAFE_API_KEY assignment")
    return values[0]


def validate(response, criteria):
    answer = response["answers"]["route"]
    probs = answer["probabilities"]
    if answer["type"] != "choice" or answer["choice"] not in criteria:
        raise ValueError("Unexpected answer type or choice")
    if set(probs) != set(criteria):
        raise ValueError("Unexpected probability keys")
    numbers = [*probs.values(), answer["confidence"]]
    if any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in numbers):
        raise ValueError("Invalid probabilities or confidence")
    if abs(sum(probs.values()) - 1) > 0.001:
        raise ValueError("Probabilities do not sum to one")
    usage = response["usage"]
    if type(usage.get("input_tokens")) is not int or not 0 <= usage["input_tokens"] <= RESERVE_TOKENS:
        raise ValueError("Input usage absent or exceeds reservation; stop")
    if type(usage.get("output_tokens")) is not int or usage["output_tokens"] < 0:
        raise ValueError("Invalid output usage")
    if not isinstance(response.get("model"), str) or not response["model"]:
        raise ValueError("Missing model identity")
    return answer


def call(payload, out, ident, secret):
    data = json.dumps(payload, ensure_ascii=False).encode()
    if len(data) > 100_000:
        raise ValueError("Request exceeds frozen byte cap; do not truncate evidence")
    # Persistent reservation includes failed requests whose billing is unknown.
    ledger_path = ROOT / "attempts.jsonl"
    with ledger_path.open("a+") as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        ledger.seek(0)
        entries = [json.loads(line) for line in ledger if line.strip()]
        if len(entries) >= MAX_CALLS or (len(entries) + 1) * RESERVE_TOKENS * RATE > 5:
            raise ValueError("Experiment budget exhausted")
        entry = {"id": ident, "out": str(out.relative_to(ROOT)), "timestamp": time.time(),
                 "reserved_usd": RESERVE_TOKENS * RATE, "request_sha256": hashlib.sha256(data).hexdigest()}
        ledger.write(json.dumps(entry) + "\n")
        ledger.flush()
        import os
        os.fsync(ledger.fileno())
    save(out / f"{ident}.request.json", payload)
    request = urllib.request.Request("https://api.typesafe.ai/v1/systemone", data=data,
                                    headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    started = time.perf_counter()
    result = {"id": ident, "request_sha256": entry["request_sha256"], "ok": False}
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
        (out / f"{ident}.response.json").write_text(raw + "\n")
        parsed = json.loads(raw)
        answer = validate(parsed, payload["questions"]["route"]["criteria"])
        result.update(ok=True, answer=answer, model=parsed["model"], usage=parsed["usage"],
                      estimated_usd=parsed["usage"]["input_tokens"] * RATE)
    except urllib.error.HTTPError as exc:
        result["error"] = {"type": "HTTPError", "status": exc.code}
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        result["error"] = {"type": type(exc).__name__}
    result["seconds"] = time.perf_counter() - started
    save(out / f"{ident}.result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["smoke", "replay"])
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.relative_to(ROOT)
    secret = key()
    questions = json.loads((ROOT / "questions.json").read_text())
    paths = [ROOT / "PROTOCOL.md", ROOT / "questions.json", Path(__file__)]
    if args.mode == "smoke":
        cases = [{"id": "smoke", "split": "synthetic", "issue": {"number": 0,
                  "title": "Fix help output spelling", "body": "Correct the misspelled word in the existing CLI help text without changing behavior. Acceptance: invoking the CLI with --help displays the corrected spelling and exits zero.", "comments": []}}]
    else:
        paths.append(ROOT / "cases.json")
        cases = json.loads((ROOT / "cases.json").read_text())["cases"]
        if len(cases) != 40 or len({c["id"] for c in cases}) != 40:
            raise ValueError("Expected 40 unique frozen cases")
        cases = sorted(cases, key=lambda c: (c["split"] != "development", c["id"]))
    out.mkdir(parents=True, exist_ok=False)
    save(out / "manifest.json", {"mode": args.mode, "model": MODEL, "timestamp": time.time(),
         "sha256": {p.name: digest(p) for p in paths}, "price_per_input_token": RATE,
         "max_calls": MAX_CALLS, "reserved_tokens_per_call": RESERVE_TOKENS})
    results = []
    for case in cases:
        ident = case["id"]
        if not ident.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Unsafe case identifier")
        issue = case["issue"]
        comments = "\n\n".join(f"Comment by {c.get('author', {}).get('login', '?')}:\n{c.get('body', '')}" for c in issue.get("comments", []))
        lint = deterministic_needs_info(issue.get("body") or "", comments)
        if lint:
            result = {"id": ident, "ok": True, "deterministic": True, "decision": "needs-info", "question": lint}
            save(out / f"{ident}.result.json", result)
        else:
            result = call({"model": MODEL, "state": issue, "questions": questions}, out, ident, secret)
        result["split"] = case["split"]
        results.append(result)
        save(out / "results.json", results)
        print(f"{ident}: {'ok' if result['ok'] else 'failed'}", flush=True)
        if not result["ok"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
