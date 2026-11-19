"""Decision-owner section boundaries through the routing entry point."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory import config, plan


class DecisionOwnerTest(unittest.TestCase):
    def route(self, body):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config.Config(root=Path(directory), repo="example/repo",
                                collaboration={"reasons": {"ci": "ci-owner"}})
            result = {}
            reader = plan.Reader(cfg, result)
            responses = {"issues/718": {"number": 718, "body": body},
                         "": {"owner": {"type": "Organization"}}}
            with patch.object(reader, "fetch", side_effect=lambda label, path: responses[path]):
                reader.route(718, "ci", [])
            return result["route"]

    def test_owner_stops_at_heading_or_blank_line(self):
        for owner, expected in (("FactoryPMLogin", "FactoryPMLogin"), ("@example/pm", "@example/pm")):
            for ending in ("\n## Summary\nThis is the ticket summary.",
                           "\n\n## Summary\nThis is the ticket summary.",
                           "\n \t\nThis is the ticket summary.",
                           "\n**Acceptance**\nThis is the ticket acceptance.", ""):
                with self.subTest(owner=owner, ending=ending):
                    route = self.route(f"**Decision owner**\n{owner}{ending}")
                    self.assertEqual((route["status"], route["owner"], route["source"]),
                                     ("selected", expected, "decision_owner"))

    def test_invalid_owner_never_guesses_later_owner(self):
        for owner in ("", "Factory PM", "@first @second", "@first\n@second", "FactoryPMLogin\nSome prose."):
            with self.subTest(owner=owner):
                route = self.route(f"**Decision owner**\n{owner}\n## Summary\n@not-the-owner"
                                   "\n\n**Decision owner**\n@later-owner")
                self.assertEqual((route["status"], route["owner"], route["source"]),
                                 ("invalid", None, "decision_owner"))
                self.assertEqual([(step["step"], step["outcome"]) for step in route["provenance"]],
                                 [("decision_owner", "invalid")])
