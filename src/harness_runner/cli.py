import sys

from .roles.generator import generate
from .roles.planner import plan


def _usage() -> None:
    print(
        "\n".join(
            [
                "usage:",
                '  harness-runner plan <project-name> "<brief>"',
                "  harness-runner generate <project-name>",
                "  harness-runner generate-loop <project-name> [n=5]",
            ]
        ),
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        _usage()
    cmd, *rest = args

    if cmd == "plan":
        if len(rest) < 2:
            _usage()
        project_name = rest[0]
        brief = " ".join(rest[1:]).strip()
        if not project_name or not brief:
            _usage()
        plan(project_name, brief)

    elif cmd == "generate":
        if len(rest) < 1:
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
        for i in range(1, n + 1):
            print(f"\n========== Shift {i}/{n} ==========\n", file=sys.stderr)
            generate(project_name)

    else:
        _usage()


if __name__ == "__main__":
    main()
