import os
import subprocess
import sys
from pathlib import Path

from ..claude import run_claude
from ..paths import format_event, projects_dir, prompts_dir


def plan(project_name: str, brief: str) -> None:
    project_dir = projects_dir() / project_name

    if project_dir.exists():
        raise RuntimeError(
            f"project already exists: {project_dir}\n"
            "remove it first if you want to re-plan."
        )
    project_dir.mkdir(parents=True)

    _run_git(["init", "-q"], cwd=project_dir)
    _run_git(
        ["commit", "-q", "--allow-empty", "-m", "harness: project scaffold"],
        cwd=project_dir,
    )

    system_prompt = (prompts_dir() / "planner.md").read_text(encoding="utf-8")
    user_prompt = (
        f"# Product brief\n\n{brief}\n\n"
        "Produce feature_list.json, init.sh, and claude-progress.txt now."
    )

    print(f"[planner] project={project_name} cwd={project_dir}", file=sys.stderr)

    result = run_claude(
        cwd=str(project_dir),
        prompt=user_prompt,
        system_prompt_append=system_prompt,
        on_event=lambda e: print(format_event(e), file=sys.stderr),
    )

    try:
        _run_git(["add", "-A"], cwd=project_dir)
        _run_git(["commit", "-q", "-m", "harness: planner output"], cwd=project_dir)
    except subprocess.CalledProcessError:
        # No changes to commit — planner produced nothing. The caller will see this from the result.
        pass

    print("\n[planner] done.", file=sys.stderr)
    print(result.result)


def _run_git(args: list[str], *, cwd: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "harness-runner",
        "GIT_AUTHOR_EMAIL": "harness@local",
        "GIT_COMMITTER_NAME": "harness-runner",
        "GIT_COMMITTER_EMAIL": "harness@local",
    }
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True)
