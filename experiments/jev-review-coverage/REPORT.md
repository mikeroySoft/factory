# Jev per-criterion reviewer coverage experiment

Date: 2026-10-23. **Completed: 33 paid calls, no retries.**

## Decision

**No change.** The fixed decision criterion did not pass; no reviewer, gate, merge, or verdict policy change is proposed.

Baseline-missed primary lockbox defects flagged: `[]`.
False flags on clean `after` packet repeats: 16 / 12 = 1.333 per packet.

## Run facts

- Frozen corpus: reviewer-calibration `splits.json` v2, 11 packets; 3 repeats each.
- Requested model: `jev-latest`; resolved ids: jev-1.14.0.
- Calls: 33; failures: 0; retries: 0.
- Input/output tokens: 487740 / 146805.
- Estimated cost: $0.02048508; summed request latency: 7.852s.
- Largest compressed request: 33808 bytes (cap 100000).

## Split metrics

```json
{
  "candidate": {
    "train": {
      "per_repeat": {
        "1": {
          "packets": 6,
          "blocking_defects": 3,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 3,
          "false_flags": 5,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 2,
          "input_tokens": 34419,
          "output_tokens": 8367,
          "estimated_usd": 0.0014456,
          "latency_seconds": 1.009,
          "defect_recall": 0.0,
          "false_flags_per_packet": 0.8333333333333334,
          "false_flags_per_clean_after_packet": 1.0
        },
        "2": {
          "packets": 6,
          "blocking_defects": 3,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 3,
          "false_flags": 6,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 2,
          "input_tokens": 34419,
          "output_tokens": 8367,
          "estimated_usd": 0.0014456,
          "latency_seconds": 1.024,
          "defect_recall": 0.0,
          "false_flags_per_packet": 1.0,
          "false_flags_per_clean_after_packet": 1.0
        },
        "3": {
          "packets": 6,
          "blocking_defects": 3,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 3,
          "false_flags": 6,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 2,
          "input_tokens": 34419,
          "output_tokens": 8367,
          "estimated_usd": 0.0014456,
          "latency_seconds": 0.955,
          "defect_recall": 0.0,
          "false_flags_per_packet": 1.0,
          "false_flags_per_clean_after_packet": 1.0
        }
      },
      "repeat_noise_pstdev": {
        "defect_recall": 0.0,
        "false_flags_per_packet": 0.0786
      }
    },
    "lockbox": {
      "per_repeat": {
        "1": {
          "packets": 5,
          "blocking_defects": 2,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 2,
          "false_flags": 9,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 3,
          "input_tokens": 128161,
          "output_tokens": 40568,
          "estimated_usd": 0.00538276,
          "latency_seconds": 1.607,
          "defect_recall": 0.0,
          "false_flags_per_packet": 1.8,
          "false_flags_per_clean_after_packet": 1.5
        },
        "2": {
          "packets": 5,
          "blocking_defects": 2,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 2,
          "false_flags": 8,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 4,
          "input_tokens": 128161,
          "output_tokens": 40568,
          "estimated_usd": 0.00538276,
          "latency_seconds": 1.685,
          "defect_recall": 0.0,
          "false_flags_per_packet": 1.6,
          "false_flags_per_clean_after_packet": 2.0
        },
        "3": {
          "packets": 5,
          "blocking_defects": 2,
          "blocking_defects_flagged": 0,
          "blocking_defects_missed": 2,
          "false_flags": 6,
          "clean_after_packets": 2,
          "false_flags_on_clean_after": 3,
          "input_tokens": 128161,
          "output_tokens": 40568,
          "estimated_usd": 0.00538276,
          "latency_seconds": 1.572,
          "defect_recall": 0.0,
          "false_flags_per_packet": 1.2,
          "false_flags_per_clean_after_packet": 1.5
        }
      },
      "repeat_noise_pstdev": {
        "defect_recall": 0.0,
        "false_flags_per_packet": 0.2494
      }
    }
  },
  "retained_reviewer_baseline": {
    "train": {
      "per_repeat": {
        "1": {
          "packets": 6,
          "missed_blocking": 2,
          "unnecessary_revise": 0,
          "primary_defects": 3,
          "primary_defects_missed": 2,
          "quality": 0.6666666666666667
        },
        "2": {
          "packets": 6,
          "missed_blocking": 2,
          "unnecessary_revise": 0,
          "primary_defects": 3,
          "primary_defects_missed": 3,
          "quality": 0.6666666666666667
        }
      },
      "repeat_noise_pstdev": {
        "quality": 0.0
      }
    },
    "lockbox": {
      "per_repeat": {
        "1": {
          "packets": 5,
          "missed_blocking": 1,
          "unnecessary_revise": 1,
          "primary_defects": 2,
          "primary_defects_missed": 1,
          "quality": 0.6
        },
        "2": {
          "packets": 5,
          "missed_blocking": 2,
          "unnecessary_revise": 1,
          "primary_defects": 2,
          "primary_defects_missed": 2,
          "quality": 0.4
        }
      },
      "repeat_noise_pstdev": {
        "quality": 0.1
      }
    }
  }
}
```

