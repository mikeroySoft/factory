import unittest

import analyze


def run(seconds=1.0, fails=(), errors=(), tbs=None, n=10):
    return {"seconds": seconds, "tests_run": n, "failure_ids": list(fails), "error_ids": list(errors),
            "tracebacks": tbs or {}}


class Validity(unittest.TestCase):
    def test_same_set_matches_and_different_set_does_not(self):
        self.assertTrue(analyze.same_outcome(run(fails=["a"]), run(fails=["a"])))
        self.assertFalse(analyze.same_outcome(run(fails=["a"]), run(fails=["b"])))
        self.assertFalse(analyze.same_outcome(run(errors=["a"]), run(fails=["a"])))
        self.assertFalse(analyze.same_outcome(run(n=10), run(n=11)))

    def test_control1_must_be_fully_clean(self):
        tb = "CalledProcessError: Command '['uv', 'sync', '--project', 'x', '--frozen']' returned"
        self.assertEqual(analyze.unexpected(run()), [])
        self.assertEqual(analyze.unexpected(run(errors=["a"], tbs={"a": tb})), ["a"])
        self.assertEqual(analyze.unexpected(run(fails=["b"])), ["b"])


class VmCpu(unittest.TestCase):
    def test_arm_compares_with_preceding_control(self):
        self.assertTrue(analyze.vm_ok("ram", {"control-1": 27.0, "ram": 37.0}))
        self.assertFalse(analyze.vm_ok("ram", {"control-1": 27.0, "ram": 37.5}))

    def test_control_compares_with_preceding_control_and_arm(self):
        self.assertFalse(analyze.vm_ok("control-2", {"control-1": 20, "ram": 40, "control-2": 25}))
        self.assertTrue(analyze.vm_ok("control-2", {"control-1": 20, "ram": 28, "control-2": 25}))
        self.assertTrue(analyze.vm_ok("control-1", {"control-1": 90}))


class Credit(unittest.TestCase):
    def test_saving_above_spread_is_credited_and_inside_spread_is_zero(self):
        s = {"control-1": 1600, "ram": 1000, "control-2": 1620, "git-cache": 1600,
             "control-3": 1610, "scratch-throttle": 1500, "control-4": 1600}
        r = analyze.credit(s)
        self.assertEqual(r["spread"], 20)
        self.assertEqual(r["arms"]["ram"]["credited"], 610)
        self.assertEqual(r["arms"]["git-cache"]["credited"], 0.0)  # delta 15 <= spread 20
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_total_of_798_passes_and_unmeasured_arm_gets_zero(self):
        s = {"control-1": 1600, "ram": 802, "control-2": 1600, "control-3": 1600, "control-4": 1600}
        r = analyze.credit(s)
        self.assertEqual(r["total_credited"], 798)
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(r["arms"]["git-cache"]["measured"])


if __name__ == "__main__":
    unittest.main()
