"""Score the frozen Jev replay and render summary.json plus REPORT.md."""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CORPUS = REPO / "experiments" / "reviewer-calibration"
RUN = ROOT / "runs" / "main"
THRESHOLD = 0.5


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def split_of(case: str) -> str:
    splits = json.loads((CORPUS / "splits.json").read_text())["splits"]
    return next(split for split, cases in splits.items() if case in cases)


def load_oracles() -> dict:
    splits = json.loads((CORPUS / "splits.json").read_text())["splits"]
    out = {}
    for case in [item for name in ("train", "lockbox") for item in splits[name]]:
        oracle = json.loads((CORPUS / "cases" / case / "oracle.json").read_text())
        out[case] = {"expected_verdict": oracle["expected_verdict"],
                     "primary": {finding["id"] for finding in oracle["findings"] if finding["severity"] == "primary"}}
    return out


def _noise(values: list[float]) -> float:
    return round(statistics.pstdev(values), 4) if values else 0.0


def score_candidate(rows: list[dict], mapping: dict[str, dict[str, list[str]]], oracles: dict) -> dict:
    cells = []
    buckets: dict[str, dict[int, dict]] = {}
    for row in rows:
        flags = sorted(key.removesuffix("__whole") for key, value in row["answers"].items()
                       if key.endswith("__whole") and value < THRESHOLD)
        mapped = {defect for criterion in flags for defect in mapping[row["case"]][criterion]}
        primary = oracles[row["case"]]["primary"]
        flagged_primary = sorted(primary & mapped)
        false_flags = [criterion for criterion in flags if not mapping[row["case"]][criterion]]
        cell = {"case": row["case"], "split": row["split"], "sample": row["sample"],
                "flagged_criteria": flags, "flagged_primary_defects": flagged_primary,
                "missed_primary_defects": sorted(primary - mapped), "false_flags": false_flags}
        cells.append(cell)
        bucket = buckets.setdefault(row["split"], {}).setdefault(row["sample"], {
            "packets": 0, "blocking_defects": 0, "blocking_defects_flagged": 0,
            "blocking_defects_missed": 0, "false_flags": 0, "clean_after_packets": 0,
            "false_flags_on_clean_after": 0, "input_tokens": 0, "output_tokens": 0,
            "estimated_usd": 0.0, "latency_seconds": 0.0})
        bucket["packets"] += 1
        bucket["blocking_defects"] += len(primary)
        bucket["blocking_defects_flagged"] += len(flagged_primary)
        bucket["blocking_defects_missed"] += len(primary - mapped)
        bucket["false_flags"] += len(false_flags)
        if row["case"].endswith("-after"):
            bucket["clean_after_packets"] += 1
            bucket["false_flags_on_clean_after"] += len(false_flags)
        bucket["input_tokens"] += row.get("usage", {}).get("input_tokens", 0)
        bucket["output_tokens"] += row.get("usage", {}).get("output_tokens", 0)
        bucket["estimated_usd"] += row.get("estimated_usd", 0)
        bucket["latency_seconds"] += row.get("seconds", 0)
    split_report = {}
    for split, repeats in buckets.items():
        normalized = {}
        for sample, values in sorted(repeats.items()):
            values["estimated_usd"] = round(values["estimated_usd"], 8)
            values["latency_seconds"] = round(values["latency_seconds"], 3)
            values["defect_recall"] = (values["blocking_defects_flagged"] / values["blocking_defects"]
                                       if values["blocking_defects"] else 1.0)
            values["false_flags_per_packet"] = values["false_flags"] / values["packets"]
            values["false_flags_per_clean_after_packet"] = (
                values["false_flags_on_clean_after"] / values["clean_after_packets"]
                if values["clean_after_packets"] else 0.0)
            normalized[str(sample)] = values
        split_report[split] = {
            "per_repeat": normalized,
            "repeat_noise_pstdev": {
                "defect_recall": _noise([item["defect_recall"] for item in normalized.values()]),
                "false_flags_per_packet": _noise([item["false_flags_per_packet"] for item in normalized.values()]),
            },
        }
    return {"threshold": THRESHOLD, "splits": split_report, "cells": cells}


