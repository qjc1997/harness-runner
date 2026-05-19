import shutil
import sys
from typing import Optional

from .git_util import git_commit_all, git_init
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

    git_init(project_dir)
    git_commit_all(project_dir, "harness: project scaffold (app_spec.md)")

    print(f"[init] created {project_dir}", file=sys.stderr)
    print(f"[init] edit {spec_target} then run:", file=sys.stderr)
    print(f"         harness-runner run {project_name}", file=sys.stderr)
