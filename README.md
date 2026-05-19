# harness-runner

An autonomous multi-agent coding harness — inspired by Anthropic's research on
[harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
and [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

This is **Step 1**: the minimum viable harness. Two roles, no Evaluator.
The goal of Step 1 is to learn whether a Planner-produced feature list is granular
enough for unsupervised Generator shifts to make stable progress.

## How it works

Given a one-paragraph product brief, the harness runs two roles in turn:

1. **Planner** (one shot)
   Produces three artifacts in the project directory:
   - `feature_list.json` — flat array of E2E-testable features, each with `passes: false`
   - `init.sh` — dev-environment bootstrap (start servers, write `.dev-pids`)
   - `claude-progress.txt` — running progress log
   Initializes a git repo and makes the first commit.

2. **Generator** (one shift per invocation)
   A fresh Claude session that:
   - follows a mandatory startup protocol (`pwd`, read progress, read features, `git log`, `init.sh`, sanity-check the app)
   - picks **exactly one** feature with `passes:false`
   - implements, verifies, flips that one feature's `passes` flag, commits
   - appends a 3–5 line summary to `claude-progress.txt`

Each shift starts with no memory of the previous one — like an engineer arriving for a new shift, reconstructing context from disk.

## Requirements

- Python ≥ 3.9 (system Python on recent macOS works)
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code/) on `PATH` (`claude` command)
- `git`

## Install

```bash
# Editable install from the repo root, in a venv if you prefer
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip            # macOS-bundled pip 21.x can't do PEP 660 editable installs
pip install -e .
```

After install, the `harness-runner` command is on your `PATH`.
You can also run without install: `python -m harness_runner ...` from the repo root.

## Use

```bash
# 1. Plan a project from a brief
harness-runner plan mini-hex "A minimal Hex-like data notebook. Cells for Python (shared kernel), SQL (DuckDB), and AI generation that turns natural language into code. React+Vite frontend, FastAPI backend."

# 2. Run one generator shift
harness-runner generate mini-hex

# 3. Run N shifts back-to-back
harness-runner generate-loop mini-hex 5
```

Built projects live under `projects/<name>/` (git-ignored from the harness repo;
each project has its own internal git history that survives across shifts).

## Layout

```
prompts/
  planner.md                 # planner system prompt
  generator.md               # generator system prompt
src/
  harness_runner/
    __init__.py
    __main__.py              # `python -m harness_runner` entry
    cli.py                   # argv parsing
    claude.py                # claude CLI subprocess wrapper (stream-json events)
    paths.py                 # repo-relative paths + event formatter
    roles/
      __init__.py
      planner.py
      generator.py
projects/                    # gitignored — runtime output of builds
```

## Stable external interface

This project is meant to be driven from the outside (e.g. by NeoClaw or any other observer). The contract is intentionally minimal and language-agnostic:

- **Trigger**: shell out to `harness-runner plan ...` / `harness-runner generate ...`
- **Observe progress**: read `projects/<name>/claude-progress.txt` and `git -C projects/<name> log`
- **Inspect features**: read `projects/<name>/feature_list.json`

No shared library / no in-process integration is required.

## Roadmap

- **Step 1 (this repo, today)**: Planner + Generator, manual review between shifts.
- **Step 2**: Add Evaluator agent with [Playwright MCP](./BACKLOG.md). Sprint-contract handoffs.
- **Step 3**: Context-anxiety / shift-timeout detection and reset semantics.

## License

MIT
