"""#535: model IDs compare by identity, never by lexical ordering."""
from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

from factory import config, dispatch
from tests import test_manager_fix_outcomes as fix_suite


class WorkerModelBoundaryTest(unittest.TestCase):
    setUp = fix_suite.ManagerFixOutcomeTest.setUp
    patched = fix_suite.ManagerFixOutcomeTest.patched
    events = fix_suite.ManagerFixOutcomeTest.events
    prepare_fix = fix_suite.ManagerFixOutcomeTest.prepare_fix

    def test_unqualified_models_on_either_side_of_the_pin_report_a_mismatch(self) -> None:
        self.prepare_fix()
        self.cfg.worker_usage["default"] = config.usage_spec("default", {"model": "result.model"})
        for model, mismatch in (("model-a", True), ("model-b", False), ("model-c", True)):
            with self.subTest(model=model):
                self.cfg.workers["default"] = [
                    sys.executable, "-c", f"print({json.dumps({'result': {'model': model}})!r})",
                    "--model", "provider/model-b",
                ]
                before = len(self.events("worker-model-mismatch"))
                with self.patched(), \
                        mock.patch.object(dispatch, "build_prompt", return_value="prompt"), \
                        mock.patch.object(dispatch, "commit_leftovers"), \
                        mock.patch.object(dispatch, "run_gate", return_value=(True, "PASS")):
                    dispatch.worker_round(9, self.wt, set(), "change", "", 1, float("inf"))
                self.assertEqual(len(self.events("worker-model-mismatch")) - before, int(mismatch))
                usage = self.events("llm-usage")[-1]
                self.assertEqual((usage["worker_model"], usage["model"]), ("provider/model-b", model))
                self.assertEqual(self.events("attempt")[-1]["worker_model"], "provider/model-b")
                if mismatch:
                    self.assertEqual(self.events("worker-model-mismatch")[-1]["reported_model"], model)


if __name__ == "__main__":
    unittest.main()
