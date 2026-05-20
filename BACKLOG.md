# BACKLOG

Deferred items, in rough priority order. Move out of this file into an actual implementation when picked up.

## Backend: `anthropic_api` — direct Messages API

**Why**: From 2026-06-15, `claude -p` (the only backend we currently have) drains a separate, capped monthly credit pool on Anthropic subscriptions, metered at full API rates. For heavy or production usage the subscription path stops making sense — at that point we want a backend that talks directly to the Anthropic Messages API with our own API key, bypassing the subscription entirely (same rates, no $20 minimum, no monthly cap to worry about).

**Implementation outline** (`src/harness_runner/backends/anthropic_api.py`):

- Use the official `anthropic` Python SDK
- Read `ANTHROPIC_API_KEY` from env
- Implement a minimal **tool-use loop** that matches what Claude Code provides natively:
  - `Read`, `Write`, `Edit`, `Glob`, `Grep` — file ops scoped to `cwd`
  - `Bash` — subprocess with timeout
  - `AskUserQuestion` — not needed for autonomous mode
- Stream `message_start` / `content_block_delta` / `tool_use` events through the same `on_event` channel as `claude_cli`
- Return `RunResult` with `cost_usd` computed from input/output token counts × Sonnet rate
- Register in `backends/__init__.py:_REGISTRY` as `"anthropic_api"`

