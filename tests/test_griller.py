"""Tests for the asynchronous grill step before refine (model mocked).

Run from the repo root:
    PYTHONPATH=src python3 -m unittest tests.test_griller -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_runner import stuck  # noqa: E402
from harness_runner.roles import griller  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
QUESTIONS = "GRILL_RESULT: QUESTIONS\n### Q1 — Who gives clues?\nSpymaster or operative?\n➡️ Recommended: spymaster, per RULES.md\n"
NONE = "GRILL_RESULT: NO_QUESTIONS\nAssuming spymasters give clues."


class GrillFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "proj"
        self.dir.mkdir()
        env = {**os.environ, **_ENV}
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True, env=env)
        (self.dir / "feature_list.json").write_text("[]")
        (self.dir / "claude-progress.txt").write_text("log\n")
        subprocess.run(["git", "add", "-A"], cwd=self.dir, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=self.dir, check=True, env=env)
        self.outputs = []
        self.prompts = []
        self.planned = []
        self.notified = []
        for target in (stuck,):
            p = mock.patch.object(target, "notify", side_effect=lambda t, b: self.notified.append(b) or ["test"])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(griller, "get_backend", return_value=SimpleNamespace(run=self._run))
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(os.environ, {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
        p.start()
        self.addCleanup(p.stop)

    def _run(self, **kw):
        self.prompts.append(kw["prompt"])
        return SimpleNamespace(result=self.outputs.pop(0), cost_usd=0.01)

    def refine(self, req=None, cont=False):
        return griller.grill_then_refine("proj", self.dir, req, cont, lambda name, full: self.planned.append(full))

    def test_full_async_round_trip(self):
        self.outputs = [QUESTIONS]
        self.assertEqual(self.refine("Allow several spymasters"), stuck.EXIT_NEEDS_HUMAN)
        self.assertIn("Who gives clues?", (self.dir / stuck.NEEDS_HUMAN_FILE).read_text())
        self.assertIn("Who gives clues?", self.notified[0])
        self.assertEqual(self.planned, [])
        # answering is required before continuing
        self.assertEqual(self.refine(cont=True), stuck.EXIT_NEEDS_HUMAN)
        # owner answers through resolve
        stuck.resolve(self.dir, "Q1: spymaster gives clues", [])
        self.assertFalse((self.dir / stuck.NEEDS_HUMAN_FILE).exists())
        self.assertEqual(griller.load_pending(self.dir)["rounds"][0]["answers"], "Q1: spymaster gives clues")
        # next round: nothing open -> plan with the Q&A attached
        self.outputs = [NONE]
        self.assertEqual(self.refine(cont=True), 0)
        self.assertIn("Q1: spymaster gives clues", self.prompts[-1], "griller sees earlier answers")
        self.assertEqual(len(self.planned), 1)
        self.assertIn("Allow several spymasters", self.planned[0])
        self.assertIn("Owner decisions from grilling", self.planned[0])
        self.assertIn("Q1: spymaster gives clues", self.planned[0])
        self.assertIsNone(griller.load_pending(self.dir))
        self.assertEqual(len(list((self.dir / ".harness" / "grill").glob("*-done.json"))), 1)

    def test_no_questions_plans_immediately(self):
        self.outputs = [NONE]
        self.assertEqual(self.refine("Fix a typo"), 0)
        self.assertEqual(self.planned, ["Fix a typo"])
        self.assertFalse((self.dir / stuck.NEEDS_HUMAN_FILE).exists())

    def test_new_requirements_refused_while_pending(self):
        self.outputs = [QUESTIONS]
        self.refine("A")
        self.assertEqual(self.refine("B"), 1)

    def test_invalid_output_does_not_plan(self):
        self.outputs = ["I think it is fine."]
        self.assertEqual(self.refine("A"), 1)
        self.assertEqual(self.planned, [])
        self.assertIsNone(griller.load_pending(self.dir))

    def test_parse_result(self):
        self.assertEqual(griller.parse_result(QUESTIONS)[0], "questions")
        self.assertEqual(griller.parse_result(NONE)[0], "none")
        self.assertEqual(griller.parse_result("GRILL_RESULT: QUESTIONS\nno numbered questions")[0], "invalid")


if __name__ == "__main__":
    unittest.main()
