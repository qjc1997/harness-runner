"""Tests for the test-change audit (deterministic checks + mocked LLM review).

Run from the repo root:
    PYTHONPATH=src python3 -m unittest tests.test_test_audit -v
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

from harness_runner import test_audit  # noqa: E402
from harness_runner.stuck import NEEDS_HUMAN_FILE, head_sha  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

GO_TEST = '''package app
func TestStart(t *testing.T) {
	if got := rows(); got != 2 {
		t.Fatalf("rows = %d", got)
	}
	if err := start(); err != nil {
		t.Fatal(err)
	}
}
'''
JS_CHECK = '''check(rows === 'Word list|Turn timer', 'summary rows');
check(code === 409, 'start refused');
'''


class Repo:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.git("init", "-q")
        self.write("app_test.go", GO_TEST)
        self.write("scripts/check-manage.mjs", JS_CHECK)
        self.write("feature_list.json", json.dumps([{"id": "f297", "description": "Lobby summary shows Game mode, Spymasters, Operatives rows", "passes": True}]))
        self.commit("init")
        self.base = head_sha(self.dir)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True, text=True, env={**os.environ, **_ENV}).stdout

    def write(self, rel, text):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def close(self):
        self._tmp.cleanup()


class DeterministicTests(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.close)

    def problems(self):
        return [i.problem for i in test_audit.deterministic_audit(self.r.dir, self.r.base).issues]

    def test_cited_value_update_passes(self):
        self.r.write("scripts/check-manage.mjs", JS_CHECK.replace("'Word list|Turn timer'", "'Word list|Turn timer|Game mode|Spymasters|Operatives'"))
        self.r.commit("test-update: f297 scripts/check-manage.mjs — summary now has 5 rows")
        self.assertEqual(self.problems(), [])

    def test_new_test_files_are_free(self):
        self.r.write("scripts/check-new.mjs", "check(true, 'x');\n")
        self.r.commit("feat: f300 new check")
        self.assertEqual(self.problems(), [])

    def test_file_created_and_refined_in_same_shift(self):
        self.r.write("scripts/check-new.mjs", "check(a, 'x');\n")
        self.r.commit("feat: f300 new check")
        self.r.write("scripts/check-new.mjs", "check(a, 'x');\ncheck(b, 'y');\n")
        self.r.commit("feat: f300 extend new check")
        self.assertEqual(self.problems(), [])

    def test_owner_decision_counts_as_citation(self):
        self.r.write("app_test.go", GO_TEST.replace("!= 2", "!= 5"))
        self.r.commit("test-update: app_test.go rows 2 -> 5 per Owner decision 2026-10-15")
        self.assertEqual(self.problems(), [])

    def test_edit_outside_test_update_commit(self):
        self.r.write("app_test.go", GO_TEST.replace("!= 2", "!= 5"))
        self.r.commit("feat: f297 lobby rows")
        self.assertIn("existing test assertions edited outside a 'test-update:' commit", self.problems())

    def test_tests_written_for_current_plan_are_new_work(self):
        self.r.write("feature_list.json", json.dumps([{"id": "f346", "passes": False}]))
        self.r.commit("harness: refiner output")
        self.r.write("scripts/check-hygiene.mjs", "check(a === 1, 'x');\n")
        self.r.commit("feat: f346 new hygiene check")
        self.r.base = head_sha(self.r.dir)  # next shift starts here
        self.r.write("scripts/check-hygiene.mjs", "check(a === 1 && b === 2, 'x stricter');\n")
        self.r.commit("feat: f347 tighten hygiene check")
        self.assertEqual(self.problems(), [])
        # weakening is still caught for new-work tests
        self.r.write("scripts/check-hygiene.mjs", "check(a === 1 || true, 'x');\n")
        self.r.commit("feat: f347 relax")
        self.assertIn("'|| true' makes a check always pass", self.problems())

    def test_added_checks_are_welcome(self):
        self.r.write("scripts/check-manage.mjs", JS_CHECK + "check(note === 'x', 'new note check');\n")
        self.r.commit("feat: f331 extend check with the localhost note")
        res = test_audit.deterministic_audit(self.r.dir, self.r.base)
        self.assertEqual((res.issues, res.review_commits), ([], set()))

    def test_added_early_exit_goes_to_review(self):
        self.r.write("scripts/check-manage.mjs", "process.exit(0);\n" + JS_CHECK)
        self.r.commit("feat: f331 tweak")
        self.assertEqual(len(test_audit.deterministic_audit(self.r.dir, self.r.base).review_commits), 1)

    def test_setup_only_edit_goes_to_review_not_block(self):
        self.r.write("scripts/check-manage.mjs", "const shots = 'docs/screens';\n" + JS_CHECK)
        self.r.commit("feat: f324 screenshots")
        self.r.base = head_sha(self.r.dir)
        self.r.write("scripts/check-manage.mjs", "const shots = process.env.SCREENS_DIR || 'docs/screens';\n" + JS_CHECK)
        self.r.commit("feat: f324 screenshots honour SCREENS_DIR")
        res = test_audit.deterministic_audit(self.r.dir, self.r.base)
        self.assertEqual(res.issues, [])
        self.assertEqual(len(res.review_commits), 1)

    def test_missing_citation(self):
        self.r.write("app_test.go", GO_TEST.replace("!= 2", "!= 5"))
        self.r.commit("test-update: fix rows")
        self.assertTrue(any("does not cite" in p for p in self.problems()))

    def test_dropped_assertion(self):
        self.r.write("app_test.go", GO_TEST.replace('	if err := start(); err != nil {\n		t.Fatal(err)\n	}\n', ""))
        self.r.commit("test-update: f297 simplify")
        self.assertTrue(any("assertions dropped" in p for p in self.problems()))

    def test_or_true_and_skip(self):
        self.r.write("scripts/check-manage.mjs", JS_CHECK.replace("code === 409", "code === 409 || true"))
        self.r.write("app_test.go", GO_TEST.replace("func TestStart(t *testing.T) {", "func TestStart(t *testing.T) {\n\tt.Skip(\"flaky\")"))
        self.r.commit("test-update: f297 rows")
        probs = self.problems()
        self.assertIn("'|| true' makes a check always pass", probs)
        self.assertIn("test skipped", probs)

    def test_deleted_test_file(self):
        (self.r.dir / "app_test.go").unlink()
        self.r.commit("test-update: f297 remove obsolete test")
        self.assertIn("an existing test file was deleted", self.problems())


class LlmAndEscalationTests(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.addCleanup(self.r.close)
        self.r.write("scripts/check-manage.mjs", JS_CHECK.replace("'Word list|Turn timer'", "'Word list|Turn timer|Game mode|Spymasters|Operatives'"))
        self.r.commit("test-update: f297 scripts/check-manage.mjs — summary now has 5 rows")
        self.prompts = []

    def backend(self, verdict):
        def run(**kw):
            self.prompts.append(kw["prompt"])
            text = "ok\nTEST_AUDIT_JSON\n```json\n" + json.dumps(verdict) + "\n```"
            return SimpleNamespace(result=text, cost_usd=0.01)
        return SimpleNamespace(run=run)

    def test_justified_change_is_quiet(self):
        with mock.patch("harness_runner.backends.get_backend", return_value=self.backend({"justified": True, "coverage_preserved": True, "files": [], "reason": "ok"})):
            res = test_audit.audit_shift(self.r.dir, self.r.base)
        self.assertEqual(res.issues, [])
        self.assertEqual(res.reviewed_commits, 1)
        self.assertIn("Lobby summary shows Game mode", self.prompts[0], "cited feature must be given to the auditor")
        self.assertFalse((self.r.dir / NEEDS_HUMAN_FILE).exists())

    def test_unjustified_change_escalates(self):
        verdict = {"justified": False, "coverage_preserved": True, "files": ["scripts/check-manage.mjs"], "reason": "f297 does not mention this"}
        with mock.patch("harness_runner.backends.get_backend", return_value=self.backend(verdict)):
            res = test_audit.audit_shift(self.r.dir, self.r.base)
        self.assertEqual(len(res.issues), 1)
        question = (self.r.dir / NEEDS_HUMAN_FILE).read_text()
        self.assertIn("f297 does not mention this", question)
        self.assertIn("git revert --no-edit", question)

    def test_llm_can_be_disabled(self):
        with mock.patch.dict(os.environ, {"HARNESS_TEST_AUDIT_LLM": "0"}), \
                mock.patch("harness_runner.backends.get_backend", side_effect=AssertionError("must not be called")):
            res = test_audit.audit_shift(self.r.dir, self.r.base)
        self.assertEqual(res.issues, [])


class PathTests(unittest.TestCase):
    def test_is_test_path(self):
        for p in ["app_test.go", "tests/test_x.py", "scripts/check-visual.mjs", "ui/foo.test.tsx", "spec/a.js", "test_stuck.py"]:
            self.assertTrue(test_audit.is_test_path(p), p)
        for p in ["server.go", "scripts/build.sh", "frontend/ui/game.tsx", "docs/testing.md"]:
            self.assertFalse(test_audit.is_test_path(p), p)


if __name__ == "__main__":
    unittest.main()
