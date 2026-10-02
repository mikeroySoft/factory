"""Split-aware Pilot 1 scoring (docs/eval-promotion-contract.md, issue #126).

Reads an adjudication.json and reports, per contract arm and split, the two
separate reviewer metrics with repeat-run noise:
  missed_blocking     — a primary defect was missed, or a REVISE case got APPROVE
  unnecessary_revise  — an APPROVE case got REVISE
Counts only; never a population accuracy claim.

usage: score.py <adjudication.json>
"""
import json, pathlib, statistics, sys

HERE = pathlib.Path(__file__).parent
SPLITS = json.loads((HERE / "splits.json").read_text())["splits"]


def primary_ids(case: str) -> set[str]:
    oracle = json.loads((HERE / "cases" / case / "oracle.json").read_text())
    return {f["id"] for f in oracle["findings"] if f["severity"] == "primary"}


def split_of(case: str) -> str:
    return next(name for name, cases in SPLITS.items() if case in cases)


def score(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        missed = r["expected_verdict"] == "REVISE" and (
            r["verdict"] != "REVISE" or any(
                r["defects"].get(d) == "missed" for d in primary_ids(r["case"])))
        unnecessary = r["expected_verdict"] == "APPROVE" and r["verdict"] == "REVISE"
        arm = out.setdefault(r["contract"], {}).setdefault(split_of(r["case"]), {})
        s = arm.setdefault(r["sample"], {"n": 0, "missed_blocking": 0, "unnecessary_revise": 0})
        s["n"] += 1; s["missed_blocking"] += missed; s["unnecessary_revise"] += unnecessary
    report = {}
    for contract, splits in out.items():
        for split, samples in splits.items():
            quality = [1 - (s["missed_blocking"] + s["unnecessary_revise"]) / s["n"] for s in samples.values()]
            report.setdefault(contract, {})[split] = {
                "cases_per_repeat": next(iter(samples.values()))["n"], "repeats": len(samples),
                "missed_blocking": sum(s["missed_blocking"] for s in samples.values()),
                "unnecessary_revise": sum(s["unnecessary_revise"] for s in samples.values()),
                "quality_per_repeat": quality,
                "noise_pstdev": round(statistics.pstdev(quality), 4)}
    return report


if __name__ == "__main__":
    print(json.dumps(score(json.loads(pathlib.Path(sys.argv[1]).read_text())), indent=2))
