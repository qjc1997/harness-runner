import os
import subprocess
from pathlib import Path


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "harness-runner",
        "GIT_AUTHOR_EMAIL": "harness@local",
        "GIT_COMMITTER_NAME": "harness-runner",
        "GIT_COMMITTER_EMAIL": "harness@local",
    }


def git_run(args: list[str], *, cwd: Path) -> None:
    """Run a git command, raise CalledProcessError on non-zero exit."""
    subprocess.run(["git", *args], cwd=cwd, env=_git_env(), check=True)


def git_init(project_dir: Path) -> None:
    """Initialize a fresh git repo in `project_dir` (silent)."""
    git_run(["init", "-q"], cwd=project_dir)


def git_has_staged_changes(project_dir: Path) -> bool:
    """True if `git diff --cached` has any staged changes."""
    # `git diff --cached --quiet` exits 0 if no staged diff, 1 if there is one.
    rc = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=project_dir,
        env=_git_env(),
    ).returncode
    return rc != 0


def git_commit_all(project_dir: Path, message: str) -> bool:
    """`git add -A` then commit if there's anything staged.

    Returns True if a commit was made, False if there were no changes.
    Raises on any underlying git error (does NOT swallow them — bare
    `except CalledProcessError: pass` hides "git not installed",
    "repo corrupted", "hook failed", disk-full, etc.).
    """
    git_run(["add", "-A"], cwd=project_dir)
    if not git_has_staged_changes(project_dir):
        return False
    git_run(["commit", "-q", "-m", message], cwd=project_dir)
    return True


def git_commit_empty(project_dir: Path, message: str) -> None:
    """Make an empty commit (used for project scaffolding marker)."""
    git_run(["commit", "-q", "--allow-empty", "-m", message], cwd=project_dir)
