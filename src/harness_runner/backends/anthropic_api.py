"""Direct Anthropic Messages API backend.

Bypasses the Claude CLI subprocess entirely. Uses the official `anthropic`
Python SDK with a full agentic tool-use loop, implementing the same six
tools that Claude Code provides natively: Read, Write, Edit, Glob, Grep, Bash.

Why this exists: from 2026-06-15, `claude -p` (the claude_cli backend) drains
a separate monthly credit pool on Anthropic subscriptions at full API rates.
For sustained usage, calling the API directly with your own API key is cleaner
and potentially cheaper.

Usage:
    HARNESS_BACKEND=anthropic_api ANTHROPIC_API_KEY=sk-ant-... harness-runner run <project>

Optional dep — install with:
    pip install "harness-runner[anthropic_api]"
    # or just: pip install anthropic>=0.28
"""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .base import RunResult

# Timeout config (same env vars as claude_cli for consistency)
SHIFT_TIMEOUT_SEC = int(os.environ.get("HARNESS_SHIFT_TIMEOUT_SEC", "1800"))
IDLE_TIMEOUT_SEC = int(os.environ.get("HARNESS_IDLE_TIMEOUT_SEC", "300"))

# Pricing per 1M tokens (claude-sonnet-4-6 as of 2026-05)
_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":      {"input": 3.0,  "output": 15.0},
    "claude-opus-4-7":        {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5":       {"input": 0.80, "output": 4.0},
}
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_RATES = {"input": 3.0, "output": 15.0}

# Tools exposed to the agent (mirrors what Claude Code provides via `claude -p`)
_TOOL_SCHEMAS = [
    {
        "name": "Read",
        "description": "Read a file from the local filesystem. Returns the file content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute or cwd-relative path"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-indexed)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "Write content to a file, creating parent directories as needed. Overwrites existing files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": (
            "Replace an exact string in a file. old_string must appear exactly once. "
            "Returns an error if it appears zero times or more than once."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Glob",
        "description": "Find files matching a glob pattern. Returns matching paths, one per line.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'"},
                "path": {"type": "string", "description": "Root directory (defaults to cwd)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "Search file contents with a regex pattern. Returns matching lines with file:line format.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search (defaults to cwd)"},
                "glob": {"type": "string", "description": "Glob filter, e.g. '*.py'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Bash",
        "description": "Execute a shell command. Returns stdout, stderr, and exit code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 60)"},
                "description": {"type": "string", "description": "Human-readable description (ignored)"},
            },
            "required": ["command"],
        },
    },
]


