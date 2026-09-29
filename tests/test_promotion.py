import unittest

from factory.promotion import decide


def claim(train, held, held_name="lockbox", kind="prompt"):
    return {"stage": "review", "change": {"kind": kind}, "baseline": "a", "candidate": "b",
            "splits": {"train": dict(zip(("baseline", "candidate"), train)),
                       held_name: dict(zip(("baseline", "candidate"), held))}}


class DecideTest(unittest.TestCase):
    def test_keep_when_train_up_and_lockbox_holds(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6])))[0], "keep")

    def test_revert_train_only_win(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.4, .4])))[0], "revert")

    def test_revert_gain_inside_noise(self):
        self.assertEqual(decide(claim(([.4, .8], [.6, .8]), ([.6, .6], [.6, .6])))[0], "revert")

    def test_fresh_substitutes_for_lockbox(self):
        self.assertEqual(decide(claim(([.5, .5], [.8, .8]), ([.6, .6], [.7, .7]), "fresh"))[0], "keep")

    def test_contract_violations_rejected(self):
        for bad in (claim(([.5], [.8]), ([.6, .6], [.6, .6])),            # no repeats
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6]), "selection"),  # no held-out
                    claim(([.5, .5], [.8, .8]), ([.6, .6], [.6, .6]), kind="harness")):
            with self.assertRaises(ValueError):
                decide(bad)
