"""Universal implementation quality reviewer.

Runs after each Generator shift and reviews the git diff for code quality
issues: fragile external API usage, missing error handling, small test datasets,
hardcoded values, and best practice violations.

Unlike the Requirements Reviewer (which checks feature completeness) and the
Playwright Evaluator (which checks UI behaviour), this reviewer checks whether
the *implementation* is robust and well-written — independent of whether the
feature passes its acceptance steps.

Design decisions:
- Uses Haiku (cheap — just needs to read and reason about code)
- Reviews the git diff, not the entire codebase
- Non-blocking: commits proceed regardless; issues are logged for next shift
- HIGH severity issues trigger an optional immediate fix shift
- Never raises — code review errors are logged but never block progress
"""

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..backends import get_backend
from ..paths import prompts_dir


_CODE_REVIEW_JSON_RE = re.compile(
    r"CODE_REVIEW_RESULT_JSON:\s*(\{.*\})",
    re.DOTALL,
)


@dataclass
class CodeIssue:
    file: str
    line_approx: int
    severity: str       # "high" | "medium" | "low"
    category: str
    description: str
    suggestion: str


@dataclass
class CodeReviewResult:
    verdict: str        # "pass" | "warn" | "fail" | "error"
    issues: list[CodeIssue] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: Optional[str] = None


def code_review(
    project_dir: Path,
    feature_ids: list[str],
    feature_descriptions: dict[str, str],
) -> CodeReviewResult:
    """Review the git diff from the most recent commit(s) for implementation quality.

    `feature_ids`: list of feature IDs completed in this shift (e.g. ["f056", "f057"])
    `feature_descriptions`: mapping from feature ID to its description string

    Returns a CodeReviewResult. Never raises.
    """
    t0 = time.monotonic()

    # Get git diff for the shift's commits
    diff = _get_shift_diff(project_dir, len(feature_ids))
    if not diff:
        return CodeReviewResult(
            verdict="error",
            error="Could not obtain git diff — skipping code review",
        )

    system_prompt_path = prompts_dir() / "code_reviewer.md"
    if not system_prompt_path.exists():
        return CodeReviewResult(
            verdict="error",
            error=f"code_reviewer.md not found at {system_prompt_path}",
        )

    system_prompt = system_prompt_path.read_text(encoding="utf-8")

    # Build feature summary
    feature_summary = "\n".join(
        f"- {fid}: {feature_descriptions.get(fid, '(no description)')}"
        for fid in feature_ids
    )

    # Truncate diff if very large (keep first 24000 chars — Haiku has 200k context,
    # cost is negligible, and data-fetching code often appears later in large diffs)
    diff_truncated = diff[:24000]
    if len(diff) > 24000:
        diff_truncated += f"\n\n[... diff truncated at 24000 chars, {len(diff) - 24000} more chars not shown ...]"

    user_prompt = (
        f"# Features implemented in this shift\n\n{feature_summary}\n\n"
        f"# Git diff\n\n```diff\n{diff_truncated}\n```\n\n"
        "Review this diff for implementation quality issues. "
        "Check all six categories from your protocol, then output CODE_REVIEW_RESULT_JSON."
    )

    try:
        backend = get_backend()
        result = backend.run(
            cwd=str(project_dir),
            prompt=user_prompt,
            system_prompt_append=system_prompt,
            model="haiku",
            on_event=None,  # silent
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return CodeReviewResult(
            verdict="error",
            duration_ms=duration_ms,
            error=str(e).splitlines()[0],
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    return _parse_result(result.result, result.cost_usd or 0.0, duration_ms)


def _get_shift_diff(project_dir: Path, num_features: int) -> str:
    """Get the combined git diff for the last N commits (one per feature)."""
    try:
        # Get commits from this shift (one per feature plus the shift log commit)
        n = max(num_features + 1, 2)
        result = subprocess.run(
            ["git", "diff", f"HEAD~{n}", "HEAD",
             "--", "*.py", "*.ts", "*.tsx", "*.js"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=15,
        )
        diff = result.stdout.strip()
        if not diff:
            # Fallback: just last commit
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "HEAD",
                 "--", "*.py", "*.ts", "*.tsx", "*.js"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=15,
            )
            diff = result.stdout.strip()
        return diff
    except Exception:
        return ""


def _parse_result(text: str, cost_usd: float, duration_ms: int) -> CodeReviewResult:
    """Parse CODE_REVIEW_RESULT_JSON block from reviewer output."""
    m = _CODE_REVIEW_JSON_RE.search(text)
    if not m:
        return CodeReviewResult(
            verdict="error",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=f"No CODE_REVIEW_RESULT_JSON block found. Last 200 chars: {text[-200:]!r}",
        )

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return CodeReviewResult(
            verdict="error",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=f"JSON parse error: {e}",
        )

    raw_verdict = str(data.get("verdict", "")).upper()
    if raw_verdict not in ("PASS", "WARN", "FAIL"):
        raw_verdict = "WARN"  # default to warn on unexpected verdict

    issues = [
        CodeIssue(
            file=str(i.get("file", "")),
            line_approx=int(i.get("line_approx", 0)),
            severity=str(i.get("severity", "medium")).lower(),
            category=str(i.get("category", "")),
            description=str(i.get("description", "")),
            suggestion=str(i.get("suggestion", "")),
        )
        for i in (data.get("issues") or [])
        if isinstance(i, dict)
    ]

    return CodeReviewResult(
        verdict=raw_verdict.lower(),
        issues=issues,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
    )


def append_code_review_to_progress(
    project_dir: Path,
    result: CodeReviewResult,
    feature_ids: list[str],
) -> None:
    """Append code review findings to claude-progress.txt for next shift to address."""
    progress_path = project_dir / "claude-progress.txt"
    if result.verdict in ("pass", "error"):
        return  # nothing worth logging

    lines = [
        "",
        f"## ⚠️ Code Review: {result.verdict.upper()} — {', '.join(feature_ids)}",
        f"Cost: ${result.cost_usd:.4f}  |  Issues: {len(result.issues)}",
        "",
    ]
    for issue in result.issues:
        lines.append(
            f"- [{issue.severity.upper()}] {issue.file}: {issue.description} "
            f"→ {issue.suggestion}"
        )
    lines.append(
        "\nNext Generator shift should address HIGH severity issues before new features."
        if any(i.severity == "high" for i in result.issues)
        else "\nNext Generator shift may optionally address MEDIUM issues."
    )
    lines.append("")

    try:
        with progress_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[code_reviewer] could not append to progress: {e}", file=sys.stderr)
