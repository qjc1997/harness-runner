"""Pluggable LLM backends.

Each backend wraps a different underlying integration (subprocess CLI,
direct API, third-party SDK) but exposes the same minimal `Backend`
protocol to the role modules.

Why this exists: from 2026-06-15, Anthropic separates programmatic
`claude -p` usage onto a metered credit pool at full API rates. We want
the option to swap to an API-direct or alternative-vendor backend
without touching role/orchestration code. See BACKLOG.md.

Select a backend via:
  - explicit name to `get_backend("<name>")`, or
  - `HARNESS_BACKEND` environment variable, or
  - default (`claude_cli`)
"""

import os
from typing import Any, Optional

from .base import Backend, RunResult
from .claude_cli import ClaudeCLIBackend, ShiftTimeoutError


def _load_anthropic_api() -> type:
    from .anthropic_api import AnthropicAPIBackend  # noqa: PLC0415
    return AnthropicAPIBackend


_REGISTRY: dict[str, Any] = {
    "claude_cli": ClaudeCLIBackend,
    "anthropic_api": _load_anthropic_api,   # lazy — avoids ImportError if sdk absent
    # "codex_cli":     CodexCLIBackend,
    # "gemini_cli":    GeminiCLIBackend,
}


def get_backend(name: Optional[str] = None) -> Backend:
    """Resolve a backend by name.

    Resolution order: explicit arg → HARNESS_BACKEND env var → "claude_cli".
    """
    chosen = name or os.environ.get("HARNESS_BACKEND") or "claude_cli"
    if chosen not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise RuntimeError(
            f"unknown backend: {chosen!r}\n"
            f"available: {available}\n"
            f"set HARNESS_BACKEND env var to override the default."
        )
    factory = _REGISTRY[chosen]
    # Lazy loaders are callables that return a class; direct entries are already classes.
    if callable(factory) and not isinstance(factory, type):
        cls = factory()
    else:
        cls = factory
    return cls()


__all__ = ["Backend", "RunResult", "ShiftTimeoutError", "get_backend"]