def score_baseline(rows: list[dict], oracles: dict) -> dict:
    buckets: dict[str, dict[int, dict]] = {}
    cells = []
    for row in rows:
        if row["contract"] != "baseline":
            continue
        primary = oracles[row["case"]]["primary"]
        missed_defects = sorted(defect for defect in primary if row["defects"].get(defect) == "missed")
        missed_case = row["expected_verdict"] == "REVISE" and (row["verdict"] != "REVISE" or bool(missed_defects))
        unnecessary = row["expected_verdict"] == "APPROVE" and row["verdict"] == "REVISE"
        cells.append({"case": row["case"], "sample": row["sample"], "split": split_of(row["case"]),
                      "missed_primary_defects": missed_defects, "missed_blocking": missed_case,
                      "unnecessary_revise": unnecessary})
        bucket = buckets.setdefault(split_of(row["case"]), {}).setdefault(row["sample"], {
            "packets": 0, "missed_blocking": 0, "unnecessary_revise": 0,
            "primary_defects": 0, "primary_defects_missed": 0})
        bucket["packets"] += 1
        bucket["missed_blocking"] += int(missed_case)
        bucket["unnecessary_revise"] += int(unnecessary)
        bucket["primary_defects"] += len(primary)
        bucket["primary_defects_missed"] += len(missed_defects)
    split_report = {}
    for split, repeats in buckets.items():
        normalized = {}
        for sample, values in sorted(repeats.items()):
            values["quality"] = 1 - (values["missed_blocking"] + values["unnecessary_revise"]) / values["packets"]
            normalized[str(sample)] = values
        split_report[split] = {"per_repeat": normalized,
                               "repeat_noise_pstdev": {"quality": _noise([item["quality"] for item in normalized.values()])}}
    return {"source": "experiments/reviewer-calibration/runs/rerun-20261016/adjudication.json",
            "contract": "baseline", "splits": split_report, "cells": cells}


def decide(candidate: dict, baseline: dict) -> dict:
    baseline_misses = {(cell["case"], defect) for cell in baseline["cells"] if cell["split"] == "lockbox"
                       for defect in cell["missed_primary_defects"]}
    caught = sorted({(cell["case"], defect) for cell in candidate["cells"] if cell["split"] == "lockbox"
                     for defect in cell["flagged_primary_defects"]} & baseline_misses)
    clean = [cell for cell in candidate["cells"] if cell["case"].endswith("-after")]
    false_average = sum(len(cell["false_flags"]) for cell in clean) / len(clean)
    worth_follow_up = bool(caught) and false_average <= 1
    return {"worth_follow_up": worth_follow_up,
            "baseline_missed_lockbox_defects_flagged": [{"case": case, "defect": defect} for case, defect in caught],
            "false_flags_on_clean_after_packets": sum(len(cell["false_flags"]) for cell in clean),
            "clean_after_packet_repeats": len(clean),
            "false_flags_per_clean_after_packet": false_average,
            "criterion": "at least one baseline-missed primary lockbox defect flagged and <=1 false flag per clean after packet"}


