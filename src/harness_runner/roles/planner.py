import os
import subprocess
import sys
from pathlib import Path

from ..backends import get_backend
from ..paths import count_features, format_progress, projects_dir, prompts_dir


def plan(project_name: str) -> None:
    project_dir = projects_dir() / project_name

    if not project_dir.exists():
        raise RuntimeError(
            f"project not found: {project_dir}\n"
            f'run "harness-runner init {project_name}" first.'
        )

    spec_file = project_dir / "app_spec.md"
    if not spec_file.exists():
        raise RuntimeError(
            f"app_spec.md missing in {project_dir}\n"
            f'run "harness-runner init {project_name}" to scaffold it.'
        )

    if (project_dir / "feature_list.json").exists():
        raise RuntimeError(
            f"feature_list.json already exists in {project_dir}\n"
            "this project has already been planned. delete the file to re-plan,\n"
            "or run `harness-runner generate` to continue building."
        )

    spec = spec_file.read_text(encoding="utf-8")
    system_prompt = (prompts_dir() / "planner.md").read_text(encoding="utf-8")
    user_prompt = (
        f"# Application Specification (read from {spec_file.name})\n\n{spec}\n\n"
        "Produce feature_list.json, init.sh, and claude-progress.txt now. "
        "Do not write application code — that is the next agent's job."
    )

    backend = get_backend()
    print(
        f"[planner] project={project_name} cwd={project_dir} backend={backend.name}",
        file=sys.stderr,
    )

    result = backend.run(
        cwd=str(project_dir),
        prompt=user_prompt,
        system_prompt_append=system_prompt,
        on_event=lambda e: print(backend.format_event(e), file=sys.stderr),
    )

    try:
        _run_git(["add", "-A"], cwd=project_dir)
        _run_git(["commit", "-q", "-m", "harness: planner output"], cwd=project_dir)
    except subprocess.CalledProcessError:
        # No changes to commit — planner produced nothing.
        pass

    print("\n[planner] done.", file=sys.stderr)
    passing, total = count_features(project_dir)
    print(format_progress(passing, total), file=sys.stderr)
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
