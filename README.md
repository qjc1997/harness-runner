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

Each shift starts with no memory of the previous one — like an engineer arriving for a new shift,
reconstructing context from disk.

## Requirements

- [Bun](https://bun.sh/) ≥ 1.1
- [Claude Code CLI](https://docs.claude.com/en/docs/claude-code/) on `PATH`
- `git`

## Install

```bash
bun install
```

## Use

```bash
# 1. Plan a project from a brief
bun src/cli.ts plan mini-hex "A minimal Hex-like data notebook. Cells for Python (shared kernel), SQL (DuckDB), and AI generation that turns natural language into code. React+Vite frontend, FastAPI backend."

# 2. Run one generator shift
bun src/cli.ts generate mini-hex

# 3. Run N shifts back-to-back
bun src/cli.ts generate-loop mini-hex 5
```

Built projects live under `projects/<name>/` (git-ignored from the harness repo;
each project has its own internal git history that survives across shifts).

## Layout

```
prompts/
  planner.md          # planner system prompt
  generator.md        # generator system prompt
src/
  cli.ts              # argv parsing
  claude.ts           # claude CLI subprocess wrapper (stream-json events)
  paths.ts            # repo-relative paths + event formatter
  roles/
    planner.ts
    generator.ts
projects/             # gitignored — runtime output of builds
```

## Roadmap

- **Step 1 (this repo, today)**: Planner + Generator, manual review between shifts.
- **Step 2**: Add Evaluator agent with Playwright MCP. Sprint-contract handoffs.
- **Step 3**: Context-anxiety / shift-timeout detection and reset semantics.
- **Step 4**: Integrate as an `OrchestratorAgent` into [NeoClaw](https://github.com/qiaojiacheng/neoclaw) so builds can be triggered and monitored from chat.

## License

MIT
