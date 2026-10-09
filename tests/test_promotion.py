import unittest

from factory.promotion import decide


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
