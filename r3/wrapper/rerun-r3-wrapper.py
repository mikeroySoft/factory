"""ID-filtered rerun wrapper for the #694 proof-r3 timeouts. Lives in sup/, never in the repo.

usage:
  rerun-r3-wrapper.py verify ID [ID ...]
      Dry. Runs NO tests. Enumerates the selection exactly as scripts/mutation.py does (seed 197, cap 200,
      scope 1f9d2810, head 133a496b), checks the stamp, and for every ID checks that the mutation it would
      apply byte-matches runs/final-mutation-proof/timeouts/diffs/<ID>.diff and <ID>.mutated.py.
      Writes r3-exec/wrapper-verify.json. Exit 0 only if everything matches.
  rerun-r3-wrapper.py run --go RULING OUTDIR ID [ID ...]
      [Tank variant: R3_BASE/R3_CHECKOUT/R3_EXPECT/R3_VERIFY_OUT env paths; run options --workers N --tests A,B --duration S before the IDs; failing tests logged]
      Calls scripts/mutation.py main() (proof mode, --blocking, same equivalents file, --duration 36000) with
      the selection limited to the IDs, at most 6 workers, per-mutant timeout 8106s. Re-checks every mutation
      up front and again just before each one; any mismatch stops the run before a test starts.

Environment contract (checked, refused otherwise): cwd/checkout final-proof-r3 at head 133a496b, SIGINT at
Python's default handler, TMPDIR under the base dir, no UV_NO_SYNC / VIRTUAL_ENV / UV_NO_INSTALL* /
ANTHROPIC_API_KEY / FACTORY_MUTATION_PROOF*.
"""
import datetime
import difflib
import hashlib
import json
import math
import os
import pathlib
import re
import shlex
import signal
import sys
import tempfile
import threading
import time

D = pathlib.Path(os.environ.get("R3_BASE", "/Users/mike/factory-survivors-20261109"))
CHECKOUT = pathlib.Path(os.environ.get("R3_CHECKOUT", str(D / "final-proof-r3")))
EXPECT = pathlib.Path(os.environ.get("R3_EXPECT", str(D / "runs/final-mutation-proof/timeouts")))
HEAD = "133a496b9623ea47fb0a3b34d8a5354c860b26ec"
BASE = "19457e840460e0163e1172f08580538c4dcae767"
FINGERPRINT = "1f9d2810b1744c69a690f2dbf837c3554d9dcd622ae58f0529f1f31274f23784"
SELECTED_SHA = "3dd1d72ed28e7da89781b5c47c1f2a4ee8db0c5e4aa77bfc60ab4baafed6a847"
SEED, CAP = 197, 200
TIMEOUT = 8106.0
MAX_WORKERS = 6
DURATION = "36000"
FORBIDDEN = ("UV_NO_SYNC", "VIRTUAL_ENV", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY",
             "FACTORY_MUTATION_PROOF", "FACTORY_MUTATION_PROOF_BASE")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def regen_diff(source, mutated, file):
    """Exactly sup/ta/regen.py's diff format (n=2, a/ b/ headers, trailing newline)."""
    return "\n".join(difflib.unified_diff(source.split("\n"), mutated.split("\n"), fromfile="a/" + file,
                                          tofile="b/" + file, lineterm="", n=2)) + "\n"


def load(ids):
    os.chdir(CHECKOUT)
    sys.path.insert(0, str(CHECKOUT))
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    if head != HEAD:
        raise SystemExit(f"STOP: checkout head {head} != {HEAD}")
    from scripts import mutation as M
    if (M.SEED, M.CAP) != (SEED, CAP):
        raise SystemExit(f"STOP: harness seed/cap {(M.SEED, M.CAP)} != {(SEED, CAP)}")
    expected = json.loads((EXPECT / "mutants200.json").read_text())
    by = {m["id"]: m for m in expected["mutants"]}
    if expected["selected_sha256"] != SELECTED_SHA:
        raise SystemExit("STOP: mutants200.json stamp is not the proof stamp")
    bad = [i for i in ids if i not in by]
    if bad or len(set(ids)) != len(ids) or not ids:
        raise SystemExit(f"STOP: bad or duplicate IDs: {bad or ids}")
    return M, expected, by


def check_scope(M):
    computed = []
    lines = M.scope_lines(BASE, computed)
    receipt = computed[0].receipt()
    if receipt["fingerprint"] != FINGERPRINT or receipt["head"] != HEAD or receipt["base"] != BASE:
        raise SystemExit(f"STOP: scope receipt differs from the proof's: {receipt}")
    return lines, receipt


