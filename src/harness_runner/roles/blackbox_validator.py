"""Black-box validator role.

An independent, adversarial reviewer that judges the RUNNING app from the outside:
real-world inputs → real public interface (frontend origin) → actual output →
semantic-correctness judgment. Detects mocked dependencies and refuses to pass
cases it could not truly exercise.

This is the mechanism that catches what the internal evaluator misses — it mirrors
what an external human/agent reviewer does (real inputs, semantic judgment, mock
detection), rather than checking the Generator's own structural signals.

Unlike the Generator/Evaluator, it NEVER edits code or flips feature flags. It
emits a report (blackbox_report.json) and appends failures to claude-progress.txt.

Test cases live in projects/<name>/blackbox_cases.json:
    [
      {"id": "bocca-della-verita",
       "input": "https://.../real_bocca_photo.jpg",
       "expected": "Identifies it as Bocca della Verità / Mouth of Truth, labeled non-curated (it is NOT in the collection) — must NOT confidently claim an unrelated collection item.",
       "out_of_kb": true}
    ]
"""

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..backends import ShiftTimeoutError, get_backend
from ..history import record_shift
from ..paths import projects_dir, prompts_dir

_RESULT_RE = re.compile(r"BLACKBOX_RESULT_JSON:\s*(\{.*\})", re.DOTALL)


@dataclass
class CaseResult:
    id: str
    result: str          # "pass" | "fail" | "inconclusive"
    expected: str = ""
    actual: str = ""
    evidence: str = ""


@dataclass
class BlackboxResult:
    verdict: str         # "pass" | "fail" | "inconclusive" | "error"
    cases: list[CaseResult] = field(default_factory=list)
    mock_detected: bool = False
    mock_detail: str = ""
    summary: str = ""
    cost_usd: float = 0.0
    error: Optional[str] = None


def blackbox(project_name: str) -> BlackboxResult:
    """Run a black-box validation shift against the running app. Never raises
    for validation outcomes (only for missing project/cases)."""
    project_dir = projects_dir() / project_name
    if not project_dir.exists():
        raise RuntimeError(f"project not found: {project_dir}")

    cases_path = project_dir / "blackbox_cases.json"
    if not cases_path.exists():
        raise RuntimeError(
            f"no blackbox_cases.json in {project_dir}\n"
            "Author real-world cases first: a JSON list of "
            '{"id","input","expected","out_of_kb"} objects.'
        )
    try:
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        assert isinstance(cases, list) and cases
    except Exception as e:
        raise RuntimeError(f"blackbox_cases.json invalid: {e}")

    system_prompt = (prompts_dir() / "blackbox_validator.md").read_text(encoding="utf-8")
    user_prompt = (
        "Begin black-box validation. First read ARCHITECTURE.md to find the "
        "frontend origin, the API shape, and any external dependencies (e.g. the "
        "VLM). Run the mock-detection probe once. Then exercise EACH case below "
        "end-to-end through the frontend origin with its REAL input, and judge "
        "semantic correctness. End with the BLACKBOX_RESULT_JSON line.\n\n"
        f"# Test cases\n```json\n{json.dumps(cases, ensure_ascii=False, indent=2)}\n```"
    )

    backend = get_backend()
    print(
        f"[blackbox] project={project_name} cases={len(cases)} backend={backend.name}",
        file=sys.stderr,
    )

    t0 = time.monotonic()
    try:
        run_result = backend.run(
            cwd=str(project_dir),
            prompt=user_prompt,
            system_prompt_append=system_prompt,
            on_event=lambda e: print(backend.format_event(e), file=sys.stderr),
        )
    except ShiftTimeoutError as e:
        record_shift(
            project_dir, role="blackbox", passing_before=0, passing_after=0,
            total=len(cases), outcome="timeout",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        return BlackboxResult(verdict="error", error=str(e).splitlines()[0])
    except Exception as e:
        record_shift(
            project_dir, role="blackbox", passing_before=0, passing_after=0,
            total=len(cases), outcome="error",
            duration_ms=int((time.monotonic() - t0) * 1000),
            note=str(e).splitlines()[0],
        )
        return BlackboxResult(verdict="error", error=str(e).splitlines()[0])

    result = _parse_result(run_result.result, run_result.cost_usd or 0.0)
    _write_report(project_dir, result)
    _append_to_progress(project_dir, result)

    passes = sum(1 for c in result.cases if c.result == "pass")
    record_shift(
        project_dir, role="blackbox",
        passing_before=passes, passing_after=passes, total=len(cases),
        outcome="ok",
        session_id=run_result.session_id, cost_usd=run_result.cost_usd,
        duration_ms=run_result.duration_ms,
        input_tokens=run_result.input_tokens, output_tokens=run_result.output_tokens,
        model=run_result.model,
        note=f"blackbox={result.verdict}"
             + (f"; MOCK:{result.mock_detail[:40]}" if result.mock_detected else ""),
    )
    print(run_result.result)
    return result


def _parse_result(text: str, cost_usd: float) -> BlackboxResult:
    m = _RESULT_RE.search(text)
    if not m:
        return BlackboxResult(
            verdict="error", cost_usd=cost_usd,
            error=f"No BLACKBOX_RESULT_JSON found. Last 200 chars: {text[-200:]!r}",
        )
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return BlackboxResult(verdict="error", cost_usd=cost_usd, error=f"JSON parse error: {e}")

    verdict = str(data.get("verdict", "")).lower()
    if verdict not in ("pass", "fail", "inconclusive"):
        verdict = "inconclusive"
    cases = [
        CaseResult(
            id=str(c.get("id", "?")),
            result=str(c.get("result", "inconclusive")).lower(),
            expected=str(c.get("expected", "")),
            actual=str(c.get("actual", "")),
            evidence=str(c.get("evidence", "")),
        )
        for c in (data.get("cases") or []) if isinstance(c, dict)
    ]
    return BlackboxResult(
        verdict=verdict,
        cases=cases,
        mock_detected=bool(data.get("mock_detected", False)),
        mock_detail=str(data.get("mock_detail", "")),
        summary=str(data.get("summary", "")),
        cost_usd=cost_usd,
    )


def _write_report(project_dir: Path, result: BlackboxResult) -> None:
    report = {
        "verdict": result.verdict,
        "mock_detected": result.mock_detected,
        "mock_detail": result.mock_detail,
        "summary": result.summary,
        "cases": [
            {"id": c.id, "result": c.result, "expected": c.expected,
             "actual": c.actual, "evidence": c.evidence}
            for c in result.cases
        ],
    }
    try:
        (project_dir / "blackbox_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        print(f"[blackbox] could not write report: {e}", file=sys.stderr)


def _append_to_progress(project_dir: Path, result: BlackboxResult) -> None:
    if result.verdict in ("pass", "error"):
        return
    lines = ["", f"## 🕵️ Black-Box Validation: {result.verdict.upper()}"]
    if result.mock_detected:
        lines.append(f"⚠️ MOCKED DEPENDENCY DETECTED — results unvalidated: {result.mock_detail}")
    for c in result.cases:
        if c.result == "pass":
            continue
        lines.append(f"- [{c.result.upper()}] {c.id}: expected {c.expected}")
        lines.append(f"    actual: {c.actual[:300]}")
        if c.evidence:
            lines.append(f"    → {c.evidence[:200]}")
    lines.append("\nNext shift must address FAIL cases. INCONCLUSIVE cases need a real "
                 "dependency (not a mock) to validate.\n")
    try:
        with (project_dir / "claude-progress.txt").open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[blackbox] could not append to progress: {e}", file=sys.stderr)
