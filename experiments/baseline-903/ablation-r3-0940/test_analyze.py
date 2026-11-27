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
        self.assertTrue(analyze.vm_ok("git-cache", {"control-1": 27.0, "git-cache": 37.0}))
        self.assertFalse(analyze.vm_ok("git-cache", {"control-1": 27.0, "git-cache": 37.5}))

    def test_control_compares_with_preceding_control_and_arm(self):
        self.assertFalse(analyze.vm_ok("control-2", {"control-1": 20, "git-cache": 40, "control-2": 25}))
        self.assertTrue(analyze.vm_ok("control-2", {"control-1": 20, "git-cache": 28, "control-2": 25}))
        self.assertTrue(analyze.vm_ok("control-1", {"control-1": 90}))


class FilenameOnly(unittest.TestCase):
    def test_only_extra_filename_failures_count_as_inapplicable(self):
        a, b = sorted(analyze.FILENAME_TESTS)
        self.assertTrue(analyze.filename_only(run(), run(fails=[a, b])))
        self.assertFalse(analyze.filename_only(run(), run(fails=[a, "other"])))
        self.assertFalse(analyze.filename_only(run(), run()))
        self.assertFalse(analyze.filename_only(run(), run(fails=[a], errors=["e"])))


class Credit(unittest.TestCase):
    def test_saving_above_spread_is_credited_and_inside_spread_is_zero(self):
        s = {"control-1": 1600, "git-cache": 1000, "control-2": 1620, "scratch-throttle": 1600,
             "control-3": 1610, "ram-apfs": 1500, "control-4": 1600}
        r = analyze.credit(s)
        self.assertEqual(r["spread"], 20)
        self.assertEqual(r["arms"]["git-cache"]["credited"], 610)
        self.assertTrue(r["arms"]["git-cache"]["fix_ticket"])
        self.assertEqual(r["arms"]["scratch-throttle"]["credited"], 0.0)  # delta 15 <= spread 20
        self.assertEqual(r["verdict"], "INCONCLUSIVE")

    def test_total_of_798_passes_and_unmeasured_arm_gets_zero(self):
        s = {"control-1": 1600, "git-cache": 802, "control-2": 1600, "control-3": 1600, "control-4": 1600}
        r = analyze.credit(s)
        self.assertEqual(r["total_credited"], 798)
        self.assertEqual(r["verdict"], "PASS")
        self.assertFalse(r["arms"]["ram-apfs"]["measured"])


if __name__ == "__main__":
    unittest.main()
