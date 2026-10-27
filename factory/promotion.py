"""Split-aware eval promotion contract (docs/eval-promotion-contract.md).

A claim compares one attributable change (candidate) to a baseline with repeated
scores per split. Keep only if train improves beyond grader noise and the
lockbox (or fresh production) mean also rises (delta > 0); held-out keep does
not require beating lockbox/fresh noise. Flat or down reverts. Higher score
is better. Claims must set contract_version to 1. The deterministic gate is
untouched: this only judges eval claims.

Online form (`window`, `drift`): a merged lessons/curate PR is the candidate, the
`score` rows of the PROMOTION_N attempts before the merge are the baseline and the
first PROMOTION_N scored attempts journaled after it the fresh split; the same pstdev noise margin applies.
"""

from __future__ import annotations

from statistics import mean, pstdev

SPLITS = ("train", "selection", "lockbox", "fresh")
CHANGE_KINDS = ("prompt", "skill", "lesson", "worker-config", "routing", "rubric", "model", "effort", "gate-check")
MIN_REPEATS = 2
CONTRACT_VERSION = 1
PROMOTION_N = 10  # scored attempts per window on each side of a lessons/curate merge


def noise(scores: list[float]) -> float:
    """Repeat-run spread of one arm; the margin a difference must beat."""
    return pstdev(scores)


def decide(claim: dict) -> tuple[str, str]:
    """(`keep` | `revert`, reason) for a promotion claim; ValueError if the claim breaks the contract."""
    version = claim.get("contract_version")
    if version is None:
        raise ValueError("claim needs contract_version")
    if version != CONTRACT_VERSION:
        raise ValueError(f"unknown contract_version {version!r}; supported: {CONTRACT_VERSION}")
    change = claim.get("change") or {}
    if change.get("kind") not in CHANGE_KINDS:
        raise ValueError(f"change.kind must be one of {CHANGE_KINDS}")
    objective = claim.get("objective", "promotion")
    if objective not in ("promotion", "regression"):
        raise ValueError("objective must be promotion or regression")
    for key in ("stage", "baseline", "candidate"):
        if not claim.get(key):
            raise ValueError(f"claim needs {key!r}")
    splits = claim.get("splits") or {}
    if unknown := set(splits) - set(SPLITS):
        raise ValueError(f"unknown splits {sorted(unknown)}")
    held = "lockbox" if "lockbox" in splits else "fresh" if "fresh" in splits else None
    if "train" not in splits or not held:
        raise ValueError("claim needs train scores and lockbox or fresh scores")
    arms = {}
    for name in ("train", held):
        base, cand = splits[name].get("baseline") or [], splits[name].get("candidate") or []
        if min(len(base), len(cand)) < MIN_REPEATS:
            raise ValueError(f"{name}: need >= {MIN_REPEATS} repeat scores per arm")
        margin = max(noise(base), noise(cand))
        arms[name] = (mean(cand) - mean(base), margin)
    if objective == "regression":
        for name, (delta, margin) in arms.items():
            if delta < -margin:
                return "revert", f"{name} drop {delta:+.3f} beyond noise {margin:.3f}"
        return "keep", "; ".join(
            f"{name} {delta:+.3f} (noise {margin:.3f})"
            for name, (delta, margin) in arms.items()
        )
    gain, margin = arms["train"]
    if gain <= margin:
        return "revert", f"train gain {gain:+.3f} within noise {margin:.3f}"
    delta, margin = arms[held]
    if delta <= 0:
        return "revert", f"train-only win: {held} {delta:+.3f} flat or down (noise {margin:.3f})"
    return "keep", f"train {gain:+.3f}; {held} {delta:+.3f} (noise {margin:.3f})"


def scored(rows: list[dict]) -> list[dict]:
    """Journal `score` rows that carry a number (a missing transcript journals `skipped`)."""
    return [r for r in rows if r.get("event") == "score" and type(r.get("efficiency")) in (int, float)]


def window(scores: list[dict]) -> dict:
    """Means of one side of a merge: `efficiency_mean`, `compliance_mean` (judged rows only,
    None when none), `gate_first_pass_rate`, `n`; the means are None for an empty window."""
    judged = [s["compliance"] for s in scores if type(s.get("compliance")) in (int, float)]
    return {
        "efficiency_mean": mean(s["efficiency"] for s in scores) if scores else None,
        "compliance_mean": mean(judged) if judged else None,
        "gate_first_pass_rate": mean(float(bool(s.get("gate_first_pass"))) for s in scores) if scores else None,
        "n": len(scores),
    }


def drift(before: list[dict], after: list[dict]) -> tuple[str, str]:
    """(`keep` | `revert`, reason) for a merged change: revert when `efficiency_mean` or
    `gate_first_pass_rate` fell by more than the baseline window's noise (`pstdev`)."""
    if not before:
        return "keep", "no scored attempts before the merge; nothing to compare against"
    base, cand = window(before), window(after)
    notes = []
    for key, values in (("efficiency_mean", [s["efficiency"] for s in before]),
                        ("gate_first_pass_rate", [float(bool(s.get("gate_first_pass"))) for s in before])):
        delta, margin = cand[key] - base[key], noise(values)
        notes.append(f"{key} {base[key]:.3f} -> {cand[key]:.3f} ({delta:+.3f}, noise {margin:.3f})")
        if delta < -margin:
            return "revert", notes[-1] + " beyond noise"
    return "keep", "; ".join(notes)
