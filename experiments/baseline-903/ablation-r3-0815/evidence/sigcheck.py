import sys, unittest
sys.path.insert(0, "/Users/mike/tmp/r3-0735/harness")
import arm_runner
arm_runner._restore_default_signals()
sys.argv = ["x", "test_interrupts", "test_host_mutation_slots"]
unittest.main(module=None, argv=["x", "discover", "-s", "tests", "-p", "test_host_mutation_slots.py"], exit=False)