class AnthropicAPIBackend:
    """Agentic loop against the Anthropic Messages API with local tool implementations."""

    name = "anthropic_api"

    def run(
        self,
        *,
        cwd: str,
        prompt: str,
        system_prompt_append: Optional[str] = None,
        model: Optional[str] = None,
        on_event: Optional[Callable[[Any], None]] = None,
    ) -> RunResult:
        try:
            import anthropic  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "anthropic package not installed.\n"
                "Run: pip install 'anthropic>=0.28'\n"
                "or:  pip install 'harness-runner[anthropic_api]'"
            ) from None

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set.\n"
                "Export it before using the anthropic_api backend."
            )

        chosen_model = model or _DEFAULT_MODEL
        system = (
            "You are an expert full-stack developer building a production-quality "
            "web application.\n\n"
        )
        if system_prompt_append:
            system += system_prompt_append

        client = anthropic.Anthropic(api_key=api_key)
        messages: list[dict] = [{"role": "user", "content": prompt}]

        # Counters
        total_input_tokens = 0
        total_output_tokens = 0
        num_turns = 0
        t0 = time.monotonic()
        last_event_at = [t0]

        # Watchdog (same pattern as claude_cli)
        from .claude_cli import ShiftTimeoutError  # noqa: PLC0415

        deadline = t0 + SHIFT_TIMEOUT_SEC
        watchdog_done = threading.Event()
        timeout_reason: list[str] = []
        killed_proc: list[Any] = []  # placeholder — no subprocess here

        def _watchdog() -> None:
            while not watchdog_done.wait(timeout=5):
                now = time.monotonic()
                idle = now - last_event_at[0]
                if now >= deadline:
                    timeout_reason.append(f"total timeout ({SHIFT_TIMEOUT_SEC}s)")
                    watchdog_done.set()
                    return
                if idle >= IDLE_TIMEOUT_SEC:
                    timeout_reason.append(f"idle timeout ({IDLE_TIMEOUT_SEC}s without API response)")
                    watchdog_done.set()
                    return

        watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
        watchdog_thread.start()

        final_text = ""
        try:
            while True:
                if timeout_reason:
                    raise ShiftTimeoutError(
                        f"anthropic_api killed by watchdog: {timeout_reason[0]}"
                    )

                num_turns += 1
                response = client.messages.create(
                    model=chosen_model,
                    max_tokens=8192,
                    system=system,
                    tools=_TOOL_SCHEMAS,  # type: ignore[arg-type]
                    messages=messages,  # type: ignore[arg-type]
                )
                last_event_at[0] = time.monotonic()

                # Accumulate token usage
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens

                if on_event:
                    on_event({"type": "turn", "turn": num_turns, "response": response})

                # Collect text and tool calls from this response
                tool_results = []
                for block in response.content:
                    if block.type == "text":
                        final_text = block.text
                        if on_event:
                            on_event({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        if on_event:
                            on_event({
                                "type": "tool_use",
                                "name": block.name,
                                "input": block.input,
                            })
                        tool_output = self._dispatch_tool(block.name, block.input, cwd)
                        if on_event:
                            on_event({"type": "tool_result", "name": block.name})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_output,
                        })

                # Append assistant turn
                messages.append({"role": "assistant", "content": response.content})

                # Stop if no tool calls or end_turn
                if response.stop_reason == "end_turn" or not tool_results:
                    break

                # Continue loop with tool results
                messages.append({"role": "user", "content": tool_results})

        finally:
            watchdog_done.set()
            watchdog_thread.join(timeout=2)

        if timeout_reason:
            raise ShiftTimeoutError(
                f"anthropic_api killed by watchdog: {timeout_reason[0]}"
            )

        # Calculate cost
        rates = _RATES.get(chosen_model, _DEFAULT_RATES)
        cost_usd = (
            total_input_tokens * rates["input"] / 1_000_000
            + total_output_tokens * rates["output"] / 1_000_000
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if on_event:
            on_event({
                "type": "result",
                "cost_usd": cost_usd,
                "num_turns": num_turns,
                "duration_ms": duration_ms,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "model": chosen_model,
            })

        return RunResult(
            result=final_text,
            session_id=None,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            num_turns=num_turns,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            model=chosen_model,
        )

    # ── Tool dispatcher ───────────────────────────────────────────────────────

    def _dispatch_tool(self, name: str, inp: dict, cwd: str) -> str:
        try:
            if name == "Read":
                return self._tool_read(inp, cwd)
            if name == "Write":
                return self._tool_write(inp, cwd)
            if name == "Edit":
                return self._tool_edit(inp, cwd)
            if name == "Glob":
                return self._tool_glob(inp, cwd)
            if name == "Grep":
                return self._tool_grep(inp, cwd)
            if name == "Bash":
                return self._tool_bash(inp, cwd)
            return f"Error: unknown tool {name!r}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    def _resolve(self, raw_path: str, cwd: str) -> Path:
        """Resolve path relative to cwd; raise if outside cwd."""
        p = Path(raw_path)
        if not p.is_absolute():
            p = Path(cwd) / p
        resolved = p.resolve()
        cwd_resolved = Path(cwd).resolve()
        if not str(resolved).startswith(str(cwd_resolved)):
            raise PermissionError(
                f"path {raw_path!r} resolves outside project directory"
            )
        return resolved

    def _tool_read(self, inp: dict, cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        if not path.exists():
            return f"Error: file not found: {path}"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            return f"Error reading file: {e}"
        offset = max(0, int(inp.get("offset") or 0) - 1)
        limit = inp.get("limit")
        if offset:
            lines = lines[offset:]
        if limit:
            lines = lines[:int(limit)]
        # Return with 1-based line numbers (same as Claude Code's Read tool)
        start = (int(inp.get("offset") or 1))
        numbered = "\n".join(f"{start + i}\t{l}" for i, l in enumerate(lines))
        return numbered or "(empty file)"

    def _tool_write(self, inp: dict, cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inp["content"], encoding="utf-8")
        return f"Written {len(inp['content'])} bytes to {path}"

    def _tool_edit(self, inp: dict, cwd: str) -> str:
        path = self._resolve(inp["file_path"], cwd)
        if not path.exists():
            return f"Error: file not found: {path}"
        old = inp["old_string"]
        new = inp["new_string"]
        content = path.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return f"Error: old_string not found in {path.name}"
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {path.name} — "
                "provide more context to make it unique"
            )
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path.name}: replaced 1 occurrence"

    def _tool_glob(self, inp: dict, cwd: str) -> str:
        root = Path(inp.get("path") or cwd)
        if not root.is_absolute():
            root = Path(cwd) / root
        pattern = inp["pattern"]
        try:
            matches = sorted(str(p) for p in root.glob(pattern))
        except Exception as e:
            return f"Error: {e}"
        return "\n".join(matches) if matches else "(no matches)"

    def _tool_grep(self, inp: dict, cwd: str) -> str:
        pattern = inp["pattern"]
        search_path = inp.get("path") or cwd
        glob_filter = inp.get("glob")
        cmd = ["grep", "-rn", "--include", glob_filter or "*", pattern, search_path]
        if not glob_filter:
            cmd = ["grep", "-rn", pattern, search_path]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, cwd=cwd
            )
            output = result.stdout or result.stderr
            return output[:8000] if output else "(no matches)"
        except subprocess.TimeoutExpired:
            return "Error: grep timed out"
        except FileNotFoundError:
            return "Error: grep not found on PATH"

    def _tool_bash(self, inp: dict, cwd: str) -> str:
        command = inp["command"]
        timeout = int(inp.get("timeout") or 60)
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            parts = []
            if result.stdout:
                parts.append(result.stdout[:6000])
            if result.stderr:
                parts.append(f"[stderr]\n{result.stderr[:2000]}")
            if result.returncode != 0:
                parts.append(f"[exit {result.returncode}]")
            return "\n".join(parts) or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout}s"

    # ── Event formatting ──────────────────────────────────────────────────────

    def format_event(self, event: Any) -> str:
        if not isinstance(event, dict):
            return "[?]"
        t = event.get("type")
        if t == "turn":
            return f"[turn {event.get('turn')}]"
        if t == "text":
            text = " ".join(str(event.get("text", "")).split())[:160]
            return f"[text] {text}"
        if t == "tool_use":
            input_str = json.dumps(event.get("input") or {})[:140]
            return f"[tool] {event.get('name')}({input_str})"
        if t == "tool_result":
            return f"[tool_result] {event.get('name')}"
        if t == "result":
            cost = float(event.get("cost_usd") or 0)
            turns = int(event.get("num_turns") or 0)
            dur = int(event.get("duration_ms") or 0)
            return f"[result] cost=${cost:.4f} turns={turns} duration={dur/1000:.1f}s"
        return f"[{t}]"