def render_report(record: dict, summary: dict) -> str:
    decision = summary["decision"]
    candidate = summary["candidate"]
    lines = [
        "# Jev per-criterion reviewer coverage experiment",
        "",
        f"Date: {record['timestamps']['reported_at'][:10]}. **Completed: {record['consumption']['provider_calls']} paid calls, no retries.**",
        "",
        "## Decision",
        "",
        ("**Worth a reviewed follow-up.** The fixed decision criterion passed; the proposed amendment is evidence-only pre-screen output handed to the reviewer, never a verdict."
         if decision["worth_follow_up"] else
         "**No change.** The fixed decision criterion did not pass; no reviewer, gate, merge, or verdict policy change is proposed."),
        "",
        f"Baseline-missed primary lockbox defects flagged: `{decision['baseline_missed_lockbox_defects_flagged']}`.",
        f"False flags on clean `after` packet repeats: {decision['false_flags_on_clean_after_packets']} / {decision['clean_after_packet_repeats']} = {decision['false_flags_per_clean_after_packet']:.3f} per packet.",
        "",
        "## Run facts",
        "",
        "- Frozen corpus: reviewer-calibration `splits.json` v2, 11 packets; 3 repeats each.",
        f"- Requested model: `jev-latest`; resolved ids: {', '.join(summary['resolved_models'])}.",
        f"- Calls: {record['consumption']['provider_calls']}; failures: {summary['failures']}; retries: 0.",
        f"- Input/output tokens: {record['consumption']['input_tokens']} / {record['consumption']['output_tokens']}.",
        f"- Estimated cost: ${record['consumption']['estimated_usd']:.8f}; summed request latency: {record['consumption']['latency_seconds']:.3f}s.",
        f"- Largest compressed request: {record['consumption']['max_request_bytes']} bytes (cap {record['limits']['max_request_bytes']}).",
        "",
        "## Split metrics",
        "",
        "```json",
        json.dumps({"candidate": candidate["splits"], "retained_reviewer_baseline": summary["baseline"]["splits"]}, indent=2),
        "```",
        "",
        "Candidate flags use only whole-diff Noul values below 0.5. Defect credit follows the pre-call criterion-to-oracle mapping in `adjudication.json`; an uncovered criterion with no mapped oracle defect is a false flag. Repeat noise is population standard deviation over the fixed repeat metrics. The retained reviewer baseline is not rerun.",
        "",
        "## Limits",
        "",
        "- Small, single-repository corpus: four defect-bearing before packets, four clean after packets, and three additional clean approval packets.",
        "- The model saw acceptance criteria plus changed additions/deletions grouped by file; unchanged hunk context and repository files were absent.",
        "- Criteria are deterministic Markdown units, not independently rewritten atomic claims; compound issue sentences remain a source of correlated flags.",
        "- Cost uses returned input-token usage and the frozen $0.042/million estimate, not an invoice.",
        "- An alias, not an immutable model id, was requested; raw responses retain the resolved id.",
        "",
        "## Verification and artifacts",
        "",
        "```sh",
        "python -m unittest tests.test_jev_review_coverage",
        "python experiments/jev-review-coverage/score.py",
        "```",
        "",
        "`runs/main/manifest.json` pins every source and exact request hash. `runs/main/raw/` retains each request, response, and parsed result. `summary.json` retains the scored cells, baseline comparison, cost, latency, and decision.",
        "",
        "## Schema-1 record",
        "",
        "```json",
        json.dumps(record, indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    manifest = json.loads((RUN / "manifest.json").read_text())
    rows = json.loads((RUN / "results.json").read_text())
    if len(rows) != len(manifest["jobs"]) or any(not row.get("ok") for row in rows):
        raise ValueError("Run is incomplete")
    adjudication = json.loads((ROOT / "adjudication.json").read_text())["cases"]
    mapping = {
        case: {criterion: record["defects"].get(criterion, []) for criterion in
               [item["id"] for item in json.loads((RUN / "raw" / f"{case}.s1.request.json").read_text())["state"]["criteria"]]}
        for case, record in adjudication.items()
    }
    oracles = load_oracles()
    candidate = score_candidate(rows, mapping, oracles)
    baseline_rows = json.loads((CORPUS / "runs" / "rerun-20261016" / "adjudication.json").read_text())
    baseline = score_baseline(baseline_rows, oracles)
    decision = decide(candidate, baseline)
    consumption = {
        "provider_calls": len(rows), "retries": 0,
        "input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
        "output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
        "estimated_usd": round(sum(row["estimated_usd"] for row in rows), 8),
        "latency_seconds": round(sum(row["seconds"] for row in rows), 3),
        "max_request_bytes": max(row["request_wire_bytes"] for row in rows),
    }
    summary = {"schema_version": 1, "candidate": candidate, "baseline": baseline, "decision": decision,
               "resolved_models": sorted({row["model"] for row in rows}),
               "failures": sum(not row["ok"] for row in rows), "consumption": consumption}
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    proposed = ("Ordinary reviewed PR: hand per-criterion and per-file pre-screen probabilities to the reviewer as evidence only; never use them as a verdict."
                if decision["worth_follow_up"] else None)
    record = {
        "schema_version": 1, "experiment": "jev-review-coverage", "run_id": "main",
        "kind": "observation", "lineage": "jev-review-coverage", "iteration": 1,
        "protocol_revision": 1, "protocol_sha256": sha((ROOT / "PROTOCOL.md").read_bytes()),
        "source": {"corpus": "experiments/reviewer-calibration", "splits_version": 2,
                   "manifest": "runs/main/manifest.json", "manifest_sha256": sha((RUN / "manifest.json").read_bytes())},
        "baseline": "reviewer-calibration rerun-20261016 baseline contract",
        "candidate": "jev-latest per-criterion/per-file and whole-diff Noul pre-screen",
        "state": "completed", "reason": None,
        "timestamps": {"prepared_at": manifest["prepared_at"],
                       "started_at": min(json.loads(line)["attempted_at"] for line in
                                         (ROOT / "attempts.jsonl").read_text().splitlines()),
                       "reported_at": datetime.now(UTC).isoformat()},
        "limits": manifest["limits"], "consumption": consumption,
        "observations": {"candidate": candidate["splits"], "baseline": baseline["splits"], "decision": decision},
        "interpretation": ("The fixed follow-up criterion passed." if decision["worth_follow_up"] else "The fixed follow-up criterion did not pass."),
        "limitations": ["Eleven frozen packets from one repository.",
                        "Changed additions/deletions only; unchanged context and repository files were not model state.",
                        "Requested alias may resolve differently in a future run."],
        "evidence": {"mapping": "adjudication.json", "raw": "runs/main/raw", "summary": "summary.json"},
        "proposed_amendment": proposed,
        "decision_owner": "managing agent under initiative #119 delegation",
        "disposition": "follow-up" if decision["worth_follow_up"] else "no-change",
        "note": "Completion is not production acceptance; pre-screen output is evidence, never a verdict.",
    }
    (ROOT / "REPORT.md").write_text(render_report(record, summary))
    print(json.dumps(decision, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
