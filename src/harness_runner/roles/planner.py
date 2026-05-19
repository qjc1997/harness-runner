import sys
import time

from ..backends import ShiftTimeoutError, get_backend
from ..git_util import git_commit_all
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir
from ..validate import format_errors, validate_planner_output


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
            role="planner",
            passing_before=0,
            passing_after=0,
            total=0,
            outcome="timeout",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise
    except Exception as e:
        record_shift(
            project_dir,
            role="planner",
            passing_before=0,
            passing_after=0,
            total=0,
            outcome="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise

    # ── Validate planner output BEFORE committing ────────────────
    errors = validate_planner_output(project_dir)
    if errors:
        record_shift(
            project_dir,
            role="planner",
            passing_before=0,
            passing_after=0,
            total=0,
            outcome="validation_failed",
            session_id=result.session_id,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            model=result.model,
            note=f"{len(errors)} validation error(s)",
        )
        raise RuntimeError(
            "Planner produced invalid output. The plan has NOT been committed.\n"
            "Inspect the working tree to see what was generated, then either\n"
            "fix it by hand and `git add -A && git commit`, or remove the\n"
            "project directory and re-plan.\n\n"
            f"Errors:\n{format_errors(errors)}"
        )

    # Commit the validated plan.
    git_commit_all(project_dir, "harness: planner output")

    print("\n[planner] done.", file=sys.stderr)
    passing, total = count_features(project_dir)
    print(format_progress(passing, total), file=sys.stderr)

    record_shift(
        project_dir,
        role="planner",
        passing_before=0,
        passing_after=passing,
        total=total,
        outcome="ok",
        session_id=result.session_id,
        cost_usd=result.cost_usd,
        duration_ms=result.duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=result.model,
    )

    print(result.result)
