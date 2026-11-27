"""r3-0735 #903 ablation: validity and credit rule (f). Pure functions + small CLI."""
import json
import sys
from pathlib import Path

SEQUENCE = ("control-1", "ram", "control-2", "git-cache", "control-3", "scratch-throttle", "control-4")
ARMS = {"ram": ("control-1", "control-2"), "git-cache": ("control-2", "control-3"),
        "scratch-throttle": ("control-3", "control-4")}
PASS_SECONDS = 798.0
FIX_TICKET_SECONDS = 60.0


def outcome(run: dict) -> tuple:
    """The pass/fail/error set: run count plus the failing and erroring test ids."""
    return (run["tests_run"], frozenset(run["failure_ids"]), frozenset(run["error_ids"]))


def same_outcome(reference: dict, run: dict) -> bool:
    return outcome(reference) == outcome(run)


def unexpected(run: dict) -> list:
    """r3-0815: control-1 must be fully clean, so every failure or error is unexpected."""
    return sorted([*run["failure_ids"], *run["error_ids"]])


VM_POINTS = 10.0


def vm_neighbors(label: str, done: set) -> list:
    """Already-valid runs a run's VM CPU is compared with: an arm with its preceding control;
    control-k with the preceding control and the preceding arm."""
    i = SEQUENCE.index(label)
    if label.startswith("control"):
        return [n for n in (SEQUENCE[i - 2] if i >= 2 else None, SEQUENCE[i - 1] if i >= 1 else None)
                if n in done]
    return [n for n in (SEQUENCE[i - 1],) if n in done]


def vm_ok(label: str, vm: dict) -> bool:
    """vm: label -> mean VM CPU%; True when within VM_POINTS of every valid neighbor."""
    return all(abs(vm[label] - vm[n]) <= VM_POINTS for n in vm_neighbors(label, set(vm) - {label}))


def credit(seconds: dict) -> dict:
    """Rule (f): arm saving = mean(surrounding controls) - arm; 0 inside the max control spread."""
    controls = [seconds[c] for c in SEQUENCE if c.startswith("control") and c in seconds]
    spread = max(controls) - min(controls) if len(controls) > 1 else 0.0
    arms = {}
    for arm, (before, after) in ARMS.items():
        if arm not in seconds or before not in seconds or after not in seconds:
            arms[arm] = {"measured": False, "credited": 0.0}
            continue
        delta = (seconds[before] + seconds[after]) / 2 - seconds[arm]
        credited = delta if delta > spread else 0.0
        arms[arm] = {"measured": True, "delta": delta, "credited": credited,
                     "fix_ticket": delta >= FIX_TICKET_SECONDS}
    total = sum(a["credited"] for a in arms.values())
    return {"spread": spread, "arms": arms, "total_credited": total,
            "verdict": "PASS" if total >= PASS_SECONDS else "INCONCLUSIVE"}


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def main(argv: list) -> int:
    command = argv[1]
    if command == "control1":
        bad = unexpected(_load(argv[2]))
        print(json.dumps({"unexpected": bad}))
        return 1 if bad else 0
    if command == "same":
        return 0 if same_outcome(_load(argv[2]), _load(argv[3])) else 1
    if command == "vmcheck":  # vmcheck <valid-dir> <label> <run-dir>
        vm = {n: _load(str(Path(argv[2]) / n / "vm.json"))["mean"] for n in SEQUENCE
              if (Path(argv[2]) / n / "vm.json").exists()}
        vm[argv[3]] = _load(str(Path(argv[4]) / "vm.json"))["mean"]
        ok = vm_ok(argv[3], vm)
        print(json.dumps({"label": argv[3], "vm": vm, "neighbors": vm_neighbors(argv[3], set(vm) - {argv[3]}),
                          "ok": ok}))
        return 0 if ok else 1
    if command == "credit":  # credit <results-dir>: valid runs only, dir/<label>/outcomes.json
        seconds = {}
        for label in SEQUENCE:
            path = Path(argv[2]) / label / "outcomes.json"
            if path.exists():
                seconds[label] = _load(str(path))["seconds"]
        print(json.dumps({"seconds": seconds, **credit(seconds)}, indent=2))
        return 0
    raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
