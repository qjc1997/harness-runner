"""Tests for stuck detection, human escalation and notifications.

Stdlib unittest (no pytest dependency — matches harness-runner's zero-dep policy).
Run from the repo root:
    PYTHONPATH=src python3 -m unittest tests.test_stuck -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from harness_runner import notify as notify_mod  # noqa: E402
from harness_runner import run as run_mod  # noqa: E402
from harness_runner import stuck  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(d: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=d, check=True, capture_output=True, env={**os.environ, **_ENV})


class Project:
    """A throwaway git-backed project with a feature_list.json."""

    def __init__(self, n_features: int = 3):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "proj"
        self.dir.mkdir()
        _git(self.dir, "init", "-q")
        self.features = [{"id": f"f{i:03d}", "passes": False} for i in range(1, n_features + 1)]
        self._write_features()
        (self.dir / "claude-progress.txt").write_text("log\n")
        (self.dir / "app_spec.md").write_text("spec\n")
        self.commit("init")

    def _write_features(self) -> None:
        (self.dir / "feature_list.json").write_text(json.dumps(self.features))

    def commit(self, msg: str) -> None:
        _git(self.dir, "add", "-A")
        _git(self.dir, "commit", "-q", "--allow-empty", "-m", msg)

    def pass_next(self) -> None:
        next(f for f in self.features if not f["passes"])["passes"] = True
        self._write_features()
        self.commit("feat")

    def code_commit(self, name: str = "app.go") -> None:
        p = self.dir / name
        p.write_text(p.read_text() + "x" if p.exists() else "x")
        self.commit("wip")

    def bookkeeping_commit(self) -> None:
        with (self.dir / "claude-progress.txt").open("a") as f:
            f.write("blocked, nothing done\n")
        self.commit("docs: shift log")

    def close(self) -> None:
        self._tmp.cleanup()


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)

    def shift(self, det, action, ok=True):
        det.before_shift()
        action()
        return det.after_shift(ok)

    def test_progress_resets_counter(self):
        det = stuck.StuckDetector(self.p.dir, threshold=2)
        self.assertIsNone(self.shift(det, self.p.bookkeeping_commit))
        self.assertIsNone(self.shift(det, self.p.pass_next))
        self.assertIsNone(self.shift(det, self.p.bookkeeping_commit))

    def test_two_idle_shifts_stop(self):
        det = stuck.StuckDetector(self.p.dir, threshold=2)
        self.assertIsNone(self.shift(det, self.p.bookkeeping_commit))
        reason = self.shift(det, self.p.bookkeeping_commit)
        self.assertIn("no progress", reason)

    def test_code_commit_counts_as_progress(self):
        det = stuck.StuckDetector(self.p.dir, threshold=2)
        self.assertIsNone(self.shift(det, self.p.code_commit))
        self.assertIsNone(self.shift(det, self.p.code_commit))

    def test_consecutive_failures_stop(self):
        det = stuck.StuckDetector(self.p.dir, threshold=2)
        self.assertIsNone(self.shift(det, self.p.code_commit, ok=False))
        self.assertIn("failed", self.shift(det, self.p.code_commit, ok=False))

    def test_threshold_from_env(self):
        with mock.patch.dict(os.environ, {"HARNESS_STUCK_SHIFTS": "4"}):
            self.assertEqual(stuck.stuck_threshold(), 4)
        with mock.patch.dict(os.environ, {"HARNESS_STUCK_SHIFTS": "nope"}):
            self.assertEqual(stuck.stuck_threshold(), 2)


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.p = Project()
        self.addCleanup(self.p.close)
        self.sent = []
        patcher = mock.patch.object(stuck, "notify", side_effect=lambda t, b: self.sent.append((t, b)) or ["test"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_needs_human_file_stops_and_notifies(self):
        det = stuck.StuckDetector(self.p.dir, threshold=2)
        det.before_shift()
        (self.p.dir / stuck.NEEDS_HUMAN_FILE).write_text("Question: edit test X?\nOptions: A / B")
        code = stuck.check_after_shift(det, "proj", ok=True)
        self.assertEqual(code, stuck.EXIT_NEEDS_HUMAN)
        self.assertIn("Question: edit test X?", self.sent[0][1])
        status = json.loads((self.p.dir / ".harness" / "status.json").read_text())
        self.assertEqual(status["state"], "needs_human")
        self.assertEqual((self.p.dir / ".harness" / ".gitignore").read_text(), "status.json\n")

    def test_refuses_to_start_while_unresolved(self):
        (self.p.dir / stuck.NEEDS_HUMAN_FILE).write_text("Q?")
        self.assertEqual(stuck.check_before_loop(self.p.dir, "proj"), stuck.EXIT_NEEDS_HUMAN)

    def test_resolve_records_and_clears(self):
        (self.p.dir / stuck.NEEDS_HUMAN_FILE).write_text("Q: may I edit foo_test.go?")
        self.p.commit("blocked")
        stuck.resolve(self.p.dir, "Yes, change need to 3.", ["foo_test.go"])
        self.assertIsNone(stuck.needs_human(self.p.dir))
        log = (self.p.dir / "claude-progress.txt").read_text()
        self.assertIn("### Owner decision", log)
        self.assertIn("Yes, change need to 3.", log)
        self.assertIn("foo_test.go", log)
        self.assertEqual((self.p.dir / ".harness" / "authorized_test_edits.txt").read_text(), "foo_test.go\n")
        archived = list((self.p.dir / ".harness" / "resolved").glob("*-NEEDS_HUMAN.md"))
        self.assertEqual(len(archived), 1)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=self.p.dir, capture_output=True, text=True).stdout
        self.assertEqual(dirty.strip(), "", "resolve must commit everything except ignored status.json")
        self.assertIsNone(stuck.check_before_loop(self.p.dir, "proj"))


class LoopTests(unittest.TestCase):
    """generator_loop with simulated shifts (no claude subprocess)."""

    def setUp(self):
        self.p = Project(n_features=5)
        self.addCleanup(self.p.close)
        self.sent = []
        for target in (stuck, run_mod):
            patcher = mock.patch.object(target, "notify", side_effect=lambda t, b: self.sent.append(t) or ["test"])
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(run_mod, "projects_dir", return_value=self.p.dir.parent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def loop(self, shift_fn, n=8):
        calls = []

        def fake_retry(project_name, _generate):
            calls.append(project_name)
            return shift_fn(len(calls))

        with mock.patch.object(run_mod, "generate_with_retry", side_effect=fake_retry), \
                mock.patch.dict(os.environ, {"HARNESS_STUCK_SHIFTS": "2"}):
            code = run_mod.generator_loop("proj", n)
        return code, len(calls)

    def test_blocked_shift_stops_after_one(self):
        def shift(_i):
            (self.p.dir / stuck.NEEDS_HUMAN_FILE).write_text("blocked on owner decision")
            self.p.bookkeeping_commit()
            return True
        code, shifts = self.loop(shift)
        self.assertEqual((code, shifts), (stuck.EXIT_NEEDS_HUMAN, 1))
        self.assertTrue(self.sent and "needs you" in self.sent[0])

    def test_idle_shifts_stop_after_threshold(self):
        def shift(_i):
            self.p.bookkeeping_commit()
            return True
        self.assertEqual(self.loop(shift), (stuck.EXIT_NEEDS_HUMAN, 2))

    def test_productive_loop_runs_to_completion(self):
        def shift(_i):
            self.p.pass_next()
            return True
        code, shifts = self.loop(shift)
        self.assertEqual((code, shifts), (0, 5))
        self.assertIn("all features passing", self.sent[-1])


class NotifyTests(unittest.TestCase):
    def test_discord_payload_and_failures_never_raise(self):
        seen = {}

        class Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout):
            seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
            seen["body"] = json.loads(req.data)
            return Resp()

        env = {"HARNESS_DISCORD_WEBHOOK": "https://discord.example/api/webhooks/1/x", "HARNESS_NOTIFY_MACOS": "0"}
        with mock.patch.dict(os.environ, env), mock.patch.object(notify_mod.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(notify_mod.notify("title", "b" * 5000), ["discord"])
        self.assertEqual(seen["url"], env["HARNESS_DISCORD_WEBHOOK"])
        self.assertNotIn("Python-urllib", seen["ua"])
        self.assertLessEqual(len(seen["body"]["content"]), notify_mod.DISCORD_LIMIT)

        def boom(*a, **k):
            raise OSError("network down")

        with mock.patch.dict(os.environ, env), mock.patch.object(notify_mod.urllib.request, "urlopen", boom):
            self.assertEqual(notify_mod.notify("t", "b"), [])

    def test_custom_command_gets_message(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "msg"
            env = {"HARNESS_NOTIFY_CMD": f'printf "%s|%s" "$HARNESS_NOTIFY_TITLE" "$HARNESS_NOTIFY_BODY" > {out}',
                   "HARNESS_NOTIFY_MACOS": "0", "HARNESS_DISCORD_WEBHOOK": ""}
            with mock.patch.dict(os.environ, env), mock.patch.object(notify_mod, "_WEBHOOK_FILE", Path(d) / "none"):
                self.assertEqual(notify_mod.notify("T", "B"), ["cmd"])
            self.assertEqual(out.read_text(), "T|B")


if __name__ == "__main__":
    unittest.main()
