"""Split-aware eval promotion contract (docs/eval-promotion-contract.md).

A claim compares one attributable change (candidate) to a baseline with repeated
scores per split. Keep only if train improves beyond grader noise and the
lockbox (or fresh production) mean also rises (delta > 0); held-out keep does
not require beating lockbox/fresh noise. Flat or down reverts. Higher score
is better. Claims must set contract_version to 1. The deterministic gate is
untouched: this only judges eval claims.
"""

from __future__ import annotations

from statistics import mean, pstdev

SPLITS = ("train", "selection", "lockbox", "fresh")
CHANGE_KINDS = ("prompt", "skill", "rubric", "model", "effort")
MIN_REPEATS = 2
CONTRACT_VERSION = 1


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
    gain, margin = arms["train"]
    if gain <= margin:
        return "revert", f"train gain {gain:+.3f} within noise {margin:.3f}"
    delta, margin = arms[held]
    if delta <= 0:
        return "revert", f"train-only win: {held} {delta:+.3f} flat or down (noise {margin:.3f})"
    return "keep", f"train {gain:+.3f}; {held} {delta:+.3f} (noise {margin:.3f})"
