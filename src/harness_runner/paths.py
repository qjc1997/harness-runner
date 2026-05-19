import json
from pathlib import Path

# Layout: src/harness_runner/paths.py — repo root is two levels up from harness_runner/.
_HERE = Path(__file__).resolve().parent  # src/harness_runner/
_REPO_ROOT = _HERE.parent.parent  # repo root


def repo_dir() -> Path:
    return _REPO_ROOT


def projects_dir() -> Path:
    return _REPO_ROOT / "projects"


def prompts_dir() -> Path:
    return _REPO_ROOT / "prompts"


def templates_dir() -> Path:
    return _REPO_ROOT / "templates"


def count_features(project_dir: Path) -> tuple[int, int]:
    """Return (passing, total) from a project's feature_list.json.

    Returns (0, 0) if the file is missing or unparseable. Fail-safe.
    """
    features_file = project_dir / "feature_list.json"
    if not features_file.exists():
        return 0, 0
    try:
        with features_file.open(encoding="utf-8") as f:
            features = json.load(f)
        if not isinstance(features, list):
            return 0, 0
        total = len(features)
        passing = sum(1 for ft in features if isinstance(ft, dict) and ft.get("passes") is True)
        return passing, total
    except (json.JSONDecodeError, OSError):
        return 0, 0


def format_progress(passing: int, total: int) -> str:
    """One-line progress summary suitable for stderr."""
    if total == 0:
        return "[progress] feature_list.json not yet created"
    pct = passing / total * 100
    return f"[progress] {passing}/{total} features passing ({pct:.1f}%)"
