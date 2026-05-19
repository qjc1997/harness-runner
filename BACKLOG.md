# BACKLOG

Deferred items, in rough priority order. Move out of this file into an actual implementation when picked up.

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

…and have the Planner role copy it into each new project directory at init (one `shutil.copyfile` in `src/harness_runner/roles/planner.py` after `mkdir`). Generator and Evaluator both pick it up automatically because Claude Code reads `.mcp.json` from cwd.

**Audit done 2026-05-19**: All other MCP servers in the local NeoClaw config (`apifox`, `figma`, `neoclaw-memory`, `neoclaw-feishu`) and the Claude.ai-hosted ones (Atlassian / Gmail / Linear / Notion / Slack / ODE / Autodesk Help) are not relevant to this project — they exist for the bot's other workflows.

## Open spec questions (carry into Step 2 planning)

- **Trigger mode**: chat-triggered single build run vs. always-on background harness. Affects how Step 4 plugs into NeoClaw.
- **Target users**: solo (the bot owner) vs. multi-user. Affects whether the built app needs auth/isolation.
- **Per-build budget cap**: Anthropic's reference runs are 6h / $200. We need a sane default and a hard ceiling.
- **Generator self-testing**: Anthropic's two articles disagree on whether the Generator should self-test with a browser. Step 1 says no (just curl + pytest). Step 2 with Evaluator can revisit.
