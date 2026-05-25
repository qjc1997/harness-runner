import json
import sys
import time
from pathlib import Path
from typing import Optional

from ..backends import ShiftTimeoutError, get_backend
from ..git_util import git_commit_all
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir
from ..validate import format_errors, validate_refine_output
from .reviewer import ReviewResult, review

MAX_REFINER_ATTEMPTS = 2


def refine(project_name: str, requirements: str) -> None:
    """Append-only incremental planning: add new features for new requirements.

    Flow:
    1. Refiner LLM appends features
    2. Reviewer checks completeness (Haiku, silent)
    3. If gaps found and attempts remain, re-run Refiner with gap feedback
    4. After max attempts, commit with warning if gaps still remain
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

    existing_plan_json = json.dumps(original_features, indent=2, ensure_ascii=False)
    system_prompt = (prompts_dir() / "refiner.md").read_text(encoding="utf-8")
    base_user_prompt = (
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
        f"[refiner] existing features: {original_count} ({passing_before} passing)",
        file=sys.stderr,
    )

    t0 = time.monotonic()
    total_cost_usd: float = 0.0
    result = None
    final_review: Optional[ReviewResult] = None
    reviewer_error: Optional[str] = None
    augmented_prompt = base_user_prompt

    for attempt in range(1, MAX_REFINER_ATTEMPTS + 1):
        print(f"\n[refiner] attempt {attempt}/{MAX_REFINER_ATTEMPTS}", file=sys.stderr)

        # ── Refiner LLM call ─────────────────────────────────────────────────
        try:
            result = backend.run(
                cwd=str(project_dir),
                prompt=augmented_prompt,
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

        total_cost_usd += result.cost_usd or 0.0

        # ── Reviewer pass (silent, cheap model) ──────────────────────────────
        try:
            new_features = _read_new_features(project_dir, original_count)
            rev = review(project_dir, requirements, new_features)
            total_cost_usd += rev.cost_usd
            final_review = rev

            if rev.verdict == "error":
                reviewer_error = rev.error or "unknown reviewer error"
                print(
                    f"[reviewer] error (skipping): {reviewer_error}",
                    file=sys.stderr,
                )
                break  # Non-fatal: proceed to validate + commit

            print(
                f"[reviewer] attempt={attempt} verdict={rev.verdict} "
                f"gaps={len(rev.gaps)} cost=${rev.cost_usd:.4f}",
                file=sys.stderr,
            )
            for gap in rev.gaps:
                print(
                    f"[reviewer]   [{gap.severity}] {gap.entity} / "
                    f"{gap.operation}: {gap.reason}",
                    file=sys.stderr,
                )

            if rev.verdict == "pass":
                break  # All good — proceed to commit

            if attempt == MAX_REFINER_ATTEMPTS:
                # Exhausted iterations — commit with warning
                _append_gap_warning(project_dir, rev)
                break

            # Prepare augmented prompt for next Refiner attempt
            augmented_prompt = base_user_prompt + _format_gap_feedback(rev)

        except Exception as rev_err:
            reviewer_error = str(rev_err).splitlines()[0]
            print(
                f"[reviewer] unexpected error (skipping): {reviewer_error}",
                file=sys.stderr,
            )
            break

    # ── Validate append-only contract ────────────────────────────────────────
    assert result is not None  # loop always runs at least once
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
            cost_usd=total_cost_usd,
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

    reviewer_note = ""
    if final_review and final_review.verdict != "error":
        reviewer_note = (
            f"; reviewer={final_review.verdict}"
            + (f" ({len(final_review.gaps)} gaps remain)" if final_review.gaps else "")
        )
    elif reviewer_error:
        reviewer_note = f"; reviewer error: {reviewer_error}"

    print(
        f"\n[refiner] done: {new_count} new features added "
        f"(total {total_after}, {passing_after} passing){reviewer_note}.",
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
        cost_usd=total_cost_usd,
        duration_ms=result.duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=result.model,
        note=f"{new_count} new features added{reviewer_note}",
    )

    print(result.result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_new_features(project_dir: Path, original_count: int) -> list[dict]:
    """Slice feature_list.json to return only entries appended after original_count."""
    try:
        updated = json.loads(
            (project_dir / "feature_list.json").read_text(encoding="utf-8")
        )
        if isinstance(updated, list):
            return updated[original_count:]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _format_gap_feedback(review_result: ReviewResult) -> str:
    """Format gap list as additional context for the next Refiner attempt."""
    lines = [
        "\n\n---",
        "## Completeness Reviewer found gaps in your output",
        "",
        "The following operations were identified as missing from the features you added.",
        "You MUST add feature entries for every HIGH and MEDIUM severity gap.",
        "LOW severity gaps should be added unless you can justify N/A in one sentence.",
        "Continue the feature ID sequence from where you left off.",
        "Do NOT modify any features you already wrote.",
        "",
    ]
    for gap in review_result.gaps:
        lines.append(
            f"- [{gap.severity.upper()}] Entity: {gap.entity} | "
            f"Operation: {gap.operation} | "
            f"Why it matters: {gap.reason}"
        )
    lines += ["", "Append additional features to feature_list.json now."]
    return "\n".join(lines)


def _append_gap_warning(project_dir: Path, review_result: ReviewResult) -> None:
    """Append reviewer gap summary to claude-progress.txt as a warning for Generator."""
    progress_path = project_dir / "claude-progress.txt"
    lines = [
        "",
        "## ⚠️ Reviewer: unresolved gaps after max iterations",
        "",
        "The Requirements Reviewer ran 2 iterations and still found gaps.",
        "These were committed — the Generator should address them if they affect correctness.",
        "",
    ]
    for gap in review_result.gaps:
        lines.append(
            f"- [{gap.severity.upper()}] {gap.entity} / {gap.operation}: {gap.reason}"
        )
    lines.append("")
    try:
        with progress_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[reviewer] could not append gap warning: {e}", file=sys.stderr)
