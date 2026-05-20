# harness-runner — Design Decisions

Lightweight ADR (Architecture Decision Record) log for the harness-runner project.

Format per decision: **Status / Context / Decision / Consequences**. Decisions are listed in roughly the order they were made.

Repo: <https://github.com/qjc1997/harness-runner>
Last updated: 2026-05-20

---

## ADR 001 — Implementation language: Python (after starting in TypeScript)

**Status**: Accepted. Switched 2026-05-19, before any application code existed.

**Context**: The project lives next to NeoClaw (TypeScript / Bun). Initial implementation defaulted to TS to match. After completing the scaffold (~400 lines), reconsidered: Anthropic's official `autonomous-coding` quickstart is Python, the broader LLM / agent ecosystem (LangChain, AutoGen, MetaGPT, OpenHands, Claude Agent SDK) is Python-first, and the owner has stronger Python familiarity than TypeScript.

**Decision**: Rewrite as a Python package (`src/harness_runner/`), installable via `pip install -e .`, pure stdlib (no external dependencies), Python ≥ 3.9.

**Consequences**:
- ✅ Aligned with where the agent-tooling ecosystem actually lives. Future ports of Anthropic / community patterns translate directly.
- ✅ Single-language project (no JS/Python split per role).
- ✅ Owner's primary language.
- ❌ Cannot share code with NeoClaw via import. Integration is via shell-out + file reads only (see ADR 015).
- ❌ Lost the ~400 lines of TS work. Cost was bearable because the project was 1 day in, not weeks.

**Lesson recorded**: don't default-pick a language to match an adjacent project before establishing whether code-level sharing is actually planned. In this case the integration was always intended to be external (files + CLI), so the homogeneity argument never had weight.

---

## ADR 002 — LLM integration: `claude` CLI subprocess (not Claude Agent SDK)

**Status**: Accepted.

**Context**: Two ways to talk to Claude from Python: (a) Claude Agent SDK (Python library, in-process, tool hooks, structured messages); (b) shell out to `claude -p` and parse stream-json line-by-line.

**Decision**: Subprocess. Each shift = one `Popen(["claude", "-p", "--output-format", "stream-json", ...])` call.

**Consequences**:
- ✅ Zero dependency on a specific SDK version. SDK updates can't break us.
- ✅ Same auth path the user already uses for interactive `claude` (no separate API key handling for v0).
- ✅ Trivially swappable: spawning a different CLI (codex, gemini-cli) is a parallel implementation of the same `Backend` protocol.
- ❌ Lose Agent SDK conveniences: `@tool` decorator, hooks (PreToolUse), in-process MCP, structured tool-use callbacks.
- ❌ stream-json parsing is hand-rolled; have to handle its quirks (rate_limit_event lines, malformed JSON, etc.).
- ❌ Subprocess-specific failure modes (pipe deadlock, zombie processes) we have to engineer around — see ADR 007, 008.

---

## ADR 003 — Pluggable LLM backend abstraction

**Status**: Accepted. Implemented 2026-05-19.

**Context**: Anthropic announced (2026-06-15 effective) that `claude -p` would draw from a separate, capped monthly programmatic-usage credit pool. For heavy / long-running use this changes economics — direct API or a different vendor (Codex, Gemini) may become preferable. Hard-coding `claude_cli` would force a code rewrite to switch.

**Decision**: Define a `Backend` protocol (`backends/base.py`) with a single method `run(cwd, prompt, system_prompt_append, model, on_event) -> RunResult`. Concrete backends implement it. Selected by `HARNESS_BACKEND` env var or `get_backend(name)`. Currently: `claude_cli`. Planned: `anthropic_api`, `codex_cli`.

**Consequences**:
- ✅ Switching the LLM provider requires no role / orchestration code changes.
- ✅ Forces a clean interface — roles can't accidentally couple to claude-specific behavior.
- ✅ Easy to write a fake backend for testing without spending API credits.
- ❌ ~80 lines of protocol code overhead for a single backend that exists today.
- ❌ Some claude-CLI-specific things (stream-json event format) leak into the protocol via opaque `on_event` callback.

**Why this matters more than YAGNI**: this is a known coming event (June 15) with known impact. The abstraction has a deadline, not a hypothetical use case.

---

## ADR 004 — File-based state, no shared library, no DB