**Estimate**: 500–800 lines including tool implementations. Half a day of focused work. Most of the complexity is the Read/Write/Edit/Bash tool implementations that Claude Code gives us for free — borrow design from any open-source agent harness (smolagents, OpenHands' codeact tool, etc.) but write our own.

**When to do it**: trigger is "the first month our `claude_cli` usage costs more than the Anthropic API equivalent would have". Until then, `claude_cli` is operationally simpler.

## Backend: `codex_cli` — hedge against Anthropic policy shifts

Lightweight wrapper around OpenAI's Codex CLI (open-source). Useful as a sanity-check that our harness pattern isn't Claude-specific. Likely 100–200 lines.

## MCP — Playwright

**Status**: deferred until Step 2 (Evaluator).

**Why**: Anthropic's "Harness Design for Long-Running Apps" article uses a Playwright-backed Evaluator as the core mechanism for catching the things Generators rationalize past. Step 1 has no Evaluator, so Playwright would be unused weight. When Step 2 starts, drop a `mcp.template.json` at repo root with:

```jsonc
{
  "mcpServers": {
    "playwright": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--headless"]
    }
  }
}
```

…and have the `scaffold.init()` flow copy it into each new project directory (one `shutil.copyfile` alongside the existing `app_spec.md` copy). Generator and Evaluator both pick it up automatically because Claude Code reads `.mcp.json` from cwd.

**Audit done 2026-05-19**: All other MCP servers in the local NeoClaw config (`apifox`, `figma`, `neoclaw-memory`, `neoclaw-feishu`) and the Claude.ai-hosted ones (Atlassian / Gmail / Linear / Notion / Slack / ODE / Autodesk Help) are not relevant to this project — they exist for the bot's other workflows.

## Generator UI-bug catalog (Step 2)

When the Generator prompt gains browser-automation tooling, expand its verification section with an explicit UI-bug catalog (adapted from Anthropic's `coding_prompt.md`):

- white-on-white text or insufficient contrast
- random / garbled characters in rendered output
- incorrect timestamps or stale data
- layout overflow, cut-off elements, content escaping container
- buttons spaced too close to be reliably clickable
- missing hover / focus / disabled / loading / empty states
- console errors or warnings visible in DevTools
- broken images / missing favicons / 404 on assets

**Why**: LLM self-evaluation defaults to charitable. A concrete checklist of bug patterns forces specific observation rather than "looks fine to me".

## Stronger "catastrophic" framing (prompt polish)

Upgrade rule sections in `planner.md` / `generator.md` from "NEVER ..." to "IT IS CATASTROPHIC TO ..." with the consequence stated. Anthropic's reference prompts use this pattern consistently; LLMs weight extreme phrasing higher. Defer until Step 1 produces empirical evidence that "NEVER" is being violated — if not, leave alone.

## Override Claude Code's default system prompt

Currently we use `--append-system-prompt` which leaves Claude Code's default system prompt (long, generic dev-CLI guidance) in place. Switch to also passing `--system-prompt "You are an expert full-stack developer building a production-quality web application."` to replace the default with a minimal role line, then append our long role description.

Saves tokens per shift and reduces conflicting guidance between Claude Code's defaults and our prompts.

Pre-check: confirm `claude -p` accepts both `--system-prompt` and `--append-system-prompt` simultaneously.

## Bash command allowlist + security hook (Step 2 unattended runs)

When `generate-loop` runs unattended for hours, `--dangerously-skip-permissions` becomes risky. Add a pre-tool-use security hook (`src/harness_runner/security.py`) that intercepts Bash commands and validates them against an allowlist. Adapt the design (not the code) from Anthropic's `security.py`:

- Use `shlex.split` (not regex) to parse compound commands
- Split on `&&`, `||`, `;` and validate each segment
- Allowlist shape: `ls cat head tail wc grep pwd cp mkdir chmod npm node python pip bun git ps lsof sleep pkill ./init.sh ./stop.sh`
- Extra validation for sensitive commands:
  - `pkill` — only allow killing dev process names (`node`, `npm`, `npx`, `vite`, `uvicorn`, `python`)
  - `chmod` — only `+x` variants (no recursive, no octal modes)
  - `init.sh` — only allow `./init.sh` / `*/init.sh` paths
- Hook into Claude SDK's `PreToolUse` event (or equivalent for our CLI subprocess flow — may require switching from `claude -p` to the Claude Agent SDK Python bindings)

## Project-scoped `.claude_settings.json` (Step 2)

Write a per-project settings file at `projects/<name>/.claude_settings.json` declaring:

```json
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Read(./**)", "Write(./**)", "Edit(./**)",
      "Glob(./**)", "Grep(./**)", "Bash(*)"
    ]
  }
}
```

Restricts file ops to the project tree. Pairs with the bash allowlist above as defense-in-depth. Investigate whether `claude -p` honors this file format the same way the Claude Agent SDK does.

## Error retry in `generate-loop` / `run --loop`

Currently both abort on the first exception (RuntimeError, claude non-zero exit, etc.). For unattended runs:

- Catch exceptions per-shift
- Log to stderr + append to `claude-progress.txt`
- Sleep 3–5s, retry up to N times (default 3)
- Hard-fail only after N consecutive failures

Anthropic's reference does this around `run_agent_session()`.

## Generator one-shift boundary (long-term research)

As model capability grows, the binding constraint on shift size shifts from "what fits in context" to "what Generator can mentally hold without losing track". The hardware ceiling (context window, now 1M for Opus 4.7) becomes self-set rather than externally enforced. Anthropic's original "exactly one feature per shift" prompt was written for Sonnet 4.5 limits; with Opus 4.7 we've already loosened it to "1+ related features per shift, atomic per-feature commits, scope declaration in shift log" (2026-05-20). But the **deeper question is unsolved**: how does Generator reliably know the upper bound of what it can complete in one session, when the constraint is no longer a hard wall?

Open sub-problems to explore:

- **Pre-shift scope contract**: Generator declares "I'll do f003, f004, f005" at shift start (already in current prompt as `Batch: ...` log line). Should we machine-parse that and treat exceeding it as a logged deviation event?
- **Mid-shift retrospection**: at turn 30 of a long shift, can Generator self-evaluate "am I still on track for my declared batch, or have I lost the thread"? Anthropic's "context anxiety" research suggests models can detect this.
- **Post-shift validation**: automated diff-vs-claim check — does `git log` since shift start show only the features declared in the batch, or did Generator silently expand scope?
- **Thinking budget**: use Opus's interleaved thinking deliberately at shift start to plan batch scope BEFORE any file write. May reduce mid-shift overruns.
- **Model self-introspection**: ask "given this feature list and current state, what's a comfortable batch for ONE shift?" as a separate planning call before code generation. Could even be a different (cheaper) model.

Signals from `.harness_history.jsonl` we can already use now to spot trouble without new code:

- per-shift cost variance creeping up (~$0.35 → ~$2+ would be a yellow flag)
- regression check trip rate (most shifts should pass smoke; frequent fails = batches contain self-contradictions)
- `passing_after - passing_before` ≫ size of declared batch in shift log = silent scope creep
- input_tokens approaching 1M per shift = context ceiling getting close, time to shrink batch

Track these informally first; formalize into prompt rules or auto-checks once we have empirical data from a few real runs.

## Open spec questions (carry into Step 2 planning)

- **Trigger mode**: chat-triggered single build run vs. always-on background harness. Affects how NeoClaw's chat-side console hooks in.
- **Target users**: solo (the bot owner) vs. multi-user. Affects whether the built app needs auth/isolation.
- **Per-build budget cap**: Anthropic's reference runs are 6h / $200. We need a sane default and a hard ceiling.
- **Generator self-testing**: Anthropic's two articles disagree on whether the Generator should self-test with a browser. Step 1 says no (just curl + pytest). Step 2 with Evaluator can revisit.
