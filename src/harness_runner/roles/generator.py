import sys

from ..claude import run_claude
from ..paths import format_event, projects_dir, prompts_dir


def generate(project_name: str) -> None:
    project_dir = projects_dir() / project_name
    if not project_dir.exists():
        raise RuntimeError(f'project not found: {project_dir}\nrun "plan" first.')
    if not (project_dir / "feature_list.json").exists():
        raise RuntimeError(
            f"feature_list.json missing in {project_dir}\n"
            "the planner shift did not produce it."
        )

    system_prompt = (prompts_dir() / "generator.md").read_text(encoding="utf-8")
    user_prompt = (
        "Begin your shift. Follow the startup protocol, "
        "then implement exactly one feature."
    )

    print(f"[generator] project={project_name} cwd={project_dir}", file=sys.stderr)

    result = run_claude(
        cwd=str(project_dir),
        prompt=user_prompt,
        system_prompt_append=system_prompt,
        on_event=lambda e: print(format_event(e), file=sys.stderr),
    )

    print("\n[generator] done.", file=sys.stderr)
    print(result.result)
