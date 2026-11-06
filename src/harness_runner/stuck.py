"""Stuck detection and human escalation for unattended Generator loops.

Two ways a loop decides it needs a human:
  1. Explicit: a shift wrote `NEEDS_HUMAN.md` in the project (the generator
     prompt tells it to when it is blocked on an owner decision).
  2. Backstop: `HARNESS_STUCK_SHIFTS` (default 2) consecutive shifts made no
     progress (no new passing feature and no commit touching anything besides
     the progress log / history / feature list), or that many consecutive
     shifts failed (timeouts, crashes).

On either, the loop writes `.harness/status.json`, notifies (see notify.py)
and exits with EXIT_NEEDS_HUMAN so an outer driver can tell it apart from
success (0) and errors (1). `harness-runner resolve` records the decision.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .notify import notify
from .paths import count_features

EXIT_NEEDS_HUMAN = 3
NEEDS_HUMAN_FILE = "NEEDS_HUMAN.md"
STATE_DIR = ".harness"
AUTHORIZED_TEST_EDITS = "authorized_test_edits.txt"

# Files a shift touches even when it does no real work.
_BOOKKEEPING = {"claude-progress.txt", ".harness_history.jsonl", "feature_list.json", NEEDS_HUMAN_FILE}


def _is_bookkeeping(path: str) -> bool:
    return Path(path).name in _BOOKKEEPING or Path(path).parts[:1] == (STATE_DIR,)


def stuck_threshold() -> int:
    try:
        return max(1, int(os.environ.get("HARNESS_STUCK_SHIFTS", "2")))
    except ValueError:
        return 2


def needs_human(project_dir: Path) -> Optional[str]:
    """Contents of NEEDS_HUMAN.md if a shift asked for a human, else None."""
    f = project_dir / NEEDS_HUMAN_FILE
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8").strip() or "(NEEDS_HUMAN.md is empty)"


def _git(project_dir: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=project_dir, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def head_sha(project_dir: Path) -> str:
    return _git(project_dir, "rev-parse", "HEAD").strip()


def code_commits_since(project_dir: Path, sha: str) -> int:
    """Commits after `sha` that touch at least one non-bookkeeping file."""
    if not sha:
        return 0
    out = _git(project_dir, "log", f"{sha}..HEAD", "--name-only", "--format=@@%H")
    n = 0
    for block in out.split("@@")[1:]:
        files = [ln.strip() for ln in block.splitlines()[1:] if ln.strip()]
        if any(not _is_bookkeeping(f) for f in files):
            n += 1
    return n


class StuckDetector:
    """Call before_shift() / after_shift(ok) around each shift."""

    def __init__(self, project_dir: Path, threshold: Optional[int] = None):
        self.project_dir = project_dir
        self.threshold = threshold or stuck_threshold()
        self.idle = 0
        self.failed = 0
        self._passing = 0
        self._sha = ""

    def before_shift(self) -> None:
        self._passing, _ = count_features(self.project_dir)
        self._sha = head_sha(self.project_dir)

    def after_shift(self, ok: bool) -> Optional[str]:
        """Return a stop reason, or None to keep going."""
        passing, _ = count_features(self.project_dir)
        progressed = passing > self._passing or code_commits_since(self.project_dir, self._sha) > 0
        self.idle = 0 if progressed else self.idle + 1
        self.failed = 0 if ok else self.failed + 1
        if self.failed >= self.threshold:
            return f"{self.failed} consecutive shifts failed or timed out (feature probably too big, or the environment is broken)"
        if self.idle >= self.threshold:
            return f"{self.idle} consecutive shifts made no progress (no new passing feature, no code commit)"
        return None


def recent_human_notes(project_dir: Path, limit: int = 6) -> str:
    """Last lines of the progress log that flag owner action or blockers."""
    log = project_dir / "claude-progress.txt"
    if not log.exists():
        return ""
    keys = ("needs human", "owner action", "blocked", "watch out")
    lines = [ln.strip() for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()
             if any(k in ln.lower() for k in keys)]
    return "\n".join(ln[:300] for ln in lines[-limit:])


def write_status(project_dir: Path, state: str, **fields) -> None:
    d = project_dir / STATE_DIR
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("status.json\n", encoding="utf-8")
    data = {"state": state, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
    (d / "status.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def escalate(project_dir: Path, project_name: str, reason: str, detail: str = "") -> None:
    """Record + announce that the loop needs a human. Caller then exits EXIT_NEEDS_HUMAN."""
    passing, total = count_features(project_dir)
    body = f"{reason}\nProgress: {passing}/{total} features passing."
    if detail:
        body += f"\n\n{detail.strip()[:1500]}"
    body += f'\n\nResolve with: harness-runner resolve {project_name} "<decision>"'
    write_status(project_dir, "needs_human", reason=reason, detail=detail, passing=passing, total=total)
    print(f"\n[loop] NEEDS HUMAN — {reason}\n{detail}\n", file=sys.stderr)
    sent = notify(f"🛑 {project_name} needs you", body)
    print(f"[loop] notified via: {', '.join(sent) if sent else 'nothing configured'}", file=sys.stderr)


def check_before_loop(project_dir: Path, project_name: str) -> Optional[int]:
    """Refuse to start while an unresolved NEEDS_HUMAN.md exists."""
    question = needs_human(project_dir)
    if question is None:
        return None
    escalate(project_dir, project_name, "an earlier shift is waiting for an owner decision (NEEDS_HUMAN.md)", question)
    return EXIT_NEEDS_HUMAN


def check_after_shift(detector: StuckDetector, project_name: str, ok: bool) -> Optional[int]:
    """After a shift: explicit request first, then the progress backstop."""
    reason = detector.after_shift(ok)
    question = needs_human(detector.project_dir)
    if question is not None:
        escalate(detector.project_dir, project_name, "a shift is blocked and asked for an owner decision", question)
        return EXIT_NEEDS_HUMAN
    if reason:
        escalate(detector.project_dir, project_name, reason, recent_human_notes(detector.project_dir))
        return EXIT_NEEDS_HUMAN
    return None


def resolve(project_dir: Path, decision: str, allow_test_edits: list) -> None:
    """Record an owner decision, authorise test edits, clear NEEDS_HUMAN.md, commit."""
    from .git_util import git_commit_all

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n### Owner decision {stamp}", f"- {decision.strip()}"]
    if allow_test_edits:
        d = project_dir / STATE_DIR
        d.mkdir(exist_ok=True)
        with (d / AUTHORIZED_TEST_EDITS).open("a", encoding="utf-8") as f:
            for p in allow_test_edits:
                f.write(p.strip() + "\n")
        lines.append("- Authorised test edits (make exactly the change described above): " + ", ".join(allow_test_edits))
    from .roles.griller import attach_answers  # lazy: griller imports this module

    if attach_answers(project_dir, decision):
        lines.append("- Recorded as the answers to the open grill round; run `refine --continue` next.")
    question = needs_human(project_dir)
    if question is not None:
        archive = project_dir / STATE_DIR / "resolved"
        archive.mkdir(parents=True, exist_ok=True)
        (archive / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-NEEDS_HUMAN.md").write_text(question + "\n", encoding="utf-8")
        (project_dir / NEEDS_HUMAN_FILE).unlink()
        lines.append("- Resolves the NEEDS_HUMAN.md question (archived in .harness/resolved/).")
    with (project_dir / "claude-progress.txt").open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    write_status(project_dir, "resolved", decision=decision.strip())
    git_commit_all(project_dir, "harness: owner decision")
