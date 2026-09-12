#!/usr/bin/env python3
"""C0 read-only boundary smoke. Real evidence, no model calls or mutations."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import evidence  # The compatibility entry point selects the production source owner.
from factory import briefing

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, default=HERE.parents[1])
ROOT = parser.parse_args().root.resolve()
REPO = "mikeroySoft/factory"
BASE = {"schema_version": 1, "repository": REPO}


def observe_files():
    # Ignore continuously appended Factory activity; protect code/config bytes.
    paths = [ROOT / ".factory.toml", ROOT / "factory/cli.py", ROOT / "factory/dashboard.py", ROOT / "factory/briefing.py"]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def bridge(op, root=ROOT, **fields):
    run = subprocess.run([sys.executable, "-B", str(HERE / "evidence.py"), "--root", str(root)],
                         input=json.dumps({**BASE, "op": op, **fields}), text=True,
                         capture_output=True, timeout=100, check=False)
    result = json.loads(run.stdout)
    assert run.returncode in (0, 1, 2), (run.returncode, run.stderr)
    assert (run.returncode == 0) == result["ok"], "CLI exit contradicts JSON result"
    return result




def main():
    before = observe_files()
    first, second = bridge("observe"), bridge("observe")
    assert first["cases"] and second["cases"], "Real case evidence unavailable; inspect gh/repository prerequisites"
    for result in (first, second):
        if not result["ok"]:
            assert result["coverage"]["status"] == "partial" and result["errors"]
            print("Live observation partial:", json.dumps(result["errors"]))
    assert first["observation_id"] != second["observation_id"]
    assert first["observed_at"] < second["observed_at"]
    assert first["scope"] == second["scope"]
    assert "opening_case" not in first and "case" not in first
    assert first["attention_count"] is None or first["attention_count"] == sum(case["stage"] in ("escalated", "needs-info") for case in first["cases"])
    assert first["cases"], "No real case available for the explicit-inspect smoke"
    case = bridge("inspect", number=first["cases"][0]["number"])
    assert case["case"] and case["case"]["number"] == first["cases"][0]["number"]
    assert any(s.get("url") == case["case"]["url"] for s in case["sources"])
    assert case["sources"] and all(s["id"].startswith("S") and s["text"] for s in case["sources"])
    missing = bridge("inspect", number=2147483647)
    assert not missing["ok"] and missing["case"] is None
    assert missing["error"]["code"] in ("unknown_case", "evidence_unavailable")
    assert bridge("capabilities", repository="mikeroySoft/district")["error"]["code"] == "scope_mismatch"
    invalid = [
        {"op": "observe", "approved": True}, {"op": "inspect", "number": True},
        {"op": "capabilities", "number": 1}, {"op": "investigate", "kind": "shell"},
        {"op": "investigate", "kind": "run", "run_id": 0},
        {"op": "investigate", "kind": "log", "run_id": True},
        {"op": "investigate", "kind": "runs", "number": "1"},
        {"op": "investigate", "kind": "checks", "number": 1, "url": "https://other.example"},
        {"op": "observe", "repository": "owner/.."},
        {"op": "investigate", "kind": "workflows", "number": 1},
    ]
    invalid.extend({"op": "investigate", "kind": "file", "path": path, "ref": "main"}
                   for path in ("/etc/passwd", "../secret", "a/../secret", "a//b", "https://other.example/file", "a%2fb", "a?ref=elsewhere"))
    invalid.extend({"op": "investigate", "kind": "file", "path": "README.md", "ref": ref}
                   for ref in ("", "--hostname=other.example", "https://other.example", "main?ref=elsewhere", "../main"))
    for fields in invalid:
        assert bridge(**fields)["error"]["code"] == "invalid_request", fields
    caps = bridge("capabilities")
    assert caps["ok"] and caps["capabilities"]["actions"] == []
    assert {read.get("kind") for read in caps["capabilities"]["reads"] if read["op"] == "investigate"} == {"workflows", "file", "pr", "checks", "runs", "run", "log"}
    workflows = bridge("investigate", kind="workflows")
    assert workflows["ok"] and not workflows["sources"][0]["truncated"], "Workflow discovery unavailable or too large for this live smoke"
    paths = [item["path"] for item in json.loads(workflows["sources"][0]["text"])["workflows"] if item["path"].startswith(".github/workflows/")]
    assert paths, "No registered workflow file available for this live smoke"
    workflow_path = paths[0]
    file = bridge("investigate", kind="file", path=workflow_path, ref="main")
    assert file["ok"] and file["sources"], "Real workflow file read failed"
    sha = file["investigation"]["commit_sha"]
    assert len(sha) == 40 and any(f"ref={sha}" in s.get("url", "") for s in file["sources"])
    assert all(len(s["text"].encode()) <= briefing.SOURCE_CAP for s in file["sources"])
    with tempfile.TemporaryDirectory(prefix="fm-c0-absent-state-") as temporary:
        empty = Path(temporary)
        subprocess.run(["git", "init", "-q", str(empty)], check=True, capture_output=True)
        (empty / ".factory.toml").write_text('[repo]\nslug = "mikeroySoft/factory"\n')
        for op, fields in (("observe", {}), ("capabilities", {}),
                           ("investigate", {"kind": "file", "path": workflow_path, "ref": sha})):
            result = bridge(op, root=empty, **fields)
            assert result["cases"] if op == "observe" else result["ok"], f"Disposable-root {op} failed"
            assert not (empty / ".factory").exists(), "Read path created state/locks"
    assert observe_files() == before, "Code/config changed concurrently; inspect before attributing it"
    print("PASS: real compact observe/inspect/capabilities/revision-pinned workflow reads; fresh scope/time; invalid inputs and exit semantics; absent state stays absent; code/config unchanged")
    print("Bounded I/O, symlinks and controlled CI failures are covered by tests/test_evidence.py. This live smoke makes no model call and does not establish terminal or sandbox acceptance.")


if __name__ == "__main__":
    main()
