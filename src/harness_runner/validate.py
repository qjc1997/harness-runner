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


def _validate_single_feature(
    ft: dict,
    prefix: str,
    seen_ids: set[str],
    *,
    require_passes_false: bool = True,
) -> tuple[list[str], bool]:
    """Validate a single feature entry.

    Returns (errors, has_long_steps) where has_long_steps is True if the
    feature has 8+ steps. Updates seen_ids in-place.
    """
    errs: list[str] = []
    has_long_steps = False

    for field in ("id", "category", "priority", "description", "steps", "passes"):
        if field not in ft:
            errs.append(f"{prefix}: missing field {field!r}")

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
            has_long_steps = True
        for j, s in enumerate(steps):
            if not isinstance(s, str) or not s.strip():
                errs.append(f"{prefix}.steps[{j}]: must be non-empty string")

    if require_passes_false and ft.get("passes") is not False:
        errs.append(
            f"{prefix}: passes={ft.get('passes')!r} (must start as false; "
            "Planner must not mark anything passing)"
        )

    if "is_smoke" in ft:
        if ft.get("is_smoke") not in (True, False):
            errs.append(f"{prefix}: is_smoke must be true or false")

    return errs, has_long_steps


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
    long_step_count = 0

    for i, ft in enumerate(data):
        prefix = f"entry {i}"
        if not isinstance(ft, dict):
            errs.append(f"{prefix}: not a dict")
            continue
        entry_errs, has_long = _validate_single_feature(ft, prefix, seen_ids)
        errs.extend(entry_errs)
        if has_long:
            long_step_count += 1
        if isinstance(ft.get("is_smoke"), bool) and ft["is_smoke"]:
            smoke_count += 1

    errs.extend(_check_distribution(len(data), smoke_count, long_step_count))
    return errs


def _check_distribution(total: int, smoke_count: int, long_step_count: int) -> list[str]:
    """Check smoke and long-step distribution policies on a full feature list."""
    errs: list[str] = []
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
    required_long = _min_long_step_features(total)
    if long_step_count < required_long:
        errs.append(
            f"only {long_step_count} feature(s) have 8+ steps; "
            f"policy requires at least {required_long} for end-to-end coverage "
            f"(threshold scales with plan size: {total} features → "
            f"floor max({MIN_LONG_STEPS_FEATURES_FLOOR}, {total}//8) "
            f"= {required_long})"
        )
    return errs


# Fields that Refinement Planner must never modify on existing features.
_IMMUTABLE_FIELDS = ("id", "category", "priority", "description", "steps", "is_smoke")


def validate_refine_output(
    project_dir: Path,
    original_features: list[dict],
) -> list[str]:
    """Validate Refinement Planner output (append-only mode).

    Checks:
    1. feature_list.json is valid JSON list
    2. All original features are still present with immutable fields unchanged
    3. No existing feature had passes flipped (only Generator may flip passes)
    4. At least one new feature was added
    5. All new entries satisfy the per-entry schema
    6. Smoke count and long-step distribution re-evaluated on the full list
    """
    errors: list[str] = []

    feature_list = project_dir / "feature_list.json"
    if not feature_list.exists():
        return ["feature_list.json missing"]
    try:
        updated = json.loads(feature_list.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"feature_list.json invalid JSON: {e}"]

    if not isinstance(updated, list):
        return [f"feature_list.json must be a JSON array, got {type(updated).__name__}"]

    original_by_id: dict[str, dict] = {
        f["id"]: f for f in original_features if isinstance(f, dict) and "id" in f
    }
    updated_by_id: dict[str, dict] = {
        f["id"]: f for f in updated if isinstance(f, dict) and "id" in f
    }

    # 1. At least one new feature added
    if len(updated) <= len(original_features):
        errors.append(
            f"no new features added: updated list has {len(updated)} entries, "
            f"same as or fewer than original {len(original_features)}"
        )

    # 2. Immutability of originals
    for orig_id, orig in original_by_id.items():
        if orig_id not in updated_by_id:
            errors.append(f"original feature {orig_id!r} was removed")
            continue
        curr = updated_by_id[orig_id]
        for field in _IMMUTABLE_FIELDS:
            if orig.get(field) != curr.get(field):
                errors.append(
                    f"original feature {orig_id!r}: field {field!r} was mutated "
                    f"(was {orig.get(field)!r}, now {curr.get(field)!r})"
                )
        # Refinement Planner must not flip passes — only Generator may
        orig_passes = orig.get("passes")
        curr_passes = curr.get("passes")
        if orig_passes != curr_passes:
            errors.append(
                f"original feature {orig_id!r}: passes changed from "
                f"{orig_passes!r} to {curr_passes!r} "
                "(only Generator may flip passes)"
            )

    # 3. Validate new entries
    new_entries = [
        f for f in updated
        if isinstance(f, dict) and f.get("id") not in original_by_id
    ]
    seen_ids: set[str] = set(original_by_id.keys())
    for i, ft in enumerate(new_entries):
        if not isinstance(ft, dict):
            errors.append(f"new entry {i}: not a dict")
            continue
        entry_errs, _ = _validate_single_feature(ft, f"new entry {i}", seen_ids)
        errors.extend(entry_errs)

    # 4. Re-check distribution on full list
    smoke_count = sum(
        1 for f in updated
        if isinstance(f, dict) and f.get("is_smoke") is True
    )
    long_step_count = sum(
        1 for f in updated
        if isinstance(f, dict)
        and isinstance(f.get("steps"), list)
        and len(f["steps"]) >= 8
    )
    errors.extend(_check_distribution(len(updated), smoke_count, long_step_count))

    return errors


def format_errors(errors: list[str]) -> str:
    """Format a list of validation errors as a multi-line bullet list."""
    if not errors:
        return "(none)"
    return "\n".join(f"  - {e}" for e in errors)
