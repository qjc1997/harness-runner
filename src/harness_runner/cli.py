import sys

from .budget import BudgetExceededError, assert_under_budget
from .paths import projects_dir
from .retry import generate_with_retry
from .roles.generator import generate
from .roles.planner import plan
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
            print(f"\n========== Shift {i}/{n} ==========\n", file=sys.stderr)
            if generate_with_retry(project_name, generate):
                successes += 1
        print(f"\n[loop] {successes}/{n} shifts succeeded", file=sys.stderr)

    else:
        _usage()


if __name__ == "__main__":
    main()
