# harness-runner

An autonomous multi-agent coding harness — inspired by Anthropic's research on
[harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
and [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

This is **Step 1**: the minimum viable harness. Two roles, no Evaluator.
The goal of Step 1 is to learn whether a Planner-produced feature list is granular
enough for unsupervised Generator shifts to make stable progress.

## How it works

The flow has three steps and two roles:

1. **`init` — scaffold a project** (no Claude involved)
   Creates `projects/<name>/app_spec.md` from a template, plus a git repo with an initial commit. You then edit the spec to describe what you want built (or pass a one-line brief on the CLI to skip the template).

2. **Planner** (one Claude shift)
   Reads `app_spec.md` and produces three artifacts:
   - `feature_list.json` — flat array of E2E-testable features, each with `passes: false`
   - `init.sh` — dev-environment bootstrap (start servers, write `.dev-pids`)
   - `claude-progress.txt` — running progress log

3. **Generator** (many Claude shifts)
   A fresh Claude session per invocation that:
   - follows a mandatory startup protocol (read progress, read features, read `app_spec.md`, `git log`, `init.sh`, boot check, **regression check on already-passing features**)
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

### Quick mode

```bash
harness-runner init mini-hex "A minimal Hex-like data notebook. Cells for Python (shared Jupyter kernel), SQL (DuckDB), and AI generation. React+Vite frontend, FastAPI backend."
harness-runner run mini-hex --loop 5
```

`run` auto-detects whether the project has been planned yet:
- no `feature_list.json` → runs Planner first, then a Generator shift (1 by default; more with `--loop N`)
- `feature_list.json` exists → runs N Generator shifts

### Step-by-step (more control)

```bash
# Scaffold and edit the spec by hand
harness-runner init mini-hex
$EDITOR projects/mini-hex/app_spec.md

# Plan and generate explicitly
harness-runner run mini-hex            # plan + 1 shift
harness-runner run mini-hex --loop 3   # 3 more shifts

# Or use the lower-level commands directly
harness-runner plan mini-hex
harness-runner generate mini-hex
harness-runner generate-loop mini-hex 5
```

Built projects live under `projects/<name>/` (git-ignored from the harness repo;
each project has its own internal git history that survives across shifts).

## Layout

```
prompts/
  planner.md                 # planner system prompt
  generator.md               # generator system prompt
templates/
  app_spec.template.md       # copied into projects/<name>/app_spec.md by `init`
src/
  harness_runner/
    __init__.py
    __main__.py              # `python -m harness_runner` entry
    cli.py                   # argv parsing
    claude.py                # claude CLI subprocess wrapper (stream-json events)
    paths.py                 # repo-relative paths + event formatter + count_features
    scaffold.py              # `init` action (no Claude involved)
    run.py                   # `run` action: auto-detect planner vs generator
    roles/
      __init__.py
      planner.py
      generator.py
projects/                    # gitignored — runtime output of builds
```

## Stable external interface

This project is meant to be driven from the outside (e.g. by NeoClaw or any other observer). The contract is intentionally minimal and language-agnostic:

- **Trigger**: shell out to `harness-runner init ...` / `harness-runner run ...`
- **Observe progress**: read `projects/<name>/claude-progress.txt` and `git -C projects/<name> log`
- **Inspect features**: read `projects/<name>/feature_list.json` (each shift prints `[progress] X/N features passing (Y%)` to stderr)

No shared library / no in-process integration is required.

## Roadmap

- **Step 1 (this repo, today)**: Planner + Generator, manual review between shifts.
- **Step 2**: Add Evaluator agent with [Playwright MCP](./BACKLOG.md). Sprint-contract handoffs.
- **Step 3**: Context-anxiety / shift-timeout detection and reset semantics.

## License

MIT
