# BACKLOG

Deferred items, in rough priority order. Move out of this file into an actual implementation when picked up.

## DONE 2026-06-12 — Quality gate: config-drift + placeholder-data detection

Implemented `src/harness_runner/quality_gate.py` — deterministic static checks (no LLM, no servers) that run after every Generator shift and as a standalone `harness-runner quality-gate <project>` command:

- **config-drift (HIGH)**: frontend dev-proxy `target` port vs. backend listen port (`BACKEND_PORT`/uvicorn `--port`). Catches the rome-guide 502 (proxy→8001, backend→8000) that curl-on-backend missed.
- **placeholder-image (HIGH/MED)**: solid-color or <5KB image files in served data dirs (PIL color-count, file-size fallback). Catches the seed_full.py colored-square fallback.
- **placeholder-text (MED)**: lorem-ipsum / mock / TODO / example.com in served JSON.

Findings append to `claude-progress.txt` (same self-repair loop as the code reviewer) and are recorded in the shift note. Tests: `tests/test_quality_gate.py` (8 cases, stdlib unittest). Prompt updates: evaluator.md now mandates testing through the **frontend origin** (backend-direct curl = INCONCLUSIVE, never a pass) and treats empty/placeholder content as a failed step; code_reviewer.md gained categories 7 (degraded-output fallbacks) and 8 (cross-file config consistency).

## TODO — Playwright smoke-regression gate (heavier follow-on)

The quality gate above is static. The complementary dynamic gate: after each shift, re-run the `is_smoke: true` browser flows through the **frontend origin** and flip any that now 502/empty back to `passes: false` automatically — without waiting for a manually-triggered `evaluate` shift. This is what would have caught the proxy regression *at the shift that introduced it*. Needs: servers running + Playwright MCP, a cheap "load + non-empty + no console error" probe per smoke feature, and wiring into `generate-loop` (e.g. every N shifts or when shared config files changed in the diff). Evaluator prompt already specifies the frontend-origin rule; this automates the cadence.

## Script execution self-repair loop

**Why**: rome-guide data pipeline (2026-06-08) exposed that Generator-produced data scripts fail in ways that need human diagnosis — wrong QIDs (hallucinated), Wikimedia UA blocks, 429 rate limits. The generate-loop has a refine cycle for feature code, but there's no equivalent for standalone scripts. Each failure required multiple manual kill-restart cycles.

**What**: When a harness-managed script exits non-zero OR its stdout/stderr matches known error patterns, automatically call a lightweight Script Repair agent that:
1. Reads the script source + the last N lines of output
2. Identifies the failure category: wrong ID, blocked UA, rate limit, missing fallback, etc.
3. Patches the script in-place and re-runs (up to 3 attempts)
4. If it can't repair, escalates with a structured error report

**Error patterns to detect** (from rome-guide incident):
- `403 Forbidden` on image download → switch `requests.get` to `subprocess.run(['curl', ...])`
- QID resolves to wrong entity (label mismatch) → replace hardcoded ID with `wbsearchentities` name lookup
- `429 Too Many Requests` → add `Retry-After` backoff, increase inter-request delay
- `0 items found` for a venue → try alternative SPARQL property (P276 → P195)

**Dry-run validation gate** (prerequisite): before running a script at full scale, always run with `--limit 3` or `--dry-run` and check that at least 1 item succeeded end-to-end. Abort and repair if 0 succeed.

**When to do it**: next time a data pipeline or utility script is generated for a project (rome-guide or any new harness project).

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

---

## Project: rome-guide — Offline AI Museum Guide for Italy Trip

**Target date**: July 2026 (user going to Rome/Vatican next month)

### Overview
Offline AI guide for Rome and Vatican museums. Uses CLIP image search + VLM (Qwen2-VL) for artwork identification and Q&A. Designed for no-internet / poor-signal environments inside museums.

### Two operation modes (both required)
1. **Laptop mode**: Laptop in backpack as server, iPhone 17 PM as client via WiFi hotspot. Best quality, most venues.
2. **Phone mode (Xiaomi 11)**: For venues that ban backpacks (e.g., Borghese Gallery). Xiaomi 11 as server (Snap 888, 8-12GB RAM), iPhone 17 PM as client via hotspot.

### Scope
- **Sites**: Vatican Museums (Sistine Chapel, Raphael Rooms, antiquities), St. Peter's Basilica, Borghese Gallery, Colosseum, Roman Forum, Pantheon, Castel Sant'Angelo
- **Knowledge base**: ~500-800 artworks, Wikipedia + museum open data, pre-downloaded images
- **Test set**: ~100-200 user photos + supplemental internet photos of same venues

### Key components for harness-runner to build
1. Ollama setup + model download in init.sh (Qwen2-VL 7B for laptop, 2B for phone mode)
2. CLIP image indexing pipeline (build_index.py — one-time offline step)
3. FastAPI backend: /search/image (CLIP match) + /chat (VLM Q&A with artwork context)
4. Mobile-optimized React frontend: camera capture, result display, follow-up chat
5. Knowledge base: artworks.json + images/ + ChromaDB/SQLite-vec embeddings
6. Accuracy Evaluator: use test photo set, measure Top-1 / Top-3 recognition accuracy

### Novel challenges vs mini-hex
- External dependency management (Ollama install + model pull in init.sh)
- ML data pipeline (CLIP embedding build step, not just CRUD)
- Dual deployment target (laptop vs phone server)
- Quantitative accuracy testing with real photos as ground truth

### When to start
After Italy trip planning is confirmed. Knowledge base prep (scraping + cleaning) should start ~2 weeks before departure.
