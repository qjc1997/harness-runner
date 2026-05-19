from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable


@dataclass
class RunResult:
    """Backend-agnostic result of one agent invocation.

    `cost_usd`, `session_id`, `duration_ms`, `num_turns` may be `None`
    if a backend doesn't report them.
    """

    result: str
    session_id: Optional[str] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None


@runtime_checkable
class Backend(Protocol):
    """Pluggable LLM backend that runs one shift to completion.

    Each backend wraps a different underlying integration (Claude CLI
    subprocess, direct Anthropic Messages API, OpenAI Codex CLI, ...)
    but exposes the same minimal contract to the roles.
    """

    name: str
    """Short identifier shown in logs (e.g. "claude_cli", "anthropic_api")."""

    def run(
        self,
        *,
        cwd: str,
        prompt: str,
        system_prompt_append: Optional[str] = None,
        model: Optional[str] = None,
        on_event: Optional[Callable[[Any], None]] = None,
    ) -> RunResult:
        """Run one shift to completion in `cwd`.

        `on_event` (if given) receives backend-defined event objects as
        they arrive. Use `format_event` to render them.
        """
        ...

    def format_event(self, event: Any) -> str:
        """Render a backend-specific event as a single human-readable line."""
        ...