**Status**: Accepted. Inherited from Anthropic's autonomous-coding pattern.

**Context**: How does cross-shift state survive? Options: in-process state (lost on subprocess death), DB (overkill for single-machine), git (manual), or JSON files in the project directory.

**Decision**: All persistent state is files in `projects/<name>/`:
- `feature_list.json` — work to do, source of truth for what's done
- `claude-progress.txt` — human-readable shift log (markdown)
- `.harness_history.jsonl` — per-shift structured outcomes
- `.git/` — code history
- `init.sh`, `app_spec.md` — config / spec

No shared in-process state between roles; no DB; no IPC beyond filesystem.

**Consequences**:
- ✅ Survives crashes, interrupts, restarts. No "in-flight transaction" to recover.
- ✅ Every component (Planner, Generator, future Evaluator, NeoClaw observer, manual SSH inspection) reads the same files. One source of truth.
- ✅ Trivially debuggable — `tail -f` and `cat` reveal everything.
- ✅ Language-agnostic. NeoClaw (TS) can observe a Python project without bindings.
- ❌ Race conditions if multiple writers existed (we have one writer at a time by design, but the constraint is implicit).
- ❌ JSON parsing on every shift's startup (cheap, but not free).

---

## ADR 005 — Single-shot Claude session per shift (no `--resume`)

**Status**: Accepted.

**Context**: NeoClaw uses `claude --resume <sessionId>` to keep a long-running conversation per chat. We could do the same for harness-runner — Generator shifts resume the same session, preserving Claude's "memory" across shifts. Or each shift starts fresh.

**Decision**: Fresh session per shift. No `--resume`. Each Generator shift is a new Claude session that reconstructs context from disk (Planner output + previous shift logs).

**Consequences**:
- ✅ Matches Anthropic's "engineers working in shifts" mental model literally. Each shift = a new agent picking up cold.
- ✅ Forces durable state to be on disk, where humans and other tooling can read it.
- ✅ No "session corruption" — a bad shift can't poison future shifts.
- ✅ Each shift's reasoning is auditable in isolation (no opaque session history).
- ❌ Pay the input-token cost of re-reading feature_list / progress / app_spec every shift. Prompt caching mitigates this (cache-read counts as input at 10% billing).
- ❌ Generator can't "remember why I made this choice three shifts ago" — must re-derive from progress notes the previous shift left.

---

## ADR 006 — stream-json parsing (not synchronous `--output-format json`)

**Status**: Accepted.

**Context**: `claude -p` supports two output modes: synchronous JSON (single object at end) or `--output-format stream-json` (one JSON event per line, in real time).

**Decision**: stream-json + `--verbose`. Parse each line, fire `on_event` callback per event. Roles render tool calls / text / thinking to stderr live.

**Consequences**:
- ✅ Real-time observability: a 15-minute shift shows progress every few seconds, not a black box until completion.
- ✅ Watchdog can update "last activity" timestamp on every stdout line (see ADR 008).
- ❌ Hand-rolled line-by-line JSON parse (~30 lines).
- ❌ Have to tolerate weird event types (`rate_limit_event`, unannounced events) — defensive `try / except`.

---

## ADR 007 — stderr drained in a background thread (not merged into stdout)

**Status**: Accepted. Bug found during code review (2026-05-19), fixed before unattended long runs.

**Context**: The naïve subprocess pattern `Popen(stdout=PIPE, stderr=PIPE)` + only reading stdout deadlocks when the child writes more than the pipe buffer (~64KB on macOS/Linux) to stderr. We only read stdout, so stderr fills, the child's write blocks, and the child stops producing stdout. Classic subprocess gotcha.

**Decision**: Drain stderr in a daemon thread that appends lines to an in-memory buffer. Main loop reads stdout uninterrupted. On exit, join the stderr thread, surface its content in any error message.

**Alternative considered**: `stderr=STDOUT` (merge). Rejected because it pollutes the stream-json parsing path with non-JSON lines (mostly tolerable due to JSON parse `try/except`, but loses error-vs-output distinction in error reports).

**Consequences**:
- ✅ Cannot deadlock from stderr backpressure.
- ✅ stderr preserved for error messages.
- ❌ +15 lines of thread plumbing.

---

## ADR 008 — Watchdog with both total + idle timeouts

