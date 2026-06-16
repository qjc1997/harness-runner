"""Deterministic quality gate — catches degraded-data and config-drift bugs
that the Generator rationalizes past and self-testing (curl/API checks) misses.

Motivation (two real bugs from the rome-guide build, 2026-06-08):

1. **Config drift** — a later shift flipped the Vite dev-proxy target from
   `localhost:8000` to `localhost:8001` while the backend stayed on 8000. Every
   API call through the browser returned 502, but the backend curl-tested green,
   so it shipped. → `_check_config_drift`

2. **Placeholder data** — `seed_full.py` fell back to solid-color placeholder
   JPEGs when image downloads failed (blocked User-Agent). The feature "passed"
   (images present, list rendered) but the images were fake colored squares that
   no description could match. → `_check_placeholder_images`

Both are STATIC, deterministic checks: no LLM, no running servers, no network.
They run after each Generator shift (alongside the LLM code reviewer) and after
data-pipeline scripts. Findings are appended to claude-progress.txt so the next
shift fixes them — the same self-repair loop the code reviewer uses.

A FAIL verdict means "the feature is not actually done" — placeholder/degraded
output and broken cross-file config are completion failures, not style nits.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# PIL is optional — harness-runner has no hard runtime deps. Without it we fall
# back to a file-size heuristic for placeholder-image detection.
try:
    from PIL import Image  # type: ignore
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


@dataclass
class GateIssue:
    severity: str       # "high" | "medium" | "low"
    category: str       # "config-drift" | "placeholder-image" | "placeholder-text"
    location: str       # file path or "table:column"
    description: str
    suggestion: str


@dataclass
class GateResult:
    verdict: str        # "pass" | "warn" | "fail"
    issues: list[GateIssue] = field(default_factory=list)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "high")


# Directories that typically hold served static assets / data, relative to project root.
_DATA_DIR_HINTS = ("images", "img", "assets", "media", "static", "data", "public")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
_PLACEHOLDER_TEXT_PATTERNS = (
    re.compile(r"\blorem ipsum\b", re.I),
    re.compile(r"\bplaceholder\b", re.I),
    re.compile(r"\bTODO\b|\bFIXME\b"),
    re.compile(r"\bexample\.com\b", re.I),
    re.compile(r"\b(lorem|dolor sit amet)\b", re.I),
)
_PLACEHOLDER_IMAGE_MAX_BYTES = 5000  # files smaller than this are suspect


def quality_gate(project_dir: Path) -> GateResult:
    """Run all static quality checks. Never raises; returns a GateResult."""
    issues: list[GateIssue] = []
    for check in (_check_config_drift, _check_placeholder_images, _check_placeholder_text):
        try:
            issues.extend(check(project_dir))
        except Exception as e:  # a broken check must never block the shift
            print(f"[quality_gate] {check.__name__} errored (skipped): {e}", file=sys.stderr)

    if any(i.severity == "high" for i in issues):
        verdict = "fail"
    elif issues:
        verdict = "warn"
    else:
        verdict = "pass"
    return GateResult(verdict=verdict, issues=issues)


# ── Check 1: config drift (dev-proxy target port vs backend port) ──────────────

_VITE_TARGET_RE = re.compile(r"target:\s*['\"]https?://(?:localhost|127\.0\.0\.1):(\d+)['\"]")
_BACKEND_PORT_RES = (
    re.compile(r"BACKEND_PORT\s*[=:]\s*['\"]?(\d{2,5})"),          # init.sh / .env
    re.compile(r"--port\s+['\"]?(\d{2,5})"),                        # uvicorn --port N
    re.compile(r"uvicorn[^\n]*?:(\d{2,5})"),                        # host:port forms
    re.compile(r"port\s*=\s*(\d{2,5})", re.I),                      # port=8000
)


def _check_config_drift(project_dir: Path) -> list[GateIssue]:
    """The frontend dev-proxy target port must match the backend's listen port.

    Reads Vite proxy target(s) from frontend/vite.config.* and the backend port
    from init.sh / backend/.env, and flags any mismatch as HIGH (every browser
    API call would 502).
    """
    issues: list[GateIssue] = []

    proxy_ports: set[str] = set()
    proxy_file = ""
    for cfg in _find_files(project_dir, ("vite.config.ts", "vite.config.js", "vite.config.mjs")):
        text = _read(cfg)
        ports = _VITE_TARGET_RE.findall(text)
        if ports:
            proxy_ports.update(ports)
            proxy_file = _rel(cfg, project_dir)
    if not proxy_ports:
        return issues  # no dev proxy → nothing to check

    backend_ports = _detect_backend_ports(project_dir)
    if not backend_ports:
        return issues  # can't determine backend port → don't false-positive

    mismatched = proxy_ports - backend_ports
    if mismatched and not (proxy_ports & backend_ports):
        issues.append(GateIssue(
            severity="high",
            category="config-drift",
            location=proxy_file or "vite.config.ts",
            description=(
                f"Frontend dev-proxy targets port(s) {sorted(proxy_ports)} but the "
                f"backend listens on {sorted(backend_ports)}. Every API call through "
                f"the browser will fail (502/connection refused) even though the "
                f"backend curl-tests fine."
            ),
            suggestion=(
                f"Set the Vite proxy target port to {sorted(backend_ports)[0]} "
                f"(match the backend's actual listen port)."
            ),
        ))
    return issues


def _detect_backend_ports(project_dir: Path) -> set[str]:
    """Best-effort extraction of the backend listen port from start scripts/env."""
    ports: set[str] = set()
    for name in ("init.sh", "start.sh", "run.sh"):
        for f in _find_files(project_dir, (name,)):
            text = _read(f)
            m = re.search(r"BACKEND_PORT\s*=\s*['\"]?(\d{2,5})", text)
            if m:
                ports.add(m.group(1))
            for m in re.finditer(r"uvicorn[^\n|&]*?--port\s+['\"]?\$?\{?(\w+)\}?", text):
                # resolve --port "$BACKEND_PORT" via the var assignment captured above
                if m.group(1).isdigit():
                    ports.add(m.group(1))
    for env in _find_files(project_dir, (".env",)):
        m = re.search(r"BACKEND_PORT\s*=\s*['\"]?(\d{2,5})", _read(env))
        if m:
            ports.add(m.group(1))
    return ports


# ── Check 2: placeholder images served as real data ────────────────────────────

def _check_placeholder_images(project_dir: Path) -> list[GateIssue]:
    """Flag solid-color or tiny image files in served data dirs.

    These are the classic "download failed → generate a colored square" fallback.
    The feature looks complete (images render) but the content is fake.
    """
    issues: list[GateIssue] = []
    image_files = _collect_images(project_dir)
    if not image_files:
        return issues

    placeholders: list[str] = []
    for img in image_files:
        if _is_placeholder_image(img):
            placeholders.append(_rel(img, project_dir))

    if not placeholders:
        return issues

    total = len(image_files)
    n = len(placeholders)
    frac = n / total
    sample = ", ".join(placeholders[:8]) + (" …" if n > 8 else "")
    # Many placeholders → almost certainly a broken pipeline (HIGH). A few → WARN.
    severity = "high" if frac >= 0.10 or n >= 5 else "medium"
    issues.append(GateIssue(
        severity=severity,
        category="placeholder-image",
        location=f"{n}/{total} image files",
        description=(
            f"{n} of {total} served images are solid-color/empty placeholders "
            f"(a download-fallback artifact), not real content: {sample}"
        ),
        suggestion=(
            "Re-fetch real images (fix the root download failure — e.g. blocked "
            "User-Agent → use a browser UA or curl). Treat placeholder output as "
            "an incomplete feature, not a pass."
        ),
    ))
    return issues


def _is_placeholder_image(path: Path) -> bool:
    """True if the image is suspiciously small or a single flat color."""
    try:
        if path.stat().st_size < _PLACEHOLDER_IMAGE_MAX_BYTES:
            return True
    except OSError:
        return False
    if not _HAS_PIL:
        return False  # size check already done
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            colors = im.getcolors(maxcolors=16)  # None if >16 distinct colors
            return colors is not None and len(colors) <= 2
    except Exception:
        return False


def _collect_images(project_dir: Path, limit: int = 4000) -> list[Path]:
    """Collect image files under likely data dirs, skipping vendor/build dirs."""
    out: list[Path] = []
    skip = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".vite"}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        rel_parts = set(Path(root).relative_to(project_dir).parts)
        # Only scan dirs that look like asset/data dirs (or are under one)
        if not (rel_parts & set(_DATA_DIR_HINTS)):
            continue
        for fn in files:
            if fn.lower().endswith(_IMAGE_EXTS):
                out.append(Path(root) / fn)
                if len(out) >= limit:
                    return out
    return out


# ── Check 3: placeholder text in served data ────────────────────────────────────

def _check_placeholder_text(project_dir: Path) -> list[GateIssue]:
    """Flag lorem-ipsum / mock / TODO markers inside served JSON data files."""
    issues: list[GateIssue] = []
    skip = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", "package.json", "package-lock.json", "tsconfig.json"}
    checked = 0
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        rel_parts = set(Path(root).relative_to(project_dir).parts)
        if not (rel_parts & set(_DATA_DIR_HINTS)):
            continue
        for fn in files:
            if not fn.endswith(".json") or fn in skip:
                continue
            if checked >= 200:
                return issues
            checked += 1
            p = Path(root) / fn
            text = _read(p)
            if not text:
                continue
            for pat in _PLACEHOLDER_TEXT_PATTERNS:
                m = pat.search(text)
                if m:
                    issues.append(GateIssue(
                        severity="medium",
                        category="placeholder-text",
                        location=_rel(p, project_dir),
                        description=(
                            f"Served data file contains placeholder/mock text "
                            f"(matched {m.group(0)!r}) — looks like stub content, "
                            f"not real data."
                        ),
                        suggestion="Replace placeholder/mock content with real data.",
                    ))
                    break  # one finding per file is enough
    return issues


# ── progress-log integration (mirrors code_reviewer.append_*) ───────────────────

def append_quality_gate_to_progress(project_dir: Path, result: GateResult) -> None:
    """Append quality-gate findings to claude-progress.txt for the next shift."""
    if result.verdict == "pass":
        return
    progress_path = project_dir / "claude-progress.txt"
    lines = [
        "",
        f"## ⚠️ Quality Gate: {result.verdict.upper()}",
        f"Issues: {len(result.issues)} ({result.fail_count} high)",
        "",
    ]
    for i in result.issues:
        lines.append(f"- [{i.severity.upper()}] {i.category} @ {i.location}: {i.description} → {i.suggestion}")
    lines.append(
        "\nNext shift MUST resolve HIGH quality-gate issues — they mean the feature "
        "is not actually complete (broken config / placeholder data)."
        if result.fail_count
        else "\nNext shift should address MEDIUM quality-gate issues."
    )
    lines.append("")
    try:
        with progress_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except OSError as e:
        print(f"[quality_gate] could not append to progress: {e}", file=sys.stderr)


# ── small fs helpers ────────────────────────────────────────────────────────────

def _find_files(project_dir: Path, names: tuple[str, ...], limit: int = 50) -> list[Path]:
    out: list[Path] = []
    skip = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".vite"}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        for fn in files:
            if fn in names:
                out.append(Path(root) / fn)
                if len(out) >= limit:
                    return out
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
