"""Class (a) replay driver for #694 on GitHub-hosted Linux (ruling r3-2110). Lives on branch r3-classA-ci only.

Adapted for Linux from Ultron's sup/classb-untied-driver.py (sha256 fdd8e64a115aea3631411372b791609ac1ac714c0541b53e3d39d70a7d594bbc).
No credentials: it imports nothing but the standard library and reads no token, config or auth file.

usage: classA-driver.py ID | CONTROL
  ID       one of the 12 class (a) mutant IDs: the pinned <ID>.mutated.py is written over the target file in a scratch
           clone of the checkout at 133a496b, after checking the tree sha, the pinned bytes, and that the regenerated
           diff equals the pinned <ID>.diff byte for byte.
  CONTROL  the same run with NO mutation (the unmutated baseline).
Runs `python -B -m unittest discover -s tests` through a bootstrap that first puts cosmic_ray.exceptions.CosmicRayTestingException in builtins (the full suite, no --tests, no --failfast so every failing test is
named) with stdout and stderr in ONE log, no timeout enforced (a run over 8106s is only flagged), never edits the
checkout, never kills a child. Writes result.json (+ output.log) under $R3_DRIVER_OUT/<ID>/.
Exit 0 whenever a result was recorded (the verdict is in result.json); nonzero only on a STOP before any test ran.
Env contract (checked, refused otherwise): SIGINT default, TMPDIR set and under R3_BASE, no forbidden variables.
"""
import datetime
import difflib
import hashlib
import json
import os
import pathlib
import re
import shlex
import signal
import subprocess
import sys
import time

BASE_DIR = pathlib.Path(os.environ["R3_BASE"])
CHECKOUT = pathlib.Path(os.environ["R3_CHECKOUT"])
EXPECT = pathlib.Path(os.environ["R3_EXPECT"])
OUT = pathlib.Path(os.environ.get("R3_DRIVER_OUT", str(BASE_DIR / "driver-out")))
HEAD = "133a496b9623ea47fb0a3b34d8a5354c860b26ec"
TREE = "d15b4a81e67a3ce540d3b03b3ed2cc274bdb2707"
SELECTED_SHA = "3dd1d72ed28e7da89781b5c47c1f2a4ee8db0c5e4aa77bfc60ab4baafed6a847"
FORBIDDEN = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "UV_NO_SYNC", "VIRTUAL_ENV",
             "FACTORY_MUTATION_PROOF", "FACTORY_MUTATION_PROOF_BASE", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN")
CONTROL = "CONTROL"


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


def regen_diff(source, mutated, file):
    """Exactly the wrapper's (sup/ta/regen.py) diff format: n=2, a/ b/ headers, trailing newline."""
    return "\n".join(difflib.unified_diff(source.split("\n"), mutated.split("\n"), fromfile="a/" + file,
                                          tofile="b/" + file, lineterm="", n=2)) + "\n"


