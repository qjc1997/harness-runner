"""Out-of-band notifications when an unattended loop stops.

Channels (all optional, combined; a failing channel never raises):
  - Discord webhook: `HARNESS_DISCORD_WEBHOOK`, or the first line of
    `~/.config/harness-runner/discord_webhook` (keep it out of git; chmod 600).
  - Custom command: `HARNESS_NOTIFY_CMD` (shell), given the message in the
    `HARNESS_NOTIFY_TITLE` / `HARNESS_NOTIFY_BODY` environment variables.
  - macOS notification centre (default on macOS; `HARNESS_NOTIFY_MACOS=0` disables).
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

DISCORD_LIMIT = 2000  # Discord message content limit
_WEBHOOK_FILE = Path("~/.config/harness-runner/discord_webhook").expanduser()


def discord_webhook_url() -> Optional[str]:
    url = os.environ.get("HARNESS_DISCORD_WEBHOOK", "").strip()
    if url:
        return url
    try:
        first = _WEBHOOK_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    return first[0].strip() if first else None


def _send_discord(url: str, title: str, body: str) -> None:
    content = f"**{title}**\n{body}"
    if len(content) > DISCORD_LIMIT:
        content = content[: DISCORD_LIMIT - 1] + "…"
    req = urllib.request.Request(
        url,
        data=json.dumps({"username": "harness-runner", "content": content}).encode("utf-8"),
        # Discord rejects the default Python-urllib user agent.
        headers={"Content-Type": "application/json", "User-Agent": "harness-runner (notify, 1.0)"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"discord webhook HTTP {resp.status}")


def _send_cmd(cmd: str, title: str, body: str) -> None:
    env = {**os.environ, "HARNESS_NOTIFY_TITLE": title, "HARNESS_NOTIFY_BODY": body}
    subprocess.run(cmd, shell=True, env=env, timeout=30, check=True)


def _send_macos(title: str, body: str) -> None:
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    script = f"display notification {q(body[:200])} with title {q(title[:80])}"
    subprocess.run(["osascript", "-e", script], timeout=10, check=True, capture_output=True)


def notify(title: str, body: str) -> List[str]:
    """Send `title`/`body` on every configured channel; return the channels that succeeded."""
    sent: List[str] = []
    attempts = []
    url = discord_webhook_url()
    if url:
        attempts.append(("discord", lambda: _send_discord(url, title, body)))
    cmd = os.environ.get("HARNESS_NOTIFY_CMD", "").strip()
    if cmd:
        attempts.append(("cmd", lambda: _send_cmd(cmd, title, body)))
    if sys.platform == "darwin" and os.environ.get("HARNESS_NOTIFY_MACOS", "1") != "0":
        attempts.append(("macos", lambda: _send_macos(title, body)))
    for name, send in attempts:
        try:
            send()
            sent.append(name)
        except Exception as e:  # a broken channel must never stop the loop's shutdown
            print(f"[notify] {name} failed: {str(e).splitlines()[0] if str(e) else e!r}", file=sys.stderr)
    return sent
