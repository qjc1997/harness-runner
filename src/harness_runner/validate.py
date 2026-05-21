"""Structural validation of planner output.

LLM output is untrusted by design. Before committing what the Planner
produced, run a schema check; if the plan is malformed, abort early
rather than letting 50 Generator shifts burn dollars on a bad spec.
"""

import json
from pathlib import Path
from typing import Optional


VALID_CATEGORIES = {"functional", "style"}
VALID_PRIORITIES = {"high", "medium", "low"}

MIN_FEATURES = 10  # Loose floor — Planner should hit 30-80 per the prompt
MAX_FEATURES = 200  # Sanity cap — Anthropic ref is 200, anything above is suspicious
MIN_SMOKE_FEATURES = 3  # Generator's regression check needs at least this many
MAX_SMOKE_FEATURES = 10  # Too many "smoke" defeats the point

# Long-flow features (`steps` array length ≥ 8) cover full end-to-end user
# journeys, which is what catches multi-step regressions that shallow tests
# miss. The minimum count scales with plan size — a 30-feature plan and an
# 80-feature plan shouldn't have the same flat threshold (8/30 = 27% vs
# 8/80 = 10%). Floor of 6 keeps tiny plans honest; `total // 8` gives
# roughly 12% on larger plans.
MIN_LONG_STEPS_FEATURES_FLOOR = 6  # absolute minimum regardless of size


def _min_long_step_features(total: int) -> int:
    return max(MIN_LONG_STEPS_FEATURES_FLOOR, total // 8)


def validate_planner_output(project_dir: Path) -> list[str]:
    """Return a list of validation errors. Empty list = OK.

    Checks structure of feature_list.json (required by Generator) plus
    presence of init.sh and claude-progress.txt (referenced by Generator's
    startup protocol).
    """
    errors: list[str] = []

    # ── feature_list.json ────────────────────────────────────────
    feature_list = project_dir / "feature_list.json"
    if not feature_list.exists():
        errors.append("feature_list.json missing")
        # Skip per-entry checks if the file isn't there at all
    else:
        errors.extend(_validate_feature_list(feature_list))

    # ── companion artifacts ──────────────────────────────────────
    if not (project_dir / "init.sh").exists():
        errors.append("init.sh missing")
    if not (project_dir / "claude-progress.txt").exists():
        errors.append("claude-progress.txt missing")

    return errors


def _validate_feature_list(path: Path) -> list[str]:
    errs: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"feature_list.json invalid JSON: {e}"]
    except OSError as e:
        return [f"feature_list.json read error: {e}"]

    if not isinstance(data, list):
        return [f"feature_list.json must be a JSON array, got {type(data).__name__}"]

    if len(data) < MIN_FEATURES:
        errs.append(
            f"feature_list.json has only {len(data)} entries; "
            f"expected at least {MIN_FEATURES} (target 30-80)"
        )
    if len(data) > MAX_FEATURES:
        errs.append(
            f"feature_list.json has {len(data)} entries; sanity cap is {MAX_FEATURES}"
        )

    seen_ids: set[str] = set()
    smoke_count = 0
    long_step_count = 0  # features with 8+ steps

    for i, ft in enumerate(data):
        prefix = f"entry {i}"
        if not isinstance(ft, dict):
            errs.append(f"{prefix}: not a dict")
            continue

        # Required fields
        for field in ("id", "category", "priority", "description", "steps", "passes"):
            if field not in ft:
                errs.append(f"{prefix}: missing field {field!r}")

        # Type checks
        fid = ft.get("id")
        if isinstance(fid, str):
            if fid in seen_ids:
                errs.append(f"{prefix}: duplicate id {fid!r}")
            else:
                seen_ids.add(fid)
        else:
            errs.append(f"{prefix}: id must be string, got {type(fid).__name__}")

        cat = ft.get("category")
        if cat not in VALID_CATEGORIES:
            errs.append(f"{prefix}: category={cat!r} not in {sorted(VALID_CATEGORIES)}")

        pri = ft.get("priority")
        if pri not in VALID_PRIORITIES:
            errs.append(f"{prefix}: priority={pri!r} not in {sorted(VALID_PRIORITIES)}")

        desc = ft.get("description")
        if not isinstance(desc, str) or not desc.strip():
            errs.append(f"{prefix}: description must be non-empty string")

        steps = ft.get("steps")
        if not isinstance(steps, list) or len(steps) < 2:
            errs.append(f"{prefix}: steps must be a list of at least 2 items")
        else:
            if len(steps) >= 8:
                long_step_count += 1
            for j, s in enumerate(steps):
                if not isinstance(s, str) or not s.strip():
                    errs.append(f"{prefix}.steps[{j}]: must be non-empty string")

        if ft.get("passes") is not False:
            errs.append(
                f"{prefix}: passes={ft.get('passes')!r} (must start as false; "
                "Planner must not mark anything passing)"
            )

        # is_smoke is optional but if present must be a bool
        if "is_smoke" in ft:
            if ft.get("is_smoke") is True:
                smoke_count += 1
            elif ft.get("is_smoke") is not False:
                errs.append(f"{prefix}: is_smoke must be true or false")

    # Distribution policies
    if smoke_count < MIN_SMOKE_FEATURES:
        errs.append(
            f"only {smoke_count} feature(s) marked is_smoke:true; "
            f"need at least {MIN_SMOKE_FEATURES} for Generator's regression check"
        )
    if smoke_count > MAX_SMOKE_FEATURES:
        errs.append(
            f"{smoke_count} features marked is_smoke:true; "
            f"sanity cap is {MAX_SMOKE_FEATURES} (smoke should be a small core set)"
        )

    required_long = _min_long_step_features(len(data))
    if long_step_count < required_long:
        errs.append(
            f"only {long_step_count} feature(s) have 8+ steps; "
            f"policy requires at least {required_long} for end-to-end coverage "
            f"(threshold scales with plan size: {len(data)} features → "
            f"floor max({MIN_LONG_STEPS_FEATURES_FLOOR}, {len(data)}//8) "
            f"= {required_long})"
        )

    return errs


def format_errors(errors: list[str]) -> str:
    """Format a list of validation errors as a multi-line bullet list."""
    if not errors:
        return "(none)"
    return "\n".join(f"  - {e}" for e in errors)
