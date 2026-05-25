"""Requirements Completeness Reviewer.

Runs after the Refinement Planner to automatically detect gaps in the
feature plan before it is committed. Uses a cheap model (Haiku) since
the task is analytical pattern-matching, not code generation.

Design notes:
- Silent by default (on_event=None) — caller logs the verdict summary
- Non-fatal: parse errors or backend failures return a ReviewResult with
  verdict="error" so the caller can decide whether to skip or abort
- Never records to .harness_history.jsonl — reviewer is internal QA,
  not a top-level shift
"""

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..backends import get_backend
from ..paths import prompts_dir


_REVIEW_JSON_RE = re.compile(
    r"REVIEW_RESULT_JSON:\s*(\{.*\})",
    re.DOTALL,
)


@dataclass
class ReviewGap:
    entity: str
    operation: str
    severity: str  # "high" | "medium" | "low"
    reason: str


@dataclass
class ReviewResult:
    verdict: str           # "pass" | "fail" | "error"
    gaps: list[ReviewGap] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: Optional[str] = None


def review(
    project_dir: Path,
    requirements: str,
    new_features: list[dict],
) -> ReviewResult:
    """Check completeness of newly added features against the requirements.

    Returns a ReviewResult. Never raises — errors are captured in
    ReviewResult.verdict == "error" so the caller can decide to skip.
    """
    t0 = time.monotonic()

    system_prompt_path = prompts_dir() / "reviewer.md"
    if not system_prompt_path.exists():
        return ReviewResult(
            verdict="error",
            error=f"reviewer.md not found at {system_prompt_path}",
        )

    system_prompt = system_prompt_path.read_text(encoding="utf-8")

    arch_path = project_dir / "ARCHITECTURE.md"
    arch_content = (
        arch_path.read_text(encoding="utf-8")
        if arch_path.exists()
        else "(ARCHITECTURE.md not present)"
    )

    new_features_json = json.dumps(new_features, indent=2, ensure_ascii=False)

    user_prompt = (
        f"# Requirements Brief\n\n{requirements}\n\n"
        f"# New Features Added\n\n```json\n{new_features_json}\n```\n\n"
        f"# Architecture Context\n\n{arch_content}\n\n"
        "Review the new features for completeness against the requirements. "
        "Work through the entity × operation matrix for every capability area, "
        "then output REVIEW_RESULT_JSON."
    )

    try:
        backend = get_backend()
        result = backend.run(
            cwd=str(project_dir),
            prompt=user_prompt,
            system_prompt_append=system_prompt,
            model="haiku",
            on_event=None,  # reviewer runs silently; caller logs verdict
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return ReviewResult(
            verdict="error",
            duration_ms=duration_ms,
            error=str(e).splitlines()[0],
        )

    duration_ms = int((time.monotonic() - t0) * 1000)
    cost_usd = result.cost_usd or 0.0

    return _parse_result(result.result, cost_usd, duration_ms)


def _parse_result(text: str, cost_usd: float, duration_ms: int) -> ReviewResult:
    """Parse REVIEW_RESULT_JSON block from reviewer output."""
    m = _REVIEW_JSON_RE.search(text)
    if not m:
        return ReviewResult(
            verdict="error",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=(
                "Reviewer did not produce REVIEW_RESULT_JSON block. "
                f"Last 200 chars: {text[-200:]!r}"
            ),
        )

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return ReviewResult(
            verdict="error",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=f"REVIEW_RESULT_JSON is not valid JSON: {e}",
        )

    raw_verdict = str(data.get("verdict", "")).upper()
    if raw_verdict not in ("PASS", "FAIL"):
        return ReviewResult(
            verdict="error",
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            error=f"Invalid verdict {raw_verdict!r}; expected PASS or FAIL",
        )

    gaps = [
        ReviewGap(
            entity=str(g.get("entity", "")),
            operation=str(g.get("operation", "")),
            severity=str(g.get("severity", "medium")).lower(),
            reason=str(g.get("reason", "")),
        )
        for g in (data.get("gaps") or [])
        if isinstance(g, dict)
    ]

    return ReviewResult(
        verdict="pass" if raw_verdict == "PASS" else "fail",
        gaps=gaps,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
    )