def check_selection(muts, hot, expected, by, ids, strict=True):
    """(per-ID results, problems). strict: raise SystemExit unless the full stamp and every ID match."""
    from cosmic_ray.mutating import mutate_code
    from cosmic_ray.plugins import get_operator
    from cosmic_ray.util import read_python_source

    sel = [[str(m.module_path), list(m.start_pos), list(m.end_pos), m.operator_name, m.occurrence] for m in muts]
    stamp = sha(json.dumps(sel, separators=(",", ":")))
    problems = []
    if stamp != SELECTED_SHA or hot != expected["hot"]:
        problems.append(f"selection stamp {stamp} / hot {hot} differs from the proof's")
    results = []
    for mid in ids:
        exp = by[mid]
        key = [exp["file"], exp["start_pos"], exp["end_pos"], exp["operator"], exp["occurrence"]]
        row = {"id": mid, "file": exp["file"], "line": exp["line"], "operator": exp["operator"],
               "in_selection": key in sel}
        if not row["in_selection"]:
            row["match"] = False
            results.append(row)
            continue
        index = sel.index(key)
        mutation = muts[index]
        source = read_python_source(mutation.module_path)
        operator = get_operator(mutation.operator_name)(**(mutation.operator_args or {}))
        mutated = mutate_code(source, operator, mutation.occurrence)
        diff = regen_diff(source, mutated, exp["file"]) if mutated is not None else None
        want_diff = (EXPECT / "diffs" / f"{mid}.diff").read_bytes()
        want_mut = (EXPECT / "diffs" / f"{mid}.mutated.py").read_bytes()
        row.update({
            "index_matches": index == exp["index"],
            "diff_bytes_match": diff is not None and diff.encode() == want_diff,
            "mutated_bytes_match": mutated is not None and mutated.encode() == want_mut,
            "diff_sha256": sha(diff) if diff is not None else None,
            "mutated_sha256": sha(mutated) if mutated is not None else None,
            "recorded_diff_sha256": exp["diff_sha256"], "recorded_mutated_sha256": exp["mutated_sha256"],
            "diff_file_sha256": sha(want_diff), "mutated_file_sha256": sha(want_mut),
        })
        row["match"] = (row["index_matches"] and row["diff_bytes_match"] and row["mutated_bytes_match"]
                        and row["diff_sha256"] == row["recorded_diff_sha256"] == row["diff_file_sha256"]
                        and row["mutated_sha256"] == row["recorded_mutated_sha256"] == row["mutated_file_sha256"])
        row["_mutation_index"] = index
        results.append(row)
    problems += [f"mutation mismatch {r['id']}" for r in results if not r["match"]]
    if strict and problems:
        raise SystemExit("STOP: " + "; ".join(problems))
    return results, problems


def verify(ids):
    M, expected, by = load(ids)
    lines, receipt = check_scope(M)
    with tempfile.TemporaryDirectory(prefix="r3-wv-") as tmp:
        muts, hot = M.sample(lines, pathlib.Path(tmp))
    results, problems = check_selection(muts, hot, expected, by, ids, strict=False)
    status = "ALL-MATCH" if not problems else "STOP: " + "; ".join(problems)
    report = {"time": now(), "wrapper_sha256": sha(pathlib.Path(__file__).read_bytes()), "head": HEAD, "scope": receipt,
              "selection_stamp": SELECTED_SHA, "hot": hot, "selected": len(muts), "ids": ids, "status": status,
              "results": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]}
    out = pathlib.Path(os.environ.get("R3_VERIFY_OUT", str(D / "r3-exec/wrapper-verify.json")))
    out.write_text(json.dumps(report, indent=1))
    for r in report["results"]:
        print(r["id"], f'{r["file"]}:{r["line"]}', "MATCH" if r["match"] else "MISMATCH",
              "diff", r["diff_bytes_match"], "mutated", r["mutated_bytes_match"], r["diff_sha256"][:12])
    print(status, "->", out)
    return 0 if status == "ALL-MATCH" else 1


def refuse_environment():
    bad = sorted(k for k in os.environ if k in FORBIDDEN or k.startswith("UV_NO_INSTALL"))
    if bad:
        raise SystemExit(f"STOP: forbidden environment: {bad}")
    if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        raise SystemExit(f"STOP: SIGINT is {signal.getsignal(signal.SIGINT)!r}, not the default handler")
    tmp = pathlib.Path(os.environ.get("TMPDIR", "")).resolve()
    if not os.environ.get("TMPDIR") or D not in tmp.parents:
        raise SystemExit("STOP: TMPDIR must be set to a directory under the base dir")


