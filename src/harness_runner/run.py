import sys

from .budget import BudgetExceededError, assert_under_budget
from .notify import notify
from .paths import count_features, projects_dir
from .retry import generate_with_retry
from .roles.generator import generate
from .roles.planner import plan
from .stuck import StuckDetector, check_after_shift, check_before_loop, write_status


def run(project_name: str, loop: int = 1) -> int:
    """High-level entry: auto-detect first-run vs continuation.

    - If `projects/<name>/feature_list.json` is missing → run Planner.
    - Then run up to `loop` Generator shifts via `generator_loop`.

    Returns the process exit code (0, or `stuck.EXIT_NEEDS_HUMAN`).
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
        plan(project_name)  # planner is one-shot, no retry

    return generator_loop(project_name, loop)


def generator_loop(project_name: str, n: int) -> int:
    """Run up to `n` Generator shifts (each with retry-on-transient-failure).

    Stops early when:
      - HARNESS_BUDGET_USD (if set) is reached           → notify, exit 0
      - every feature passes                             → notify, exit 0
      - a shift wrote NEEDS_HUMAN.md, or the loop is stuck
        (see stuck.py)                                   → notify, exit 3
    Returns the process exit code.
    """
    project_dir = projects_dir() / project_name
    code = check_before_loop(project_dir, project_name)
    if code is not None:
        return code
    write_status(project_dir, "running", shifts=n)
    detector = StuckDetector(project_dir)
    successes = 0
    for i in range(1, n + 1):
        try:
            assert_under_budget(project_dir)
        except BudgetExceededError as e:
            print(f"\n[loop] budget exceeded — stopping at shift {i}/{n}: {e}", file=sys.stderr)
            write_status(project_dir, "budget_exceeded", detail=str(e))
            notify(f"💸 {project_name} hit its budget", str(e))
            break
        passing, total = count_features(project_dir)
        if total > 0 and passing >= total:
            print(f"\n[loop] all {total} features passing — exiting early at shift {i}/{n}", file=sys.stderr)
            break
        print(f"\n========== Shift {i}/{n} ==========\n", file=sys.stderr)
        detector.before_shift()
        ok = generate_with_retry(project_name, generate)
        if ok:
            successes += 1
        code = check_after_shift(detector, project_name, ok)
        if code is not None:
            print(f"\n[loop] {successes}/{i} shifts succeeded before stopping", file=sys.stderr)
            return code
    print(f"\n[loop] {successes}/{n} shifts succeeded", file=sys.stderr)
    passing, total = count_features(project_dir)
    if total > 0 and passing >= total:
        write_status(project_dir, "done", passing=passing, total=total)
        notify(f"✅ {project_name}: all features passing", f"{passing}/{total} features passing.")
    else:
        write_status(project_dir, "idle", passing=passing, total=total)
    return 0
