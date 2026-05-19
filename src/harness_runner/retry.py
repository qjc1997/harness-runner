"""Retry helper for unattended Generator loops.

A single shift can fail for many transient reasons: claude subprocess
timeout (`ShiftTimeoutError`), network blip, rate limit, claude exiting
non-zero on a tool error. Without retry, one bad shift kills the whole
`run --loop 10` and we wake up to find only 2 shifts completed.

Policy:
  - Up to `max_retries` attempts per shift (default 3)
  - Exponential-ish backoff: 5s, 10s, 15s between attempts
  - After exhausting retries, log the failure and **continue to the next
    shift** rather than aborting the whole loop. Each generator shift is
    self-contained — the next one can read the broken state from disk
    and decide what to do.
"""

import sys
import time
from typing import Callable


def generate_with_retry(
    project_name: str,
    generate_fn: Callable[[str], None],
    *,
    max_retries: int = 3,
    backoff_base_sec: int = 5,
) -> bool:
    """Call `generate_fn(project_name)` with retries.

    Returns True if the shift eventually succeeded, False if all retries
    were exhausted. Does not re-raise — the caller (a loop) wants to
    continue to the next iteration regardless.
    """
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            generate_fn(project_name)
            return True
        except KeyboardInterrupt:
            # Don't swallow user-initiated abort.
            raise
        except Exception as e:
            last_err = e
            line = str(e).splitlines()[0]
            print(
                f"[loop] shift failed (attempt {attempt}/{max_retries}): {line}",
                file=sys.stderr,
            )
            if attempt < max_retries:
                delay = backoff_base_sec * attempt  # 5, 10, 15s
                print(f"[loop] retrying in {delay}s...", file=sys.stderr)
                time.sleep(delay)
    print(
        f"[loop] giving up on this shift after {max_retries} attempts: {last_err}",
        file=sys.stderr,
    )
    return False