**Status**: Accepted. Same code review pass as ADR 007.

**Context**: `claude -p` has no built-in timeout. A stuck tool call, network hang, or API outage can leave the subprocess running indefinitely, blocking the harness loop forever. We need a timeout, but: total wall-clock alone misses "slow but legitimate" shifts (could be 25 min for a complex feature). Idle alone misses "subprocess making no progress but emitting heartbeat events".

**Decision**: Two timeouts enforced by a daemon thread:
- `HARNESS_SHIFT_TIMEOUT_SEC` (default 1800s / 30 min) — hard ceiling on shift wall-clock.
- `HARNESS_IDLE_TIMEOUT_SEC` (default 300s / 5 min) — no stdout for this long = stuck.

On trip: SIGTERM, 5s grace, SIGKILL. Raise `ShiftTimeoutError` (distinct from generic errors) so the loop retry layer can treat timeouts as transient.

**Consequences**:
- ✅ Cannot hang forever on any one shift.
- ✅ Retry layer can distinguish timeout (likely transient) from RuntimeError (likely real).
- ✅ Both env-tunable for tight cost control or generous research budgets.
- ❌ Aggressive idle threshold could kill a legitimately slow long thinking phase. Default (5 min) is conservative enough to never have tripped in practice yet.

---

## ADR 009 — Validate planner output before commit

**Status**: Accepted.

**Context**: The Planner is an LLM. Its output is untrusted by default. If `feature_list.json` is malformed (missing fields, wrong category, duplicate ids, all `passes:true` from the start), downstream Generator shifts burn dollars working against a broken spec. Detection-after-the-fact is expensive.

**Decision**: After Planner returns, run `validate.py` against the working tree. Schema rules:
- `feature_list.json` is a JSON array, 10–200 entries
- Every entry has id / category / priority / description / steps / passes
- Categories ∈ {functional, style}; priorities ∈ {high, medium, low}
- All `passes` start `false`
- 3–10 entries have `is_smoke: true` (for regression-check coverage)
- ≥ 8 entries have ≥ 8 steps (E2E flow coverage)
- `init.sh` and `claude-progress.txt` exist

If any check fails: do **not** commit, raise with the error list, leave the working tree for human inspection.

**Consequences**:
- ✅ Bad plans cost only the Planner shift (~$0.5), not 20 Generator shifts (~$30).
- ✅ Schema is mechanical — no judgment call on whether a plan is "good".
- ✅ Validator catches things `eyeball review` misses (duplicate ids, off-by-one step counts).
- ❌ Loose plans that would still produce decent output get rejected (over-strict).
- ❌ No auto-retry-on-validation-failure yet. Possible future enhancement: feed the errors back as a planner re-prompt; risky because Planner could surface-match the rules without fixing the underlying problem.

---

## ADR 010 — Retry at the loop level, not inside the backend

**Status**: Accepted.

**Context**: A single Generator shift can fail for many reasons: transient network blip, claude rate limit, ShiftTimeoutError, malformed JSON event, claude crash. Without retry, `harness-runner run mini-hex --loop 10` aborts on the first transient — we wake up to find only 2 shifts completed.

Where to put retry logic?
- (a) Inside `Backend.run()` — retry the LLM call.
- (b) Inside the role (`planner.py`, `generator.py`) — retry the whole shift logic.
- (c) At the loop driver (`run.py`, `cli.py` generate-loop) — retry the whole role call.

**Decision**: (c). Loop drivers wrap each `generate()` call in `generate_with_retry()` (3 attempts, 5/10/15s backoff). After exhausting retries on one shift, log the failure and **continue to the next shift** rather than aborting the loop.