def run(ruling, out, ids, workers=MAX_WORKERS, tests=None, duration=DURATION):
    if not ruling:
        raise SystemExit("STOP: --go needs the ruling id")
    if not 1 <= workers <= MAX_WORKERS:
        raise SystemExit(f"STOP: workers must be 1..{MAX_WORKERS}")
    refuse_environment()
    if math.ceil(len(ids) / workers) * TIMEOUT + 4500 > float(duration) - 60:
        raise SystemExit(f"STOP: too many IDs for one {duration}s window")
    M, expected, by = load(ids)
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "launch.json").write_text(json.dumps({"time": now(), "ruling": ruling, "ids": ids, "pid": os.getpid(),
                                                 "wrapper_sha256": sha(pathlib.Path(__file__).read_bytes())}, indent=1))
    expected_mutated = {}
    original_sample, original_scope = M.sample, M.scope_lines

    def scope_lines(base, computed):
        lines = original_scope(base, computed)
        if computed[-1].receipt()["fingerprint"] != FINGERPRINT:
            raise RuntimeError("scope fingerprint changed")
        return lines

    def sample(lines, scratch):
        muts, hot = original_sample(lines, scratch)
        results, _ = check_selection(muts, hot, expected, by, ids)
        chosen = [muts[r["_mutation_index"]] for r in results]
        for r, mutation in zip(results, chosen, strict=True):
            expected_mutated[(str(mutation.module_path), tuple(mutation.start_pos), mutation.operator_name,
                              mutation.occurrence)] = (r["id"], r["mutated_sha256"])
        return chosen, len(chosen)

    M.scope_lines, M.sample, M.WORKERS = scope_lines, sample, min(workers, M.WORKERS)
    if tests:
        def scoped_test_command(copy, module=None):
            return shlex.join(["env", "-C", str(copy), f"PYTHONPATH={copy}", sys.executable, "-B", "-m", "unittest", "-f", *tests])

        M.test_command = scoped_test_command
    import cosmic_ray.mutating as CM
    from cosmic_ray.plugins import get_operator
    from cosmic_ray.util import read_python_source

    inner, lock, stop = CM.mutate_and_test, threading.Lock(), threading.Event()

    def guarded(mutations, test_command, timeout):
        mutation = mutations[0]
        rel = "/".join(pathlib.PurePosixPath(str(mutation.module_path)).parts[-2:])
        mid, want = expected_mutated.get((rel, tuple(mutation.start_pos), mutation.operator_name, mutation.occurrence),
                                         (None, None))
        if stop.is_set() or len(mutations) != 1 or mid is None or timeout < 60:
            stop.set()
            raise RuntimeError(f"stopped before a test started: {rel} {mid} timeout={timeout}")
        operator = get_operator(mutation.operator_name)(**(mutation.operator_args or {}))
        from cosmic_ray.mutating import mutate_code
        mutated = mutate_code(read_python_source(mutation.module_path), operator, mutation.occurrence)
        if mutated is None or sha(mutated) != want:
            stop.set()
            raise RuntimeError(f"mutation bytes differ from the pinned {mid}; no test started")
        started, t0 = now(), time.monotonic()
        result = inner(mutations, test_command, TIMEOUT)
        outcome = "timeout" if result.output == "timeout" else (
            result.test_outcome.value if result.test_outcome else result.worker_outcome.value)
        with lock, (out / "mutants.jsonl").open("a") as log:
            log.write(json.dumps({"id": mid, "file": rel, "line": mutation.start_pos[0], "operator": mutation.operator_name,
                                  "outcome": outcome, "duration_s": round(time.monotonic() - t0, 1), "timeout_s": TIMEOUT,
                                  "harness_timeout_arg": timeout, "started": started, "finished": now(),
                                  "result_diff_sha256": sha(result.diff or ""),
                                  "failed_tests": re.findall(r"^(?:FAIL|ERROR): (.+)$", result.output or "", re.MULTILINE)[:8],
                                  "output_tail": (result.output or "")[-1200:]}) + "\n")
        return result

    CM.mutate_and_test = guarded
    os.environ["FACTORY_MUTATION_PROOF"] = str(out / "proof.json")
    os.environ["FACTORY_MUTATION_PROOF_BASE"] = BASE
    code = M.main(["--blocking", "--equivalents", "mutation-equivalents.json", "--duration", str(duration)])
    (out / "exit.json").write_text(json.dumps({"time": now(), "exit": code}))
    return code


def main(argv):
    if len(argv) >= 2 and argv[0] == "verify":
        return verify(argv[1:])
    if len(argv) >= 5 and argv[0] == "run" and argv[1] == "--go":
        rest, opts = argv[4:], {}
        while rest and rest[0] in ("--workers", "--tests", "--duration"):
            opts[rest[0][2:]] = rest[1]
            rest = rest[2:]
        return run(argv[2], argv[3], rest, workers=int(opts.get("workers", MAX_WORKERS)),
                   tests=opts["tests"].split(",") if "tests" in opts else None, duration=opts.get("duration", DURATION))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
