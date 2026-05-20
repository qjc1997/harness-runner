# harness-runner

An autonomous multi-agent coding harness — inspired by Anthropic's research on
[harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
and [effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

This is **Step 1**: the minimum viable harness. Two roles, no Evaluator.
The goal of Step 1 is to learn whether a Planner-produced feature list is granular
enough for unsupervised Generator shifts to make stable progress.

## Why I built this

In late 2025 Anthropic published two articles on long-running coding agents — patterns for orchestrating LLMs across many context-windows to build complete applications. Their reference implementation ([`autonomous-coding`](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding)) is Python tied to the Claude Agent SDK, distributed under "Internal Anthropic use" license.

This project is an independent take on the same patterns, with a different set of design decisions deliberately chosen for adversarial conditions the reference doesn't address:

- **Pluggable LLM backend** — abstracted behind a `Backend` protocol so the same orchestration runs against `claude_cli`, direct Anthropic Messages API, OpenAI Codex CLI, or Gemini CLI without touching role-code. Particularly relevant given Anthropic's 2026-06-15 programmatic-billing change.
- **Production-grade subprocess handling** — watchdog timeouts (total + idle), background stderr draining to defeat the 64KB pipe-buffer deadlock, structured retry-with-backoff for transient failures, soft budget cap.
- **Schema-level validation** of Planner output before commit, with invariants on category, step distribution, and smoke-feature coverage. Bad plans cost one Planner shift, not twenty Generator shifts.
- **Observable shift accounting** — per-project `.harness_history.jsonl` records cost / duration / tokens / passing-deltas per shift, machine-queryable without parsing markdown.

The project is also the platform on which I'm building a separate application (a data-notebook clone). The recursion is intentional: the harness is the tool, the harness building an app is the validation, and the resulting app is the actual goal.

For the design rationale and trade-offs behind each significant decision, see [`docs/decisions.md`](./docs/decisions.md).

## Problem this addresses

LLMs write code competently for single tasks, but coordinating dozens of features across many sessions on a real application is hard. Three forces work against unattended autonomous work:

- **Context windows are finite** — even with 1M context, a multi-day build won't fit in one session.
- **Self-evaluation is biased** — agents trained to be helpful consistently mark their own output as "done" prematurely.
- **No persistent memory** — each session starts cold; state has to survive on disk.

A *harness* is the orchestration layer that addresses these: structured state-on-disk (feature list, progress log, git history), multi-shift workflow (each shift = fresh context that reconstructs from disk), schema-level checks (the agent's output is validated before it lands), and explicit budget / timeout guardrails for unattended runs.

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
   - follows a mandatory startup protocol (read progress, read features, read `app_spec.md`, `git log`, `init.sh`, boot check, **regression check on smoke features**)
   - declares a "batch" of 1+ related features (typically 1–5) and verifies each end-to-end
   - flips each verified feature's `passes` flag and commits **atomically per feature** (one commit per feature, never squashed)
   - appends a shift-log entry to `claude-progress.txt` recording the batch and any non-obvious decisions

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
docs/
  decisions.md               # ADR log: rationale + trade-offs for major design choices
examples/                    # placeholder for sample runs (see examples/README.md)
src/
  harness_runner/
    __init__.py
    __main__.py              # `python -m harness_runner` entry
    cli.py                   # argv parsing
    paths.py                 # repo-relative paths + count_features + format_progress
    scaffold.py              # `init` action (no LLM involved)
    run.py                   # `run` action: auto-detect planner vs generator
    backends/                # pluggable LLM backends
      __init__.py            #   get_backend() factory + HARNESS_BACKEND env var
      base.py                #   Backend protocol + RunResult dataclass
      claude_cli.py          #   default: `claude -p` subprocess + stream-json parser
    roles/
      __init__.py
      planner.py
      generator.py
projects/                    # gitignored — runtime output of builds
```

## Backend selection

The LLM call site is abstracted behind a `Backend` protocol so the harness can
swap underlying integrations without touching role/orchestration code.

Available backends:
- **`claude_cli`** (default) — `claude -p` subprocess. Uses your local Claude
  Code CLI for auth and model resolution.

Planned (see [BACKLOG](./BACKLOG.md)):
- `anthropic_api` — direct Anthropic Messages API, bypassing the Claude Code
  subscription credit pool
- `codex_cli` — OpenAI Codex CLI
- `gemini_cli` — Google Gemini CLI

Select via environment variable:

```bash
HARNESS_BACKEND=claude_cli harness-runner run mini-hex
```

**Heads-up on costs (effective 2026-06-15)**: Anthropic moves `claude -p`,
Claude Agent SDK, and related programmatic usage onto a separate monthly credit
pool, metered at full API rates. Pro = $20, Max 5x = $100, Max 20x = $200,
Team Premium = $100 per seat. Credit must be claimed (Anthropic emails on
June 8). No rollover. For heavy usage, the `anthropic_api` backend (when
implemented) will let you bypass the subscription entirely and pay API rates
pay-as-you-go.

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
