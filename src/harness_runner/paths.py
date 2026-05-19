import json
from pathlib import Path
from typing import Any

# Layout: src/harness_runner/paths.py — repo root is two levels up from harness_runner/.
_HERE = Path(__file__).resolve().parent  # src/harness_runner/
_REPO_ROOT = _HERE.parent.parent  # repo root


def repo_dir() -> Path:
    return _REPO_ROOT


def projects_dir() -> Path:
    return _REPO_ROOT / "projects"


def prompts_dir() -> Path:
    return _REPO_ROOT / "prompts"


def format_event(e: Any) -> str:
    """One-line summary of a stream-json event from `claude -p --output-format stream-json`."""
    if not isinstance(e, dict):
        return "[?]"

    t = e.get("type")

    if t == "system" and e.get("subtype") == "init":
        return f"[init] session={e.get('session_id')} model={e.get('model')}"

    if t == "assistant":
        msg = e.get("message") or {}
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
        cost = float(e.get("total_cost_usd") or 0)
        turns = int(e.get("num_turns") or 0)
        dur = int(e.get("duration_ms") or 0)
        return f"[result] cost=${cost:.4f} turns={turns} duration={dur/1000:.1f}s"

    return f"[{t}]"
