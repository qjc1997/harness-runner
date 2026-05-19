import sys

from .paths import projects_dir
from .retry import generate_with_retry
from .roles.generator import generate
from .roles.planner import plan


def run(project_name: str, loop: int = 1) -> None:
    """High-level entry: auto-detect first-run vs continuation.

    - If `projects/<name>/feature_list.json` is missing → run Planner.
    - Then run Generator `loop` times, each with retry-on-transient-failure.

    Errors out if the project directory or `app_spec.md` is missing
    (run `harness-runner init <name>` first).

    Planner failure is NOT retried — the spec is the input that needs
    human judgment to fix. Generator failures retry up to 3 times each;
    after exhausting retries on one shift, the loop continues to the
    next shift (a fresh agent may handle what got the previous one stuck).
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
        plan(project_name)  # planner is one-shot, no retry

    successes = 0
    for i in range(1, loop + 1):
        print(f"\n========== Generator shift {i}/{loop} ==========\n", file=sys.stderr)
        if generate_with_retry(project_name, generate):
            successes += 1
    print(f"\n[run] {successes}/{loop} generator shifts succeeded", file=sys.stderr)
