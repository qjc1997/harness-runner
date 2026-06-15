"""Tests for the black-box validator's result parsing (the deterministic part).

The validation itself is agent-driven (LLM + running app), so these tests cover
the BLACKBOX_RESULT_JSON parsing, verdict mapping, and report shaping — the logic
that turns the agent's output into a structured, actionable result.

    PYTHONPATH=src python3 -m unittest tests.test_blackbox_validator -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_runner.roles.blackbox_validator import _parse_result  # noqa: E402


class ParseResultTest(unittest.TestCase):
    def test_fail_with_quoted_actual(self):
        text = (
            "I ran the cases.\n"
            'BLACKBOX_RESULT_JSON: {"verdict":"fail","mock_detected":false,'
            '"mock_detail":"","cases":[{"id":"bocca","input":"url","expected":"identify Bocca",'
            '"actual":"This is Marforio, an ancient Roman sculpture...","result":"fail",'
            '"evidence":"confidently named wrong item"}],"summary":"wrong id"}'
        )
        r = _parse_result(text, 0.5)
        self.assertEqual(r.verdict, "fail")
        self.assertEqual(len(r.cases), 1)
        self.assertEqual(r.cases[0].result, "fail")
        self.assertIn("Marforio", r.cases[0].actual)
        self.assertEqual(r.cost_usd, 0.5)

    def test_mock_detected_inconclusive(self):
        text = (
            'BLACKBOX_RESULT_JSON: {"verdict":"inconclusive","mock_detected":true,'
            '"mock_detail":"VLM at :11434 returns identical generic text for all inputs",'
            '"cases":[{"id":"c1","result":"inconclusive","actual":"generic boilerplate"}],'
            '"summary":"VLM mocked"}'
        )
        r = _parse_result(text, 0.1)
        self.assertEqual(r.verdict, "inconclusive")
        self.assertTrue(r.mock_detected)
        self.assertIn("11434", r.mock_detail)

    def test_all_pass(self):
        text = (
            'BLACKBOX_RESULT_JSON: {"verdict":"pass","mock_detected":false,"cases":'
            '[{"id":"colosseum","result":"pass","actual":"The Colosseum, Flavian Amphitheatre..."}],'
            '"summary":"ok"}'
        )
        r = _parse_result(text, 0.0)
        self.assertEqual(r.verdict, "pass")
        self.assertEqual(r.cases[0].result, "pass")

    def test_no_json_block_is_error(self):
        r = _parse_result("I could not finish the validation.", 0.0)
        self.assertEqual(r.verdict, "error")
        self.assertIsNotNone(r.error)

    def test_malformed_json_is_error(self):
        r = _parse_result("BLACKBOX_RESULT_JSON: {not valid json", 0.0)
        self.assertEqual(r.verdict, "error")

    def test_unknown_verdict_defaults_inconclusive(self):
        text = 'BLACKBOX_RESULT_JSON: {"verdict":"green","cases":[]}'
        r = _parse_result(text, 0.0)
        self.assertEqual(r.verdict, "inconclusive")


if __name__ == "__main__":
    unittest.main(verbosity=2)
