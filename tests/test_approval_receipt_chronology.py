"""#855: receipt publication cannot renew old evidence or exhaust read bounds."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from factory import decisions, lifecycle
from tests import test_cross_host_decisions as cross


class ReceiptChronologyTest(unittest.TestCase):
    def setUp(self):
        self.real_run = decisions._run
        self.case = cross.CrossHostDecisionTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_future_receipt_or_evidence_refuses_prepare_and_apply(self):
        for defect in ("receipt", "evidence", "undated"):
            for phase in ("prepare", "apply"):
                with self.subTest(defect=defect, phase=phase):
                    receipt = self.case.share()
                    preview = self.case.prepare() if phase == "apply" else None
                    if defect == "receipt":
                        receipt["at"] = "2099-01-01T00:00:00Z"
                    elif defect == "evidence":
                        receipt["evidence"][1]["at"] = "2099-01-01T00:00:00Z"
                    else:
                        receipt["evidence"][1].pop("at")
                    self.case.comments[0]["body"] = "Factory approval v1\n" + json.dumps(receipt)
                    with self.assertRaises(decisions.DecisionError) as caught:
                        self.case.driver.apply(preview) if preview else self.case.prepare()
                    self.assertEqual(caught.exception.code, "approval_evidence")
                    self.assertEqual(self.case.driver.calls, [])

    def test_republication_cannot_supersede_newer_local_refusal(self):
        for phase in ("prepare", "apply"):
            with self.subTest(phase=phase):
                (self.case.cfg.factory / "events.jsonl").unlink(missing_ok=True)
                receipt = self.case.share()
                preview = self.case.prepare() if phase == "apply" else None
                lifecycle.append(self.case.cfg.factory / "events.jsonl", {
                    "event": "review", "ticket": 7, "head": self.case.head,
                    "actual_head": self.case.head, "verdict": "REVISE", "accepted": True,
                    "at": "2026-11-18T19:16:00Z",
                })
                # Publishing again (even with a new gate) does not renew the old review.
                receipt["at"] = "2026-11-18T19:17:00Z"
                receipt["evidence"][0]["at"] = receipt["at"]
                self.case.comments[0].update(
                    body="Factory approval v1\n" + json.dumps(receipt),
                    created_at="2026-11-18T19:17:01Z", updated_at="2026-11-18T19:17:01Z",
                )
                with self.assertRaises(decisions.DecisionError) as caught:
                    self.case.driver.apply(preview) if preview else self.case.prepare()
                self.assertEqual(caught.exception.code, "approval_evidence")
                self.assertEqual(self.case.driver.calls, [])

    def test_unrelated_discussion_is_filtered_before_bounded_capture(self):
        self.case.share()
        comments = [
            {"user": {"login": "factory"}, "body": "Review\n" + "x" * 20_000},
            {"user": {"login": "outsider"}, "body": "Factory approval v1\n" + "x" * 20_000},
        ] * 20 + self.case.comments
        payload = json.dumps(comments).encode()
        self.assertGreater(len(payload), decisions.READ_CAP)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        endpoint = f"repos/{self.case.cfg.repo}/issues/17/comments"

        def transport(argv, **kwargs):
            if endpoint in argv:
                kwargs["env"] = {**kwargs["env"], "GH_TOKEN": "local-test-token"}
                # Real gh applies the production filter before the real capped reader.
                args = [arg for arg in argv[2:] if arg not in ("--hostname", "github.com")]
                args[args.index(endpoint)] = f"http://127.0.0.1:{server.server_port}/comments"
                return self.real_run(["gh", "api", *args], **kwargs)
            return self.case.transport(argv, **kwargs)

        decisions._run.side_effect = transport
        preview = self.case.prepare()
        self.assertEqual(preview["targets"][0]["head"], self.case.head)
        self.assertTrue(self.case.driver.apply(preview)["ok"])


if __name__ == "__main__":
    unittest.main()
