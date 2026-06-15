import sys

from .budget import BudgetExceededError, assert_under_budget
from .paths import count_features, projects_dir
from .quality_gate import append_quality_gate_to_progress, quality_gate
from .retry import generate_with_retry
from .roles.blackbox_validator import blackbox
from .roles.code_reviewer import append_code_review_to_progress, code_review
from .roles.evaluator import evaluate
from .roles.generator import generate
from .roles.planner import plan
from .roles.refiner import refine
from .run import run
from .scaffold import init


def _usage() -> None:
    print(
        "\n".join(
            [
                "usage:",
                '  harness-runner init <project-name> ["<brief>"]',
                "      Scaffold projects/<name>/app_spec.md and git repo.",
                "      If <brief> is given, it replaces the template; otherwise edit",
                "      app_spec.md by hand before running plan/run.",
                "",
                "  harness-runner run <project-name> [--loop N]",
                "      High-level entry. Auto-detects:",
                "      - no feature_list.json yet → runs Planner, then N=1 generator shift",
                "      - feature_list.json present → runs N generator shifts",
                "",
                "  harness-runner plan <project-name>",
                "      Explicit planner shift (reads projects/<name>/app_spec.md).",
                "",
                "  harness-runner generate <project-name>",
                "      Single generator shift.",
                "",
                "  harness-runner generate-loop <project-name> [n=5]",
                "      Multiple generator shifts back-to-back.",
                "",
                '  harness-runner refine <project-name> "<requirements>"',
                "      Append-only incremental planning: read existing feature_list.json",
                "      and add new features for new requirements. Never modifies",
                "      existing features. Run generate-loop afterwards to implement them.",
                "",
                "  harness-runner evaluate <project-name>",
                "      Single Playwright-based evaluation shift. Verifies features from",
                "      a real browser perspective; flips passes true→false on regressions.",
                "",
                "  harness-runner code-review <project-name> [feature-ids...]",
                "      Review implementation quality of the latest git diff using Haiku.",
                "      Checks robustness, API usage, error handling, and best practices.",
                "      If feature-ids omitted, reviews the last N commits automatically.",
                "",
                "  harness-runner quality-gate <project-name>",
                "      Deterministic static checks (no LLM): dev-proxy/backend port",
                "      mismatch, solid-color placeholder images, and placeholder/mock",
                "      text in served data. Exits 1 if any HIGH issue is found.",
                "",
                "  harness-runner blackbox <project-name>",
                "      Adversarial black-box validation: drives the RUNNING app through",
                "      its real public interface with real-world inputs from",
                "      blackbox_cases.json, judges semantic correctness, and detects",
                "      mocked dependencies. Reports only (never edits). Exits 1 on FAIL.",
            ]
        ),
        file=sys.stderr,
    )
    sys.exit(1)


def _parse_loop_flag(args: list[str], default: int = 1) -> tuple[int, list[str]]:
    """Extract --loop N from args; return (n, remaining_args)."""
    n = default
    remaining: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--loop":
            if i + 1 >= len(args):
                print("--loop requires a value", file=sys.stderr)
                sys.exit(1)
            try:
                n = int(args[i + 1])
            except ValueError:
                print(f"invalid --loop value: {args[i + 1]}", file=sys.stderr)
                sys.exit(1)
            i += 2
        elif a.startswith("--loop="):
            try:
                n = int(a.split("=", 1)[1])
            except ValueError:
                print(f"invalid --loop value: {a}", file=sys.stderr)
                sys.exit(1)
            i += 1
        else:
            remaining.append(a)
            i += 1
    if n <= 0:
        print(f"--loop must be positive, got {n}", file=sys.stderr)
        sys.exit(1)
    return n, remaining