def replay(mid, by):
    work = OUT / mid
    work.mkdir(parents=True, exist_ok=False)
    tmp = work / "tmp"
    tmp.mkdir()
    copy = work / "copy"
    git("clone", "--quiet", "--shared", str(CHECKOUT), str(copy), cwd=BASE_DIR)
    git("checkout", "--quiet", "--detach", HEAD, cwd=copy)
    if git("rev-parse", "HEAD", cwd=copy) != HEAD or git("rev-parse", "HEAD^{tree}", cwd=copy) != TREE:
        raise SystemExit(f"STOP: copy head/tree differs for {mid}")
    row = {"id": mid, "file": None, "line": None, "operator": None}
    mutated_sha = original_sha = diff_sha = None
    if mid != CONTROL:
        row = by[mid]
        mutated = (EXPECT / "diffs" / f"{mid}.mutated.py").read_bytes()
        want_diff = (EXPECT / "diffs" / f"{mid}.diff").read_bytes()
        if sha(mutated) != row["mutated_sha256"]:
            raise SystemExit(f"STOP: pinned mutated bytes for {mid} differ from mutants200.json")
        if sha(want_diff) != row["diff_sha256"]:
            raise SystemExit(f"STOP: pinned diff for {mid} differs from mutants200.json")
        target = copy / row["file"]
        original = target.read_bytes()
        if sha(original) == sha(mutated):
            raise SystemExit(f"STOP: {mid} mutated file equals the original")
        if regen_diff(original.decode(), mutated.decode(), row["file"]).encode() != want_diff:
            raise SystemExit(f"STOP: pinned diff for {mid} does not turn the checked-out file into the mutated bytes")
        target.write_bytes(mutated)
        if sha(target.read_bytes()) != row["mutated_sha256"]:
            raise SystemExit(f"STOP: written bytes for {mid} differ from the pinned mutated sha")
        mutated_sha, original_sha, diff_sha = sha(mutated), sha(original), sha(want_diff)
    status = git("status", "--short", cwd=copy)
    expect_status = f"M {row['file']}" if mid != CONTROL else ""
    if status != expect_status:
        raise SystemExit(f"STOP: copy tree status {status!r} != {expect_status!r} for {mid}")
    env = {k: v for k, v in os.environ.items() if k not in FORBIDDEN}
    env.update({"TMPDIR": str(tmp), "PYTHONPATH": str(copy), "PYTHONDONTWRITEBYTECODE": "1"})
    # r3-2240: the same entry point as `python -m unittest discover -s tests`, started with CosmicRayTestingException
    # in builtins (cosmic-ray's own class, 8.7.0), so a mutant that names it in an `except` runs as a real mutant
    # instead of dying with a NameError (the M171 artifact). CONTROL uses the identical command.
    bootstrap = ("import builtins, sys, unittest\n"
                 "from cosmic_ray.exceptions import CosmicRayTestingException\n"
                 "builtins.CosmicRayTestingException = CosmicRayTestingException\n"
                 "sys.argv = ['python -m unittest', 'discover', '-s', 'tests']\n"
                 "unittest.main(module=None)\n")
    cmd = ["env", "-C", str(copy), f"PYTHONPATH={copy}", sys.executable, "-B", "-c", bootstrap]
    log = work / "output.log"
    started, t0 = now(), time.monotonic()
    with log.open("wb") as fh:
        proc = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, check=False)
    wall = round(time.monotonic() - t0, 1)
    text = log.read_text(errors="replace")
    named = re.findall(r"^(?:FAIL|ERROR): (.+)$", text, re.MULTILINE)
    ran = re.findall(r"^Ran (\d+) tests? in ([\d.]+)s", text, re.MULTILINE)
    if mid == CONTROL:
        verdict = "control passed (all passed)" if proc.returncode == 0 else "control FAILED (baseline not clean)"
    else:
        verdict = ("survived (all passed)" if proc.returncode == 0 else
                   "killed-TIED" if named else "killed-UNTIED (rc!=0, no named FAIL/ERROR)")
    result = {"id": mid, "file": row["file"], "line": row["line"], "operator": row["operator"], "rc": proc.returncode,
              "verdict": verdict, "failing_tests": named, "ran_line": ran[-1] if ran else None,
              "ran_line_text": re.findall(r"^Ran \d+ tests? in [\d.]+s", text, re.MULTILINE)[-1:] ,
              "wall_s": wall, "over_8106s": wall > 8106, "started": started, "finished": now(),
              "command": shlex.join(cmd), "head": HEAD, "tree": TREE, "selection_stamp": SELECTED_SHA,
              "mutated_sha256": mutated_sha, "original_sha256": original_sha, "pinned_diff_sha256": diff_sha,
              "copy_git_status": status, "log": str(log), "log_sha256": sha(log.read_bytes()), "tail": text[-1500:]}
    (work / "result.json").write_text(json.dumps(result, indent=1))
    print(mid, verdict, "rc", proc.returncode, named[:3], wall, flush=True)


def main(ids):
    if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        raise SystemExit("STOP: SIGINT is not the default handler")
    bad_env = [k for k in FORBIDDEN if k in os.environ] + [k for k in os.environ if k.startswith("UV_NO_INSTALL")]
    if bad_env:
        raise SystemExit(f"STOP: forbidden environment: {bad_env}")
    tmp = pathlib.Path(os.environ.get("TMPDIR", "")).resolve()
    if not os.environ.get("TMPDIR") or BASE_DIR.resolve() not in tmp.parents:
        raise SystemExit("STOP: TMPDIR must be under R3_BASE")
    if len(ids) != 1:
        raise SystemExit(__doc__)
    if git("rev-parse", "HEAD", cwd=CHECKOUT) != HEAD or git("rev-parse", "HEAD^{tree}", cwd=CHECKOUT) != TREE \
            or git("status", "--short", cwd=CHECKOUT):
        raise SystemExit("STOP: checkout not clean at the pinned head/tree")
    expected = json.loads((EXPECT / "mutants200.json").read_text())
    if expected["selected_sha256"] != SELECTED_SHA:
        raise SystemExit("STOP: mutants200.json stamp is not the proof stamp")
    by = {m["id"]: m for m in expected["mutants"]}
    if ids[0] != CONTROL and ids[0] not in by:
        raise SystemExit(f"STOP: bad ID {ids[0]}")
    OUT.mkdir(parents=True, exist_ok=True)
    replay(ids[0], by)


if __name__ == "__main__":
    main(sys.argv[1:])
