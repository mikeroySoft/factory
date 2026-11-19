"""#855: shared approval receipts require provider-authenticated edit metadata."""
from __future__ import annotations

import json
import unittest

from factory import decisions
from tests import test_cross_host_decisions as cross


class ApprovalEditMetadataTest(unittest.TestCase):
    NODE_ID = "IC_factory_approval"

    def setUp(self):
        self.case = cross.CrossHostDecisionTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.graphql_response = self._metadata(None)
        self.lookup_failure = False
        self.lookup_calls: list[list[str]] = []
        decisions._run.side_effect = self.transport

    def _metadata(self, last_edited_at):
        return {"data": {"nodes": [{"id": self.NODE_ID, "lastEditedAt": last_edited_at}]}}

    def transport(self, argv, **kwargs):
        if argv[:2] == ["gh", "api"] and "graphql" in argv:
            self.lookup_calls.append(argv)
            if self.lookup_failure:
                return self.case.driver.completed(argv, code=1, err="edit metadata unavailable")
            return self.case.driver.completed(argv, out=json.dumps(self.graphql_response))
        return self.case.transport(argv, **kwargs)

    def _share(self):
        self.case.share()
        self.case.comments[0]["node_id"] = self.NODE_ID
        self.graphql_response = self._metadata(None)
        self.lookup_failure = False
        self.lookup_calls.clear()

    def _refuse_after(self, phase, change, code):
        self._share()
        preview = self.case.prepare() if phase == "apply" else None
        change()
        with self.assertRaises(decisions.DecisionError) as caught:
            self.case.driver.apply(preview) if preview else self.case.prepare()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.case.driver.calls, [])

    def test_same_second_rest_timestamps_do_not_hide_provider_edit(self):
        for phase in ("prepare", "apply"):
            with self.subTest(phase=phase):
                self._refuse_after(
                    phase,
                    lambda: setattr(
                        self,
                        "graphql_response",
                        self._metadata("2026-11-18T19:15:24Z"),
                    ),
                    "approval_evidence",
                )
                self.assertTrue(self.lookup_calls)

    def test_absent_or_malformed_edit_metadata_refuses(self):
        cases = (
            ("node_id", lambda: self.case.comments[0].pop("node_id")),
            ("node", lambda: setattr(self, "graphql_response", {"data": {"nodes": []}})),
            (
                "lastEditedAt",
                lambda: setattr(
                    self,
                    "graphql_response",
                    {"data": {"nodes": [{"id": self.NODE_ID}]},},
                ),
            ),
            ("malformed", lambda: setattr(self, "graphql_response", self._metadata(7))),
        )
        for defect, change in cases:
            for phase in ("prepare", "apply"):
                with self.subTest(defect=defect, phase=phase):
                    self._refuse_after(phase, change, "approval_evidence")

    def test_unedited_null_metadata_is_accepted(self):
        for phase in ("prepare", "apply"):
            with self.subTest(phase=phase):
                self._share()
                preview = self.case.prepare()
                if phase == "apply":
                    self.assertTrue(self.case.driver.apply(preview)["ok"])
                self.assertTrue(self.lookup_calls)
                self.assertTrue(any(
                    self.NODE_ID in argument
                    for call in self.lookup_calls
                    for argument in call
                ))

    def test_provider_edit_lookup_failure_refuses(self):
        for phase in ("prepare", "apply"):
            with self.subTest(phase=phase):
                self._refuse_after(
                    phase,
                    lambda: setattr(self, "lookup_failure", True),
                    "provider_unavailable",
                )


if __name__ == "__main__":
    unittest.main()
