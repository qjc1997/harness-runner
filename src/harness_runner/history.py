"""Per-shift outcome log.

Each call to `record_shift()` appends one JSONL line to
`projects/<name>/.harness_history.jsonl`. The file is the canonical
record of "what happened this shift" — observable by NeoClaw or any
external watchdog without needing to parse markdown.

Schema (one line per shift, JSON):

  {
    "ts": "2026-05-19T13:45:00.000Z",
    "role": "planner" | "generator",
    "passing_before": 0,
    "passing_after": 1,
    "total": 30,
    "outcome": "ok" | "validation_failed" | "timeout" | "error",
    "session_id": "...",
    "cost_usd": 0.42,
    "duration_ms": 187432,
    "input_tokens": 12345,
    "output_tokens": 890,
    "model": "claude-sonnet-4-6",
    "note": "<short, optional>"
  }

A simple "stuck detector" can scan the last N entries and alert when
`passing_after - passing_before == 0` for too many in a row.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


HISTORY_FILE_NAME = ".harness_history.jsonl"


def record_shift(
    project_dir: Path,
    *,
    role: str,
    passing_before: int,
    passing_after: int,
    total: int,
    outcome: str,
    session_id: Optional[str] = None,
    cost_usd: Optional[float] = None,
    duration_ms: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    model: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Append a shift outcome row. Never raises — history is observability,
    not control flow, and must not break a live shift."""
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "role": role,
            "passing_before": passing_before,
            "passing_after": passing_after,
            "total": total,
            "outcome": outcome,
            "session_id": session_id,
            "cost_usd": cost_usd,
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
            "note": note,
        }
        with (project_dir / HISTORY_FILE_NAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        # Best-effort. If we can't write history, the shift itself still ran.
        pass
