import sys

from ..claude import run_claude
from ..paths import count_features, format_event, format_progress, projects_dir, prompts_dir


def generate(project_name: str) -> None:
    project_dir = projects_dir() / project_name
    if not project_dir.exists():
        raise RuntimeError(
            f"project not found: {project_dir}\n"
            f'run "harness-runner init {project_name}" first.'
        )
    if not (project_dir / "feature_list.json").exists():
        raise RuntimeError(
            f"feature_list.json missing in {project_dir}\n"
            f'run "harness-runner plan {project_name}" first.'
        )

    system_prompt = (prompts_dir() / "generator.md").read_text(encoding="utf-8")
    user_prompt = (
        "Begin your shift. Follow the startup protocol, then either fix any "
        "regression you find or implement exactly one new feature."
    )

    print(f"[generator] project={project_name} cwd={project_dir}", file=sys.stderr)

    result = run_claude(
        cwd=str(project_dir),
        prompt=user_prompt,
        system_prompt_append=system_prompt,
        on_event=lambda e: print(format_event(e), file=sys.stderr),
    )

    print("\n[generator] done.", file=sys.stderr)
    passing, total = count_features(project_dir)
    print(format_progress(passing, total), file=sys.stderr)
    print(result.result)
