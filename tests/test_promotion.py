import unittest

from factory.promotion import decide
from factory.promotion import drift, scored, window


def claim(train, held, held_name="lockbox", kind="prompt", contract_version=1):
    body = {"stage": "review", "change": {"kind": kind}, "baseline": "a", "candidate": "b",
            "splits": {"train": dict(zip(("baseline", "candidate"), train)),
                       held_name: dict(zip(("baseline", "candidate"), held))}}
    if contract_version is not None:
        body["contract_version"] = contract_version
    return body


class DecideTest(unittest.TestCase):
    def test_keep_when_train_up_and_lockbox_up(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.65, .65])))[0], "keep")

    def test_revert_when_lockbox_flat(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6])))[0], "revert")

    def test_revert_lockbox_drop_inside_noise(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.5, .7], [.55, .6])))[0], "revert")

    def test_revert_train_only_win(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.4, .4])))[0], "revert")

    def test_revert_gain_inside_noise(self):
        self.assertEqual(decide(claim(([.4, .8], [.6, .8]), ([.6, .6], [.6, .6])))[0], "revert")

    def test_fresh_substitutes_for_lockbox(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.7, .7]), "fresh"))[0], "keep")

    def test_keep_when_lockbox_bump_smaller_than_noise(self):
        # train beats noise; lockbox mean up but delta < lockbox noise → still keep
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.5, .7], [.55, .75])))[0], "keep")

    def test_gate_check_is_a_change_kind(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.65, .65]), kind="gate-check"))[0], "keep")

    def test_contract_violations_rejected(self):
        for bad in (claim(([.5], [.8]), ([.6, .6], [.6, .6])),            # no repeats
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6]), "selection"),  # no held-out
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6]), kind="harness"),
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.65, .65]), contract_version=None),
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.65, .65]), contract_version=2)):
            with self.assertRaises(ValueError):
                decide(bad)


def score(efficiency, gate_first_pass=True, compliance=0.8, **extra):
    return {"event": "score", "efficiency": efficiency, "gate_first_pass": gate_first_pass, "compliance": compliance, **extra}


class DriftTest(unittest.TestCase):
    """Online keep/revert for a merged lessons/curate PR (#238)."""

    before = [score(0.9), score(0.8), score(0.9), score(0.85), score(0.9, False, None),
              score(0.9), score(0.8), score(0.9), score(0.85), score(0.9)]

    def test_window_means_skip_unjudged_compliance_and_handle_empty(self):
        self.assertEqual(window(self.before), {"efficiency_mean": 0.87, "compliance_mean": 0.8,
                                               "gate_first_pass_rate": 0.9, "n": 10})
        self.assertEqual(window([]), {"efficiency_mean": None, "compliance_mean": None, "gate_first_pass_rate": None, "n": 0})

    def test_scored_drops_skipped_rows(self):
        self.assertEqual(scored([{"event": "score", "skipped": "missing"}, {"event": "attempt"}, score(1)]), [score(1)])

    def test_keep_inside_noise(self):
        verdict, reason = drift(self.before, [score(0.85)] * 10)  # efficiency -0.02 within pstdev 0.04
        self.assertEqual(verdict, "keep")
        self.assertIn("efficiency_mean 0.870 -> 0.850", reason)

    def test_revert_when_efficiency_drops_beyond_noise(self):
        verdict, reason = drift(self.before, [score(0.8)] * 10)  # -0.07 > 0.04
        self.assertEqual((verdict, reason), ("revert", "efficiency_mean 0.870 -> 0.800 (-0.070, noise 0.040) beyond noise"))

    def test_revert_when_first_pass_rate_drops_beyond_noise(self):
        self.assertEqual(drift(self.before, [score(0.9, False)] * 5 + [score(0.9)] * 5)[0], "revert")  # 0.9 -> 0.5, noise 0.3
        self.assertEqual(drift(self.before, [score(0.9, False)] * 2 + [score(0.9)] * 8)[0], "keep")  # 0.9 -> 0.8

    def test_no_baseline_keeps(self):
        self.assertEqual(drift([], [score(0.1)] * 10)[0], "keep")
        self.assertEqual(drift([score(1.0)], [score(0.99)] * 10)[0], "revert")  # a one-row baseline has zero noise
