import sys
import time

from ..backends import ShiftTimeoutError, get_backend
from ..git_util import git_commit_all
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir


def evaluate(project_name: str) -> None:
    """Playwright-based UI evaluation shift.

    Verifies features from a real browser perspective — catches data-flow
    bugs, state inconsistencies, and interaction failures that Generator
    self-testing (curl/HTTP) misses.

    Evaluator only finds bugs (flips passes true→false). It never marks
    features as passing — that remains the Generator's responsibility.
    """
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

    passing_before, total = count_features(project_dir)

    system_prompt = (prompts_dir() / "evaluator.md").read_text(encoding="utf-8")
    user_prompt = (
        "Begin your evaluation shift. Follow the startup protocol, then verify "
        "smoke features and any high-priority data-flow features using Playwright. "
        "Report regressions found."
    )

    backend = get_backend()
    print(
        f"[evaluator] project={project_name} cwd={project_dir} backend={backend.name}",
        file=sys.stderr,
    )

    t0 = time.monotonic()
    try:
        result = backend.run(
            cwd=str(project_dir),
            prompt=user_prompt,
            system_prompt_append=system_prompt,
            on_event=lambda e: print(backend.format_event(e), file=sys.stderr),
        )
    except ShiftTimeoutError as e:
        record_shift(
            project_dir,
            role="evaluator",
            passing_before=passing_before,
            passing_after=passing_before,
            total=total,
            outcome="timeout",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise
    except Exception as e:
        record_shift(
            project_dir,
            role="evaluator",
            passing_before=passing_before,
            passing_after=passing_before,
            total=total,
            outcome="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise

    passing_after, total_after = count_features(project_dir)
    regressions = passing_before - passing_after  # evaluator can only reduce passing count

    print(
        f"\n[evaluator] done: {regressions} regression(s) found "
        f"({passing_after}/{total_after} passing).",
        file=sys.stderr,
    )
    print(format_progress(passing_after, total_after), file=sys.stderr)

    record_shift(
        project_dir,
        role="evaluator",
        passing_before=passing_before,
        passing_after=passing_after,
        total=total_after,
        outcome="ok",
        session_id=result.session_id,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=result.model,
        note=f"{regressions} regression(s) found" if regressions else "no regressions",
    )

    print(result.result)
