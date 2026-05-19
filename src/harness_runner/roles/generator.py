import sys
import time

from ..backends import ShiftTimeoutError, get_backend
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir


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

    # Snapshot progress before the shift to detect "shift ran but moved nothing".
    passing_before, total = count_features(project_dir)

    system_prompt = (prompts_dir() / "generator.md").read_text(encoding="utf-8")
    user_prompt = (
        "Begin your shift. Follow the startup protocol, then either fix any "
        "regression you find or implement exactly one new feature."
    )

    backend = get_backend()
    print(
        f"[generator] project={project_name} cwd={project_dir} backend={backend.name}",
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
            role="generator",
            passing_before=passing_before,
            passing_after=passing_before,  # no progress on timeout
            total=total,
            outcome="timeout",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise
    except Exception as e:
        passing_after, _ = count_features(project_dir)
        record_shift(
            project_dir,
            role="generator",
            passing_before=passing_before,
            passing_after=passing_after,
            total=total,
            outcome="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise

    passing_after, total_after = count_features(project_dir)

    print("\n[generator] done.", file=sys.stderr)
    print(format_progress(passing_after, total_after), file=sys.stderr)

    record_shift(
        project_dir,
        role="generator",
        passing_before=passing_before,
        passing_after=passing_after,
        total=total_after,
        outcome="ok",
        session_id=result.session_id,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
    )

    print(result.result)
