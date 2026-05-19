"""Per-project budget cap.

When `HARNESS_BUDGET_USD` is set, a loop driver (run --loop /
generate-loop) checks the cumulative cost recorded in
`.harness_history.jsonl` before each generator shift and aborts the
loop with a `BudgetExceededError` once the project has spent more
than the limit.

This is a soft cap — the *current* shift in flight always completes;
the check fires before the *next* one starts. So you can overshoot
by one shift's worth of cost (~$1-5 for a Sonnet generator shift).

Set the limit by env:
    HARNESS_BUDGET_USD=80 harness-runner run mini-hex --loop 100

Unset (default): no cap, loop runs until shift count or interrupt.
"""

import json
import os
from pathlib import Path
from typing import Optional

from .history import HISTORY_FILE_NAME


class BudgetExceededError(RuntimeError):
    """Raised when cumulative project cost has exceeded HARNESS_BUDGET_USD.

    Loop drivers should catch this, log it, and break out cleanly —
    NOT retry. The user explicitly set a cap; bypassing it would
    defeat the purpose.
    """


def get_budget_limit() -> Optional[float]:
    """Return HARNESS_BUDGET_USD parsed as a positive float, or None.

    Invalid / non-positive values are treated as "unset" rather than
    raising — a typo in env shouldn't abort the loop.
    """
    raw = os.environ.get("HARNESS_BUDGET_USD")
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if v <= 0:
        return None
    return v


def project_cost_total(project_dir: Path) -> float:
    """Sum `cost_usd` across all history entries for this project.

    Returns 0.0 if the history file is missing or unreadable. Skips
    malformed JSON lines and entries with non-numeric cost_usd.
    """
    history_file = project_dir / HISTORY_FILE_NAME
    if not history_file.exists():
        return 0.0
    total = 0.0
    try:
        with history_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                c = row.get("cost_usd")
                if isinstance(c, (int, float)):
                    total += float(c)
    except OSError:
        return 0.0
    return total


def assert_under_budget(project_dir: Path) -> None:
    """Raise BudgetExceededError if accumulated cost is at/over the limit.

    Does nothing if HARNESS_BUDGET_USD is unset or invalid.
    """
    limit = get_budget_limit()
    if limit is None:
        return
    spent = project_cost_total(project_dir)
    if spent >= limit:
        raise BudgetExceededError(
            f"project has spent ${spent:.4f}, "
            f"which meets/exceeds HARNESS_BUDGET_USD=${limit:.2f}"
        )