Candidate flags use only whole-diff Noul values below 0.5. Defect credit follows the pre-call criterion-to-oracle mapping in `adjudication.json`; an uncovered criterion with no mapped oracle defect is a false flag. Repeat noise is population standard deviation over the fixed repeat metrics. The retained reviewer baseline is not rerun.

## Limits

- Small, single-repository corpus: four defect-bearing before packets, four clean after packets, and three additional clean approval packets.
- The model saw acceptance criteria plus changed additions/deletions grouped by file; unchanged hunk context and repository files were absent.
- Criteria are deterministic Markdown units, not independently rewritten atomic claims; compound issue sentences remain a source of correlated flags.
- Cost uses returned input-token usage and the frozen $0.042/million estimate, not an invoice.
- An alias, not an immutable model id, was requested; raw responses retain the resolved id.

## Verification and artifacts

```sh
python -m unittest tests.test_jev_review_coverage
python experiments/jev-review-coverage/score.py
```

`runs/main/manifest.json` pins every source and exact request hash. `runs/main/raw/` retains each request, response, and parsed result. `summary.json` retains the scored cells, baseline comparison, cost, latency, and decision.

## Schema-1 record

```json
{
  "schema_version": 1,
  "experiment": "jev-review-coverage",
  "run_id": "main",
  "kind": "observation",
  "lineage": "jev-review-coverage",
  "iteration": 1,
  "protocol_revision": 1,
  "protocol_sha256": "0d5a74a248c82cda3dfd29e101eaf88d1a2386263485c3e076a0168069548d27",
  "source": {
    "corpus": "experiments/reviewer-calibration",
    "splits_version": 2,
    "manifest": "runs/main/manifest.json",
    "manifest_sha256": "15cb200851eb41ce3c583c1d1c2b163138256f4d769577a8e321682814497589"
  },
  "baseline": "reviewer-calibration rerun-20261016 baseline contract",
  "candidate": "jev-latest per-criterion/per-file and whole-diff Noul pre-screen",
  "state": "completed",
  "reason": null,
  "timestamps": {
    "prepared_at": "2026-10-23T01:25:56.583162+00:00",
    "started_at": "2026-10-23T01:26:53.431826+00:00",
    "reported_at": "2026-10-23T01:27:41.119504+00:00"
  },
  "limits": {
    "max_calls": 33,
    "max_request_bytes": 100000,
    "reserved_input_tokens_per_call": 100000,
    "max_spend_usd": 0.25,
    "max_seconds": 2700,
    "retries": 0
  },
  "consumption": {
    "provider_calls": 33,
    "retries": 0,
    "input_tokens": 487740,
    "output_tokens": 146805,
    "estimated_usd": 0.02048508,
    "latency_seconds": 7.852,
    "max_request_bytes": 33808
  },
  "observations": {
    "candidate": {
      "train": {
        "per_repeat": {
          "1": {
            "packets": 6,
            "blocking_defects": 3,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 3,
            "false_flags": 5,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 2,
            "input_tokens": 34419,
            "output_tokens": 8367,
            "estimated_usd": 0.0014456,
            "latency_seconds": 1.009,
            "defect_recall": 0.0,
            "false_flags_per_packet": 0.8333333333333334,
            "false_flags_per_clean_after_packet": 1.0
          },
          "2": {
            "packets": 6,
            "blocking_defects": 3,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 3,
            "false_flags": 6,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 2,
            "input_tokens": 34419,
            "output_tokens": 8367,
            "estimated_usd": 0.0014456,
            "latency_seconds": 1.024,
            "defect_recall": 0.0,
            "false_flags_per_packet": 1.0,
            "false_flags_per_clean_after_packet": 1.0
          },
          "3": {
            "packets": 6,
            "blocking_defects": 3,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 3,
            "false_flags": 6,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 2,
            "input_tokens": 34419,
            "output_tokens": 8367,
            "estimated_usd": 0.0014456,
            "latency_seconds": 0.955,
            "defect_recall": 0.0,
            "false_flags_per_packet": 1.0,
            "false_flags_per_clean_after_packet": 1.0
          }
        },
        "repeat_noise_pstdev": {
          "defect_recall": 0.0,
          "false_flags_per_packet": 0.0786
        }
      },
      "lockbox": {
        "per_repeat": {
          "1": {
            "packets": 5,
            "blocking_defects": 2,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 2,
            "false_flags": 9,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 3,
            "input_tokens": 128161,
            "output_tokens": 40568,
            "estimated_usd": 0.00538276,
            "latency_seconds": 1.607,
            "defect_recall": 0.0,
            "false_flags_per_packet": 1.8,
            "false_flags_per_clean_after_packet": 1.5
          },
          "2": {
            "packets": 5,
            "blocking_defects": 2,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 2,
            "false_flags": 8,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 4,
            "input_tokens": 128161,
            "output_tokens": 40568,
            "estimated_usd": 0.00538276,
            "latency_seconds": 1.685,
            "defect_recall": 0.0,
            "false_flags_per_packet": 1.6,
            "false_flags_per_clean_after_packet": 2.0
          },
          "3": {
            "packets": 5,
            "blocking_defects": 2,
            "blocking_defects_flagged": 0,
            "blocking_defects_missed": 2,
            "false_flags": 6,
            "clean_after_packets": 2,
            "false_flags_on_clean_after": 3,
            "input_tokens": 128161,
            "output_tokens": 40568,
            "estimated_usd": 0.00538276,
            "latency_seconds": 1.572,
            "defect_recall": 0.0,
            "false_flags_per_packet": 1.2,
            "false_flags_per_clean_after_packet": 1.5
          }
        },
        "repeat_noise_pstdev": {
          "defect_recall": 0.0,
          "false_flags_per_packet": 0.2494
        }
      }
    },
    "baseline": {
      "train": {
        "per_repeat": {
          "1": {
            "packets": 6,
            "missed_blocking": 2,
            "unnecessary_revise": 0,
            "primary_defects": 3,
            "primary_defects_missed": 2,
            "quality": 0.6666666666666667
          },
          "2": {
            "packets": 6,
            "missed_blocking": 2,
            "unnecessary_revise": 0,
            "primary_defects": 3,
            "primary_defects_missed": 3,
            "quality": 0.6666666666666667
          }
        },
        "repeat_noise_pstdev": {
          "quality": 0.0
        }
      },
      "lockbox": {
        "per_repeat": {
          "1": {
            "packets": 5,
            "missed_blocking": 1,
            "unnecessary_revise": 1,
            "primary_defects": 2,
            "primary_defects_missed": 1,
            "quality": 0.6
          },
          "2": {
            "packets": 5,
            "missed_blocking": 2,
            "unnecessary_revise": 1,
            "primary_defects": 2,
            "primary_defects_missed": 2,
            "quality": 0.4
          }
        },
        "repeat_noise_pstdev": {
          "quality": 0.1
        }
      }
    },
    "decision": {
      "worth_follow_up": false,
      "baseline_missed_lockbox_defects_flagged": [],
      "false_flags_on_clean_after_packets": 16,
      "clean_after_packet_repeats": 12,
      "false_flags_per_clean_after_packet": 1.3333333333333333,
      "criterion": "at least one baseline-missed primary lockbox defect flagged and <=1 false flag per clean after packet"
    }
  },
  "interpretation": "The fixed follow-up criterion did not pass.",
  "limitations": [
    "Eleven frozen packets from one repository.",
    "Changed additions/deletions only; unchanged context and repository files were not model state.",
    "Requested alias may resolve differently in a future run."
  ],
  "evidence": {
    "mapping": "adjudication.json",
    "raw": "runs/main/raw",
    "summary": "summary.json"
  },
  "proposed_amendment": null,
  "decision_owner": "managing agent under initiative #119 delegation",
  "disposition": "no-change",
  "note": "Completion is not production acceptance; pre-screen output is evidence, never a verdict."
}
```
