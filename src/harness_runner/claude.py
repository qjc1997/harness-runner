import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ClaudeResult:
    result: str
    session_id: str
    cost_usd: float
    duration_ms: int
    num_turns: int


def run_claude(
    *,
    cwd: str,
    prompt: str,
    system_prompt_append: Optional[str] = None,
    model: str = "sonnet",
    on_event: Optional[Callable[[dict], None]] = None,
) -> ClaudeResult:
    """Run `claude -p` as a one-shot subprocess with stream-json output.

    Each line of stdout is a JSON event (system/init, assistant, user/tool_result, result).
    The terminal `result` event carries the final response and session stats.
    """
    args: list[str] = [
        "claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--model",
        model,
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

    final_result: Optional[ClaudeResult] = None
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
            final_result = ClaudeResult(
                result=str(event.get("result") or ""),
                session_id=str(event.get("session_id") or ""),
                cost_usd=float(event.get("total_cost_usd") or 0),
                duration_ms=int(event.get("duration_ms") or 0),
                num_turns=int(event.get("num_turns") or 0),
            )

    exit_code = proc.wait()
    stderr_text = proc.stderr.read() if proc.stderr is not None else ""
    if exit_code != 0:
        raise RuntimeError(f"claude exited {exit_code}\nstderr:\n{stderr_text}")
    if final_result is None:
        raise RuntimeError(
            f"claude finished without a result event\nstderr:\n{stderr_text}"
        )
    return final_result
