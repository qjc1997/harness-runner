import json
import subprocess
from typing import Any, Callable, Optional

from .base import RunResult


class ClaudeCLIBackend:
    """Runs `claude -p` as a subprocess with stream-json output.

    Uses the locally-installed Claude Code CLI for auth and model
    resolution. **From 2026-06-15** this counts as "programmatic usage"
    on Anthropic subscriptions and drains a separate monthly credit pool
    at full API rates (see BACKLOG.md). For heavy usage, consider
    switching to a direct-API backend when one becomes available.
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

        final: Optional[RunResult] = None
        assert proc.stdout is not None
        for line in proc.stdout:
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
                final = RunResult(
                    result=str(event.get("result") or ""),
                    session_id=sid,
                    cost_usd=float(event.get("total_cost_usd") or 0),
                    duration_ms=int(event.get("duration_ms") or 0),
                    num_turns=int(event.get("num_turns") or 0),
                )

        exit_code = proc.wait()
        stderr_text = proc.stderr.read() if proc.stderr is not None else ""
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
