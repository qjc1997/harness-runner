import sys

from .paths import projects_dir
from .roles.generator import generate
from .roles.planner import plan


def run(project_name: str, loop: int = 1) -> None:
    """High-level entry: auto-detect first-run vs continuation.

    - If `projects/<name>/feature_list.json` is missing → run Planner.
    - Then run Generator `loop` times.

    Errors out if the project directory or `app_spec.md` is missing
    (run `harness-runner init <name>` first).
    """
    project_dir = projects_dir() / project_name
    if not project_dir.exists():
        raise RuntimeError(
            f"project not found: {project_dir}\n"
            f'run "harness-runner init {project_name}" first.'
        )
    if not (project_dir / "app_spec.md").exists():
        raise RuntimeError(
            f"app_spec.md missing in {project_dir}\n"
            f'run "harness-runner init {project_name}" to scaffold it.'
        )

    if not (project_dir / "feature_list.json").exists():
        print("\n========== Planner shift ==========\n", file=sys.stderr)
        plan(project_name)

    for i in range(1, loop + 1):
        print(f"\n========== Generator shift {i}/{loop} ==========\n", file=sys.stderr)
        generate(project_name)
