import json
import sys
import time
from pathlib import Path
from typing import Optional

from ..backends import ShiftTimeoutError, get_backend
from ..history import record_shift
from ..paths import count_features, format_progress, projects_dir, prompts_dir
from ..quality_gate import append_quality_gate_to_progress, quality_gate
from .code_reviewer import CodeReviewResult, append_code_review_to_progress, code_review


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
            passing_after=passing_before,
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

    # ── Code Quality Review ───────────────────────────────────────────────────
    # Detect which features were completed this shift and review the implementation.
    new_passing_ids = _detect_new_passing(project_dir, passing_before, passing_after)
    review_result: Optional[CodeReviewResult] = None
    review_cost: float = 0.0

    if new_passing_ids and passing_after > passing_before:
        feature_descriptions = _load_feature_descriptions(project_dir, new_passing_ids)
        try:
            review_result = code_review(project_dir, new_passing_ids, feature_descriptions)
            review_cost = review_result.cost_usd
            _log_review(review_result, new_passing_ids)
            append_code_review_to_progress(project_dir, review_result, new_passing_ids)
        except Exception as rev_err:
            print(f"[code_reviewer] unexpected error (skipping): {rev_err}", file=sys.stderr)

    # ── Quality Gate (deterministic) ──────────────────────────────────────────
    # Static checks that catch degraded-data and config-drift bugs the LLM
    # reviewer and curl-based self-tests miss (placeholder images, dev-proxy
    # port mismatch). Runs every shift — cheap, no LLM, no servers.
    gate_note = ""
    try:
        gate_result = quality_gate(project_dir)
        if gate_result.verdict != "pass":
            append_quality_gate_to_progress(project_dir, gate_result)
            high = gate_result.fail_count
            symbol = "✗" if gate_result.verdict == "fail" else "⚠"
            print(
                f"[quality_gate] {symbol} verdict={gate_result.verdict} "
                f"issues={len(gate_result.issues)} ({high} high)",
                file=sys.stderr,
            )
            for i in gate_result.issues:
                print(f"[quality_gate]   [{i.severity}] {i.category} @ {i.location}", file=sys.stderr)
            gate_note = f"; quality_gate={gate_result.verdict.upper()} ({len(gate_result.issues)} issues)"
    except Exception as gate_err:
        print(f"[quality_gate] unexpected error (skipping): {gate_err}", file=sys.stderr)

    print("\n[generator] done.", file=sys.stderr)
    print(format_progress(passing_after, total_after), file=sys.stderr)

    review_note = ""
    if review_result:
        issue_count = len(review_result.issues)
        high_count = sum(1 for i in review_result.issues if i.severity == "high")
        if review_result.verdict == "fail":
            review_note = f"; code_review=FAIL ({high_count} high, {issue_count} total)"
        elif review_result.verdict == "warn":
            review_note = f"; code_review=WARN ({issue_count} issues)"
        elif review_result.verdict == "error":
            review_note = f"; code_review=error ({review_result.error})"

    combined_note = (review_note + gate_note).lstrip("; ")
    record_shift(
        project_dir,
        role="generator",
        passing_before=passing_before,
        passing_after=passing_after,
        total=total_after,
        outcome="ok",
        session_id=result.session_id,
        cost_usd=(result.cost_usd or 0.0) + review_cost,
        duration_ms=result.duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=result.model,
        note=combined_note or None,
    )

    print(result.result)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_new_passing(project_dir: Path, passing_before: int, passing_after: int) -> list[str]:
    """Return the IDs of features flipped to passes=true in this shift."""
    if passing_after <= passing_before:
        return []
    try:
        with (project_dir / "feature_list.json").open(encoding="utf-8") as f:
            features = json.load(f)
        # Features that pass now — we can't know exactly which were flipped this
        # shift without a before-snapshot, so return the last (passing_after -
        # passing_before) passing features ordered by id as a best approximation.
        passing = [ft for ft in features if isinstance(ft, dict) and ft.get("passes")]
        new_count = passing_after - passing_before
        return [ft["id"] for ft in passing[-new_count:] if "id" in ft]
    except Exception:
        return []


def _load_feature_descriptions(project_dir: Path, feature_ids: list[str]) -> dict[str, str]:
    """Load description strings for the given feature IDs."""
    try:
        with (project_dir / "feature_list.json").open(encoding="utf-8") as f:
            features = json.load(f)
        return {
            ft["id"]: ft.get("description", "")
            for ft in features
            if isinstance(ft, dict) and ft.get("id") in feature_ids
        }
    except Exception:
        return {}


def _log_review(result: CodeReviewResult, feature_ids: list[str]) -> None:
    """Print code review verdict to stderr."""
    verdict_symbol = {"pass": "✓", "warn": "⚠", "fail": "✗", "error": "?"}.get(
        result.verdict, "?"
    )
    print(
        f"[code_reviewer] {verdict_symbol} verdict={result.verdict} "
        f"issues={len(result.issues)} cost=${result.cost_usd:.4f}",
        file=sys.stderr,
    )
    for issue in result.issues:
        print(
            f"[code_reviewer]   [{issue.severity}] {issue.file}: {issue.description[:80]}",
            file=sys.stderr,
        )
