import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .paths import projects_dir, templates_dir


def init(project_name: str, brief: Optional[str] = None) -> None:
    """Scaffold a new project directory.

    Creates `projects/<name>/` with:
      - `app_spec.md` — copied from the template (or replaced with `brief` if provided)
      - a git repo with an initial commit

    Does NOT run any Claude agent. Run `harness-runner plan <name>` next.
    """
    project_dir = projects_dir() / project_name
    if project_dir.exists():
        raise RuntimeError(
            f"project already exists: {project_dir}\n"
            "remove it first if you want to start fresh."
        )
    project_dir.mkdir(parents=True)

    spec_target = project_dir / "app_spec.md"
    if brief is not None and brief.strip():
        spec_target.write_text(brief.strip() + "\n", encoding="utf-8")
    else:
        template = templates_dir() / "app_spec.template.md"
        shutil.copyfile(template, spec_target)

    _run_git(["init", "-q"], cwd=project_dir)
    _run_git(["add", "-A"], cwd=project_dir)
    _run_git(
        ["commit", "-q", "-m", "harness: project scaffold (app_spec.md)"],
        cwd=project_dir,
    )

    print(f"[init] created {project_dir}", file=sys.stderr)
    print(f"[init] edit {spec_target} then run:", file=sys.stderr)
    print(f"         harness-runner run {project_name}", file=sys.stderr)


def _run_git(args: list[str], *, cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "harness-runner",
        "GIT_AUTHOR_EMAIL": "harness@local",
        "GIT_COMMITTER_NAME": "harness-runner",
        "GIT_COMMITTER_EMAIL": "harness@local",
    }
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True)
