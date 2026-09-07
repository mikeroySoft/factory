"""RESPONSE_CAP sizing: trimmed envelopes must always fit the cap in bytes."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from factory import evidence


def envelope():
    # observe-style envelope, intentionally too big for the cap under test
    return {
        "ok": True,
        "coverage": {"status": "bounded", "notices": []},
        "errors": [],
        "cases": [{"number": 1, "title": "x" * 1000},
                  {"number": 2, "title": "x" * 1000}],
        "attention_count": 2,
        "sources": [],
    }


class BoundedOutputTest(unittest.TestCase):
    def test_attention_diagnostic_and_integer_to_null_trimming_fit_the_cap(self):
        for cap in (1297, 1298, 1299):
            with self.subTest(cap=cap):
                with patch.object(evidence, "RESPONSE_CAP", cap):
                    output, trimmed = evidence.bounded_output(envelope())
                self.assertTrue(trimmed)
                self.assertLessEqual(len(output.encode("ascii")), cap)
                result = json.loads(output)
                self.assertEqual(result["cases"], [])
                self.assertIsNone(result["attention_count"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["coverage"]["status"], "partial")
                self.assertTrue(any(notice.startswith("Attention count unavailable:")
                                    for notice in result["coverage"]["notices"]))
                count_error = {"source": result["sources"][0]["id"],
                               "scope": "attention_count", "code": "output_truncated"}
                self.assertIn(count_error, result["errors"])
                self.assertIn({"source": "output", "scope": "response",
                               "code": "output_truncated"}, result["errors"])
                self.assertEqual(json.loads(result["sources"][0]["text"])["reasons"],
                                 ["output_truncated"])



if __name__ == "__main__":
    unittest.main()