**Consequences**:
- ✅ Backend stays pure (just runs the subprocess; doesn't make retry policy decisions).
- ✅ Per-shift retries don't cross shift boundaries — a stuck shift can't poison the next one's fresh attempt.
- ✅ One transient doesn't burn the whole budget.
- ❌ A genuinely broken shift gets retried 3× before we give up, wasting ~$3-9.
- ❌ Loop drivers know about retry policy — slight coupling, but only one place each.

---

## ADR 011 — Budget cap is a soft pre-shift check, not mid-shift abort

**Status**: Accepted.

**Context**: `HARNESS_BUDGET_USD` lets a user cap project cost. Two implementations:
- (a) Soft: check cumulative cost before each new shift; if over, stop the loop. Current shift always finishes.
- (b) Hard: monitor cost mid-shift, SIGTERM the subprocess as soon as cumulative crosses the limit.

**Decision**: (a). Check at loop level before launching each Generator shift.

**Consequences**:
- ✅ Current shift always produces a clean commit and history entry — no half-finished work.
- ✅ Easy to reason about: cap is a "no new shifts" signal, not a "kill what's running" signal.
- ✅ Implementation is ~20 lines (read history, sum, compare). No mid-shift signal handling.
- ❌ Can overshoot by one shift's worth of cost (typically $1-5). Acceptable for a soft cap; if hard cap is needed, that's a future enhancement.
- ❌ Per-call cost from `claude -p` is reported only at result event (end of shift). Live mid-shift tracking would require parsing every `assistant` event's usage — possible but invasive.

---

## ADR 012 — `is_smoke` as a schema field (not Generator-picked)

**Status**: Accepted.

**Context**: Generator's regression check at startup needs to know which features are "core flows" worth re-verifying. Original (pre-2026-05-19) prompt: "pick 1–2 features marked passes:true that exercise the app's core flows". This left Generator to judge "core" each shift — different shifts picked different features, coverage was uneven, broken edge cases survived.

**Decision**: Planner labels 3–5 critical features with `is_smoke: true` at plan time. Generator's regression check reads `is_smoke:true AND passes:true` and verifies all of them. Validator enforces the count (3 ≤ smoke ≤ 10).

**Consequences**:
- ✅ Smoke set is stable across shifts — regression check is reproducible.
- ✅ Choice of smoke features is made once (Planner), reviewed once (validator), audited via `feature_list.json` diff history.
- ✅ Generator can't drift from regression coverage as the build evolves.
- ❌ One more schema field for Planner to manage; one more validator rule to maintain.
- ❌ If Planner picks bad smoke features, every shift re-verifies the wrong things. Mitigation: validator could add a heuristic (smoke features should span categories), but we haven't found this necessary in practice.

**Reference**: this is the borrowing of Anthropic's pattern that pushes guidance from prompt-level ("pick something core") to schema-level (`is_smoke: true`). Schema-level constraints bind harder than prompt-level on Sonnet-4.6+.

---

## ADR 013 — Atomic per-feature commits, even when batching

**Status**: Accepted. Tightened 2026-05-20 when "one feature per shift" was loosened (ADR 014).

**Context**: When Generator does multiple features in one shift, two commit strategies:
- (a) One commit per shift, however many features. Smaller git log.
- (b) One commit per feature, regardless of shift boundaries.

**Decision**: (b). Each feature flip is its own `feat: f<id> ...` commit.

**Consequences**:
- ✅ `git bisect` works — can isolate which feature introduced a regression.
- ✅ `git blame` is meaningful — every line of feature code traces to its feature.
- ✅ Selective revert: dropping one bad feature doesn't drop the four around it.
- ✅ shift log can describe a batch ("Batch: f003, f004 — both touch create-form"); git history stays granular.
- ❌ More commits, noisier `git log --oneline`. Acceptable; commit-count-per-day isn't a metric anyone optimizes.
- ❌ Generator must remember to commit between features rather than batching at end-of-shift. Encoded as a Hard rule in `generator.md`.

---

## ADR 014 — Loosened "one feature per shift" to "declared batch of related features"

**Status**: Accepted 2026-05-20. Originally "exactly one feature per shift" until empirical evidence on flow-test (2026-05-19) showed the rule was bypassed in practice.

**Context**: Anthropic's autonomous-coding quickstart was written for Sonnet 4.5 (Nov 2025) with then-current context windows and reasoning limits. By Opus 4.7 (1M context, May 2026), capability has roughly doubled. The "exactly one feature per shift" rule was anchored to those older limits.

On the flow-test run (2026-05-19), Generator strictly obeyed the `passes` flip rule (only flipped f001) but actually wrote *all 38 features' worth of code* in one shift. Its own shift log noted: "scaffolding step naturally includes full implementation… all other features functionally implemented but not yet verified/marked true".

This is the worst rule state: the rule didn't constrain behavior, but it did produce misleading progress metrics (1/38 passing while functional state was 38/38).

**Decision**: Allow Generator to pick 1–N related features per shift (a "batch"), declared in the shift log as a `Batch: f003, f004 — <rationale>` line. Each feature in the batch still gets its own verification + atomic commit (ADR 013) + flip. Soft cap suggestion: ~5 features per batch, but no hard enforcement.

**Consequences**:
- ✅ Rule matches model behavior — no more bypass.
- ✅ Per-feature audit unit preserved (one commit / one verification / one flip).
- ✅ Smaller projects can complete cleanly in fewer shifts.
- ❌ Per-shift cost variance increases — a 5-feature batch costs more than a 1-feature shift. Mitigation: budget cap (ADR 011) + watch HARNESS_BUDGET_USD.
- ❌ Risk of silent over-claim: Generator declares "Batch: f003" but writes code for f003-f005, only flipping f003. Mitigation: `Stay in declared scope` hard rule + future automated diff-vs-claim check (BACKLOG).

**Lesson recorded**: prompt rules anchored to model-capability-of-the-time need periodic re-evaluation. The model's ceiling moves; the rules don't.

---

## ADR 015 — External integration via files only, no shared library with NeoClaw

**Status**: Accepted.

**Context**: harness-runner runs adjacent to NeoClaw on the same machine. Initial discussion floated three integration patterns:
- (Path C in original design discussion) Pull harness into NeoClaw as an `OrchestratorAgent`, share code.
- (Path B) Use Claude Code's subagent / Skills mechanism inside NeoClaw.
- (Path A) Keep harness fully independent, integrate externally via shell + file reads.

**Decision**: Path A. harness-runner is a standalone Python CLI. NeoClaw (or any external observer) interacts only through:
- `harness-runner <subcommand>` shell invocation
- Reading `projects/<name>/` files (feature_list / progress / history / git log)

No shared library, no in-process import, no RPC.

**Consequences**:
- ✅ harness-runner is language-agnostic from NeoClaw's perspective. Either project can be rewritten without touching the other.
- ✅ Integration surface is minimal and observable.
- ✅ harness-runner can be used by anyone, not just NeoClaw users.
- ✅ Different machines can run them independently; integration is by SSH + file sync if ever needed.
- ❌ NeoClaw can't "stream" harness events as they happen unless it tails files. For Step 1, file tailing is enough.
- ❌ Some plumbing duplicated across both projects (e.g. cost tracking, progress display).

**Lesson recorded**: the earlier instinct to share TypeScript code (ADR 001 first iteration) was based on assumed code-level integration that this ADR explicitly rejects. Confirming integration shape early would have avoided the language-choice churn.

---

## Decisions still open

- **ADR-pending: Add Evaluator role + Playwright MCP**. Discussed 2026-05-20; deferred until URL shortener results inform priority. Three-layer design (MCP server choice / config placement / prompt scope) sketched but not implemented.
- **ADR-pending: Switch to `anthropic_api` backend before 2026-06-15 billing change**. Currently using `claude_cli` which falls under Anthropic's programmatic-credit pool effective that date. If usage exceeds Team Premium's $100/seat monthly credit, direct API likely cheaper. Trigger: monthly cost > credit.
- **ADR-pending: Generator self-boundary detection**. The deeper question raised by ADR 014: how does Generator reliably know how much it can complete in one shift when context isn't the binding constraint? See BACKLOG.md "Generator one-shift boundary (long-term research)" — currently a research direction, no decision yet.
- **ADR-pending: Cross-project cost aggregator**. Each project has its own `.harness_history.jsonl`. No global view. Possible future tool: `harness-runner cost --since <date>` summing across all projects.
- **ADR-pending: Bash command allowlist for unattended runs**. Currently using `--dangerously-skip-permissions`. For long unattended runs, Anthropic's reference uses a shlex-based allowlist (see autonomous-coding/security.py for the design). Implementation deferred to Step 2.

---

## Meta: how to use this document

For interview-portfolio purposes, this document complements the README. README answers *what is this and how do I run it*. This document answers *why is it this way and what did you consider*. Engineering judgment shows up in the trade-offs section of each ADR.

For self-reference: when re-evaluating any decision (e.g. "should I switch backends now"), find the relevant ADR's Consequences and check whether the original trade-offs still apply.
