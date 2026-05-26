import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import RunResult


# Configurable via env. Defaults intentionally generous; tighten for cheap shifts.
SHIFT_TIMEOUT_SEC = int(os.environ.get("HARNESS_SHIFT_TIMEOUT_SEC", "1800"))  # 30 min
IDLE_TIMEOUT_SEC = int(os.environ.get("HARNESS_IDLE_TIMEOUT_SEC", "300"))    # 5 min


class ShiftTimeoutError(RuntimeError):
    """Raised when the claude subprocess hits the total or idle timeout.

    Callers (run --loop, generate-loop) should treat this as a transient
    failure: retry with a fresh shift rather than aborting the whole run.
    """


class ClaudeCLIBackend:
    """Runs `claude -p` as a subprocess with stream-json output.

    Uses the locally-installed Claude Code CLI for auth and model
    resolution. **From 2026-06-15** this counts as "programmatic usage"
    on Anthropic subscriptions and drains a separate monthly credit pool
    at full API rates (see BACKLOG.md).

    Robustness:
      - `stderr` is drained in a background thread to avoid the classic
        64KB pipe-buffer deadlock when claude logs more than the pipe
        can hold while we're busy reading `stdout`.
      - A watchdog thread enforces two timeouts:
        * total shift wall-clock (`HARNESS_SHIFT_TIMEOUT_SEC`, default 30 min)
        * idle / no-output  (`HARNESS_IDLE_TIMEOUT_SEC`, default 5 min)
        On either trip: SIGTERM, 5s grace, SIGKILL; caller gets a
        `ShiftTimeoutError`.
    """

    name = "claude_cli"

    def run(
        self,
        *,
        cwd: str,
        prompt: str,
        system_prompt_append: Optional[str] = None,
        model: Optional[str] = None,
        on_event: Optional[Callable[[Any], None]] = None,
    ) -> RunResult:
        args: list[str] = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--model",
            model or "sonnet",
        ]
        # Auto-load project-local .mcp.json if present so MCP tools
        # (e.g. Playwright) are available to the agent without manual setup.
        mcp_config = Path(cwd) / ".mcp.json"
        if mcp_config.exists():
            args.extend(["--mcp-config", str(mcp_config)])
        if system_prompt_append:
            args.extend(["--append-system-prompt", system_prompt_append])
        args.append(prompt)

        proc = subprocess.Popen(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # ── stderr drain (prevents 64KB pipe-buffer deadlock) ────────
        stderr_lines: list[str] = []

        def _drain_stderr() -> None:
            if proc.stderr is None:
                return
            try:
                for line in proc.stderr:
                    stderr_lines.append(line)
            except (OSError, ValueError):
                # Pipe closed during process teardown — fine.
                pass

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        # ── watchdog (total + idle timeout) ──────────────────────────
        last_event_at = [time.monotonic()]
        deadline = last_event_at[0] + SHIFT_TIMEOUT_SEC
        watchdog_done = threading.Event()
        timeout_reason: list[str] = []  # populated by watchdog if it kills

        def _watchdog() -> None:
            while not watchdog_done.is_set():
                now = time.monotonic()
                if now >= deadline:
                    timeout_reason.append(f"total shift timeout ({SHIFT_TIMEOUT_SEC}s)")
                    break
                if now - last_event_at[0] >= IDLE_TIMEOUT_SEC:
                    timeout_reason.append(
                        f"idle for {IDLE_TIMEOUT_SEC}s (no stdout)"
                    )
                    break
                # Poll every 5s — long enough not to burn CPU, short enough to
                # react within ~5s of crossing a threshold.
                watchdog_done.wait(timeout=5)

            if timeout_reason:
                # Trip: terminate the subprocess.
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                except (OSError, ProcessLookupError):
                    pass

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        # ── main stdout reader ───────────────────────────────────────
        final: Optional[RunResult] = None
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                last_event_at[0] = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_event is not None:
                    on_event(event)
                if event.get("type") == "result":
                    sid = str(event.get("session_id") or "") or None
                    usage = event.get("usage") or {}
                    # Total input includes cache hits — same convention Anthropic
                    # bills on and that NeoClaw uses.
                    total_input = (
                        int(usage.get("input_tokens") or 0)
                        + int(usage.get("cache_creation_input_tokens") or 0)
                        + int(usage.get("cache_read_input_tokens") or 0)
                    )
                    final = RunResult(
                        result=str(event.get("result") or ""),
                        session_id=sid,
                        cost_usd=float(event.get("total_cost_usd") or 0),
                        duration_ms=int(event.get("duration_ms") or 0),
                        num_turns=int(event.get("num_turns") or 0),
                        input_tokens=total_input if usage else None,
                        output_tokens=int(usage.get("output_tokens") or 0) if usage else None,
                        model=str(event.get("model") or "") or None,
                    )
        finally:
            # Stop watchdog regardless of how we exited the loop.
            watchdog_done.set()

        exit_code = proc.wait()
        stderr_thread.join(timeout=2)
        stderr_text = "".join(stderr_lines)

        # Timeout takes precedence: even if exit_code looks innocent
        # (terminated), the watchdog's reason is the real story.
        if timeout_reason:
            raise ShiftTimeoutError(
                f"claude killed by watchdog: {timeout_reason[0]}\n"
                f"partial stderr:\n{stderr_text[-2000:]}"
            )

        if exit_code != 0:
            raise RuntimeError(f"claude exited {exit_code}\nstderr:\n{stderr_text}")
        if final is None:
            raise RuntimeError(
                f"claude finished without a result event\nstderr:\n{stderr_text}"
            )
        return final

    def format_event(self, event: Any) -> str:
        """Render a `claude -p --output-format stream-json` event line."""
        if not isinstance(event, dict):
            return "[?]"
        t = event.get("type")

        if t == "system" and event.get("subtype") == "init":
            return f"[init] session={event.get('session_id')} model={event.get('model')}"

        if t == "assistant":
            msg = event.get("message") or {}
            blocks = msg.get("content") or []
            lines = []
            for b in blocks:
                bt = b.get("type")
                if bt == "text":
                    text = " ".join(str(b.get("text", "")).split())[:160]
                    lines.append(f"[text] {text}")
                elif bt == "tool_use":
                    input_str = json.dumps(b.get("input") or {})[:140]
                    lines.append(f"[tool] {b.get('name')}({input_str})")
                elif bt == "thinking":
                    text = " ".join(str(b.get("thinking", "")).split())[:140]
                    lines.append(f"[think] {text}")
                else:
                    lines.append(f"[{bt}]")
            return "\n".join(lines)

        if t == "user":
            return "[tool_result]"

        if t == "result":
            cost = float(event.get("total_cost_usd") or 0)
            turns = int(event.get("num_turns") or 0)
            dur = int(event.get("duration_ms") or 0)
            return f"[result] cost=${cost:.4f} turns={turns} duration={dur/1000:.1f}s"

        return f"[{t}]"
