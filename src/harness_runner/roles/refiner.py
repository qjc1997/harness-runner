import json
import sys
import time

from ..backends import ShiftTimeoutError, get_backend
from ..git_util import git_commit_all
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir
from ..validate import format_errors, validate_refine_output


def refine(project_name: str, requirements: str) -> None:
    """Append-only incremental planning: add new features for new requirements.

    Reads the existing feature_list.json as a snapshot, runs the Refinement
    Planner LLM, then validates that existing features are untouched before
    committing the new entries.
    """
    project_dir = projects_dir() / project_name

    if not project_dir.exists():
        raise RuntimeError(
            f"project not found: {project_dir}\n"
            f'run "harness-runner init {project_name}" first.'
        )

    feature_list_path = project_dir / "feature_list.json"
    if not feature_list_path.exists():
        raise RuntimeError(
            f"feature_list.json not found in {project_dir}\n"
            "run 'harness-runner plan' first to produce the initial feature list."
        )

    # Snapshot original features before the LLM touches anything.
    try:
        original_features: list[dict] = json.loads(
            feature_list_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as e:
        raise RuntimeError(f"could not read feature_list.json: {e}") from e

    if not isinstance(original_features, list):
        raise RuntimeError("feature_list.json is not a JSON array — cannot refine")

    original_count = len(original_features)
    passing_before, total_before = count_features(project_dir)

    # Build context for the LLM: existing plan + new requirements.
    existing_plan_json = json.dumps(original_features, indent=2, ensure_ascii=False)
    system_prompt = (prompts_dir() / "refiner.md").read_text(encoding="utf-8")
    user_prompt = (
        f"# New requirements\n\n{requirements}\n\n"
        f"# Existing feature_list.json ({original_count} entries)\n\n"
        f"```json\n{existing_plan_json}\n```\n\n"
        "Append new features to address the requirements above. "
        "Do not modify any existing entry. "
        "Read app_spec.md, claude-progress.txt, and ARCHITECTURE.md (if present) "
        "before writing."
    )

    backend = get_backend()
    print(
        f"[refiner] project={project_name} cwd={project_dir} backend={backend.name}",
        file=sys.stderr,
    )
    print(
        f"[refiner] existing features: {original_count} "
        f"({passing_before} passing)",
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
            role="refiner",
            passing_before=passing_before,
            passing_after=passing_before,
            total=total_before,
            outcome="timeout",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise
    except Exception as e:
        record_shift(
            project_dir,
            role="refiner",
            passing_before=passing_before,
            passing_after=passing_before,
            total=total_before,
            outcome="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        raise

    # Validate: existing features untouched, new entries well-formed.
    errors = validate_refine_output(project_dir, original_features)
    if errors:
        _, total_after = count_features(project_dir)
        record_shift(
            project_dir,
            role="refiner",
            passing_before=passing_before,
            passing_after=passing_before,
            total=total_after,
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
            "Refinement Planner produced invalid output. The plan has NOT been committed.\n"
            "Inspect feature_list.json to see what was generated, then either\n"
            "fix it by hand and `git add -A && git commit`, or restore the\n"
            "original with `git checkout -- feature_list.json`.\n\n"
            f"Errors:\n{format_errors(errors)}"
        )

    git_commit_all(project_dir, "harness: refiner output")

    passing_after, total_after = count_features(project_dir)
    new_count = total_after - total_before

    print(
        f"\n[refiner] done: {new_count} new features added "
        f"(total {total_after}, {passing_after} passing).",
        file=sys.stderr,
    )
    print(format_progress(passing_after, total_after), file=sys.stderr)

    record_shift(
        project_dir,
        role="refiner",
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
        note=f"{new_count} new features added",
    )

    print(result.result)