def main() -> None:
    args = sys.argv[1:]
    if not args:
        _usage()
    cmd, *rest = args

    if cmd == "init":
        if len(rest) < 1:
            _usage()
        project_name = rest[0]
        brief = " ".join(rest[1:]).strip() if len(rest) > 1 else None
        init(project_name, brief=brief or None)

    elif cmd == "run":
        loop, positional = _parse_loop_flag(rest, default=1)
        if len(positional) < 1:
            _usage()
        run(positional[0], loop=loop)

    elif cmd == "plan":
        if len(rest) != 1:
            _usage()
        plan(rest[0])

    elif cmd == "generate":
        if len(rest) != 1:
            _usage()
        generate(rest[0])

    elif cmd == "generate-loop":
        if len(rest) < 1:
            _usage()
        project_name = rest[0]
        try:
            n = int(rest[1]) if len(rest) > 1 else 5
        except ValueError:
            print(f"invalid shift count: {rest[1]}", file=sys.stderr)
            sys.exit(1)
        if n <= 0:
            print(f"invalid shift count: {n}", file=sys.stderr)
            sys.exit(1)
        project_dir = projects_dir() / project_name
        successes = 0
        for i in range(1, n + 1):
            try:
                assert_under_budget(project_dir)
            except BudgetExceededError as e:
                print(
                    f"\n[loop] budget exceeded — stopping at shift {i}/{n}: {e}",
                    file=sys.stderr,
                )
                break
            passing, total = count_features(project_dir)
            if total > 0 and passing >= total:
                print(
                    f"\n[loop] all {total} features passing — exiting early at shift {i}/{n}",
                    file=sys.stderr,
                )
                break
            print(f"\n========== Shift {i}/{n} ==========\n", file=sys.stderr)
            if generate_with_retry(project_name, generate):
                successes += 1
        print(f"\n[loop] {successes}/{n} shifts succeeded", file=sys.stderr)

    elif cmd == "refine":
        if len(rest) < 2:
            _usage()
        project_name = rest[0]
        requirements = " ".join(rest[1:]).strip()
        refine(project_name, requirements)

    elif cmd == "evaluate":
        if len(rest) != 1:
            _usage()
        evaluate(rest[0])

    elif cmd == "code-review":
        if len(rest) < 1:
            _usage()
        project_name = rest[0]
        feature_ids = rest[1:] if len(rest) > 1 else []
        project_dir = projects_dir() / project_name
        if not project_dir.exists():
            print(f"project not found: {project_dir}", file=sys.stderr)
            sys.exit(1)
        # Load feature descriptions if IDs provided, else auto-detect from recent commits
        feature_descriptions: dict[str, str] = {}
        if feature_ids:
            try:
                import json
                with (project_dir / "feature_list.json").open(encoding="utf-8") as f:
                    features = json.load(f)
                feature_descriptions = {
                    ft["id"]: ft.get("description", "")
                    for ft in features
                    if isinstance(ft, dict) and ft.get("id") in feature_ids
                }
            except Exception:
                pass
        else:
            # Auto-detect last N passing features from git log
            try:
                import json
                import subprocess
                log = subprocess.run(
                    ["git", "log", "--oneline", "-5"],
                    cwd=str(project_dir), capture_output=True, text=True
                )
                # Extract feature IDs from commit messages
                import re
                feature_ids = re.findall(r'\bf(\d{3,4})\b', log.stdout)
                feature_ids = [f"f{n}" for n in sorted(set(feature_ids), key=int)][-5:]
                with (project_dir / "feature_list.json").open(encoding="utf-8") as f:
                    features = json.load(f)
                feature_descriptions = {
                    ft["id"]: ft.get("description", "")
                    for ft in features
                    if isinstance(ft, dict) and ft.get("id") in feature_ids
                }
            except Exception:
                pass

        result = code_review(project_dir, feature_ids or ["recent"], feature_descriptions)
        print(f"\n[code-review] verdict={result.verdict}  issues={len(result.issues)}  cost=${result.cost_usd:.4f}")
        for issue in result.issues:
            print(f"  [{issue.severity.upper()}] {issue.category} | {issue.file}")
            print(f"    {issue.description}")
            print(f"    → {issue.suggestion}")
        if result.verdict in ("warn", "fail"):
            append_code_review_to_progress(project_dir, result, feature_ids)
            print(f"\n[code-review] issues appended to claude-progress.txt")
        if result.verdict == "error":
            print(f"[code-review] error: {result.error}", file=sys.stderr)

    elif cmd == "quality-gate":
        if len(rest) != 1:
            _usage()
        project_dir = projects_dir() / rest[0]
        if not project_dir.exists():
            print(f"project not found: {project_dir}", file=sys.stderr)
            sys.exit(1)
        result = quality_gate(project_dir)
        print(f"\n[quality-gate] verdict={result.verdict}  issues={len(result.issues)}")
        for i in result.issues:
            print(f"  [{i.severity.upper()}] {i.category} @ {i.location}")
            print(f"    {i.description}")
            print(f"    → {i.suggestion}")
        if result.verdict != "pass":
            append_quality_gate_to_progress(project_dir, result)
        if result.fail_count:
            sys.exit(1)

    elif cmd == "blackbox":
        if len(rest) != 1:
            _usage()
        result = blackbox(rest[0])
        print(f"\n[blackbox] verdict={result.verdict}  cases={len(result.cases)}"
              f"  mock_detected={result.mock_detected}")
        for c in result.cases:
            print(f"  [{c.result.upper()}] {c.id}")
            if c.result != "pass":
                print(f"    expected: {c.expected}")
                print(f"    actual:   {c.actual[:200]}")
        if result.mock_detected:
            print(f"  ⚠️ MOCK: {result.mock_detail}")
        if result.error:
            print(f"[blackbox] error: {result.error}", file=sys.stderr)
        if result.verdict == "fail":
            sys.exit(1)

    else:
        _usage()


if __name__ == "__main__":
    main()
