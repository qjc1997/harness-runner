# harness-runner — Design Decisions

Lightweight ADR (Architecture Decision Record) log for the harness-runner project.

Format per decision: **Status / Context / Decision / Reason / Consequences**. The Reason section is where the engineering trade-off lives — what alternatives were considered, why the chosen one won, and what specifically tipped the decision. Without it, the Decision looks arbitrary and the Consequences look like rationalizations.

Decisions are listed in roughly the order they were made.

Repo: <https://github.com/qjc1997/harness-runner>
Last updated: 2026-05-21

---

## ADR 001 — Implementation language: Python (after starting in TypeScript)

**Status**: Accepted.

**Context**: Both Python and TypeScript are popular choices for agent development. TypeScript brings strong static typing for orchestration code and tight integration with the Node ecosystem. Python has long been the lingua franca of the LLM agent space, with most major frameworks (LangChain, AutoGen, MetaGPT, OpenHands, Claude Agent SDK) shipping Python-first.

**Decision**: Build as a Python package (`src/harness_runner/`), installable via `pip install -e .`, pure stdlib (no external dependencies), Python ≥ 3.9.

**Reason**: Two factors tipped the choice toward Python: (a) Anthropic's reference autonomous-coding quickstart is Python — patterns translate 1:1 in Python and require manual transliteration in TypeScript, so every borrowed design idea costs less to validate against the reference; (b) the broader LLM/agent ecosystem (LangChain, AutoGen, MetaGPT, OpenHands, Claude Agent SDK) is Python-first, so future tool reuse — custom agents, MCP servers, evaluation frameworks — lands in Python with no integration friction.

**Consequences**:
- ✅ Aligned with where the agent-tooling ecosystem actually lives. Future ports of Anthropic / community patterns translate directly.
- ✅ Single-language project; no JS/Python split per role.
- ✅ Stdlib-only constraint keeps the project trivially portable — no heavy `pip install` before it runs.
- ❌ Cannot share code in-process with adjacent TypeScript projects; cross-system integration must go through CLI invocations + file reads (see ADR 015).

---

## ADR 002 — LLM integration: `claude` CLI subprocess (not Claude Agent SDK)

**Status**: Accepted.

**Context**: Need to invoke Claude from Python for each shift. Two paths: (a) the official `claude-code-sdk` Python library (in-process, with `@tool` decorators, hooks, structured message types); (b) shell out to `claude -p` and parse the stream-json output ourselves.

**Decision**: Subprocess. Each shift = one `Popen(["claude", "-p", "--output-format", "stream-json", ...])` call. No SDK dependency.

**Reason**: SDK gives ergonomic in-process tooling — typed messages, decorator-based tool registration, lifecycle hooks. Subprocess gives three different things: (a) **version independence** — SDK breaking changes between releases can't silently break us, we just consume the `claude` CLI's output protocol; (b) **auth pass-through** — uses whatever auth the user already has set up for `claude` (subscription session or `ANTHROPIC_API_KEY`), no separate credential handling; (c) **language-agnostic backend substrate** — Codex CLI, Gemini CLI, or direct API can all become parallel implementations of the same `Backend` protocol (ADR 003). The decisive trade-off was SDK's in-process tool hooks vs vendor/version isolation. Anthropic's announced 2026-06-15 billing change for `claude -p` and Agent SDK made backend-switchability a **dated, near-term** real requirement, not a hypothetical one. Substrate independence beat SDK ergonomics because the switching scenario has a calendar attached.

**Consequences**:
- ✅ Zero dependency on a specific SDK version.
- ✅ Auth path the user already uses works without re-configuration.
- ✅ Trivially swappable: a different CLI is just another `Backend` implementation.
- ❌ Hand-rolled stream-json parsing (~30 lines, see ADR 006).
- ❌ No SDK conveniences (`@tool` decorator, `PreToolUse` hooks). If we ever need those for the Evaluator role, we'd reach for them.
- ❌ Subprocess-specific failure modes (pipe deadlock, zombies, signal handling) we engineer around — see ADR 007, ADR 008.

---

## ADR 003 — Pluggable LLM backend abstraction

**Status**: Accepted. Implemented 2026-05-19.

**Context**: With `claude_cli` as the only working backend, abstracting behind a `Backend` protocol introduces ~80 lines of interface code with no immediate functional benefit. Pure YAGNI ("you aren't gonna need it") says don't abstract until you have a second implementation.

**Decision**: Define a `Backend` protocol (`backends/base.py`) with a single method `run(cwd, prompt, system_prompt_append, model, on_event) -> RunResult`. Concrete backends register themselves; selection via `HARNESS_BACKEND` env var or `get_backend(name)`. Current: `claude_cli`. Planned: `anthropic_api`, `codex_cli`, `gemini_cli`.

**Reason**: YAGNI is the right default for speculative requirements. But Anthropic announced 2026-06-15 will move `claude -p` and Claude Agent SDK to a separate metered credit pool, billed at full API rates. That is a **known, dated, calendar requirement** — not speculative. The cost of NOT abstracting now: when usage in June exceeds the credit, we'd be doing weeks of refactoring under pressure to extract a backend interface from a codebase that assumes `claude_cli` everywhere. The cost of abstracting now: ~80 lines of protocol code, zero runtime overhead, zero hypothetical complexity (we know exactly what the second implementation needs to look like). The decisive trade-off: small premature-abstraction cost now vs large emergency-refactor cost later. YAGNI doesn't apply when the requirement has a date attached.

**Consequences**:
- ✅ Switching the LLM provider requires no role/orchestration code changes — drop a new backend module, set env var.
- ✅ Forces a clean interface — roles can't accidentally couple to claude-specific behavior.
- ✅ Trivially testable with a fake backend that doesn't call any LLM.
- ❌ ~80 lines of protocol code overhead for a single live backend today.
- ❌ Stream events are typed as `Any` in the protocol because each backend has its own event shape — slight type-safety loss compared to a single concrete type.

---

## ADR 004 — File-based state, no shared library, no DB

**Status**: Accepted. Inherited from Anthropic's autonomous-coding pattern.

**Context**: Cross-shift state needs to survive subprocess death. Each Generator shift is a brand-new Claude session that has zero memory of previous shifts — state must live somewhere durable.

**Decision**: All persistent state is plain files in `projects/<name>/`:
- `feature_list.json` — work to do, source of truth for what's done
- `claude-progress.txt` — human-readable shift log (markdown)
- `.harness_history.jsonl` — per-shift structured outcomes
- `.git/` — code history
- `init.sh`, `app_spec.md` — config/spec

No DB, no IPC, no shared in-process state between roles.

**Reason**: Four storage options considered: (a) in-process state via a long-running coordinator — dead-on-arrival because every shift is a fresh subprocess; (b) SQLite or similar DB — solid durability but adds schema migration concerns and a query layer for what's actually append-mostly + occasional read-modify-write; (c) git commits only — works for code but not for ephemeral metadata (e.g., cost.jsonl); (d) flat JSON / JSONL files in the project directory. The harness's binding constraint is "every shift is a new subprocess that reconstructs context from disk" — this rules out (a) entirely. DB (b) is overkill: our access pattern is append (history), full-read + targeted-write (feature_list), nothing query-heavy; the dependency surface and migration concerns don't earn their keep. (c) is read-only. (d) won because plain files satisfy durability + cross-process visibility + cross-language readability + zero-dependency. The decisive trade-off was DB's query convenience vs files' radical simplicity and observability — `cat`, `tail`, `grep`, `jq` all work on the state, and any future observer (NeoClaw, a Slack bot, a dashboard) can read it without bindings.

**Consequences**:
- ✅ Survives crashes, interrupts, restarts. No "in-flight transaction" to recover.
- ✅ Every component (Planner, Generator, future Evaluator, external observers) reads the same files. One source of truth.
- ✅ Trivially debuggable — `tail -f` reveals state changes live.
- ✅ Language-agnostic. NeoClaw (TS) can observe a Python project without bindings.
- ❌ No queries. If we ever need to aggregate across many projects ("total spent across all projects this month"), we'd hand-roll it from JSONL.
- ❌ Race conditions if multiple writers existed (we have one writer at a time by design, but the constraint is implicit).

---

## ADR 005 — Single-shot Claude session per shift (no `--resume`)

**Status**: Accepted.

**Context**: NeoClaw uses `claude --resume <sessionId>` to keep a long-running conversation per chat. The harness could do the same — each Generator shift could `--resume` the previous shift's session, preserving Claude's context for cheaper re-reads via prompt caching.

**Decision**: Fresh session per shift. No `--resume`. Each shift is a brand-new Claude session that reconstructs context from disk (feature_list + progress + git log + app_spec).

**Reason**: The harness's design metaphor is "engineers working in shifts, each arriving cold" (Anthropic's framing in their long-running agents article). `--resume` directly contradicts that — it preserves session-internal memory across shifts, which means a shift could "remember" something that isn't in the durable disk state. That's a leak: if the design says "all knowledge must round-trip through disk", `--resume` lets implicit knowledge skip the round-trip, and over time the disk state degrades into "decoration" while the session memory carries the real load. Trade-off: prompt-caching savings (cache_read at ~10% the input rate adds up over long projects) vs design philosophy integrity. We took the dollar cost because the integrity *is* the harness's whole value proposition — without it, this is just "long-running agent with progress notes". Also: `--resume` would block parallel shifts (one subprocess per session ID), and we want the option to parallelize later.

**Consequences**:
- ✅ Each shift is auditable in isolation — no opaque session history affecting reasoning.
- ✅ Forces durable state to actually be durable. Generator can't "remember why I made this choice three shifts ago" — has to re-derive from progress notes.
- ✅ No "session corruption" — a bad shift can't poison future shifts.
- ✅ Parallel shifts become possible (haven't built yet, but the option is open).
- ❌ Pay full-input token cost per shift (cache hits help but don't eliminate).
- ❌ More churn for the LLM: every shift's first 2-3 turns are re-reading state.

---

## ADR 006 — stream-json output parsing (not synchronous `--output-format json`)

**Status**: Accepted.

**Context**: `claude -p` has two output modes. Synchronous JSON: one event at end with the complete response. Stream-json: one JSON event per line, in real-time as the agent runs.

**Decision**: stream-json + `--verbose`. Parse each line, fire `on_event` callback per event. Roles render tool calls / text / thinking to stderr live.

**Reason**: Synchronous mode is genuinely simpler — no parsing loop, no event dispatch, just `parse(stdout)` at the end. Stream-json is ~30 lines of hand-rolled line-by-line JSON parsing plus event-type dispatch. So why pay the complexity? Three reasons converge: (a) **shift duration** — a 5-15 minute shift in synchronous mode is total opacity; no UI feedback, no progress hint, the user just stares at a blank terminal; (b) **watchdog liveness signal** (ADR 008) — without streaming events, the idle-timeout watchdog can't distinguish "subprocess hung" from "subprocess thinking hard"; (c) **debugging** — when a shift fails, the stream-json tool-use events tell you the last 10 tool calls; synchronous mode tells you nothing pre-failure. The decisive trade-off: ~30 lines of parsing complexity vs runtime observability. Easy choice — observability wins on a 15-minute task. On a 1-second task it wouldn't matter.

**Consequences**:
- ✅ Real-time observability: each shift's tool calls / thinking visible to the operator and to the watchdog.
- ✅ Watchdog can update `last_event_at` on every stdout line.
- ❌ Hand-rolled parsing (handle malformed lines, weird event types like `rate_limit_event`, etc.).
- ❌ Some claude-CLI-specific event shapes leak into the protocol as opaque `on_event` payloads (the Backend protocol handles this by typing `event: Any`).

---

## ADR 007 — stderr drained in a background thread (not merged into stdout)

**Status**: Accepted.

**Context**: The naïve subprocess pattern `Popen(stdout=PIPE, stderr=PIPE)` + reading only stdout is a textbook deadlock vector. The pipe buffer is ~64KB on macOS/Linux; once stderr fills, the child's write blocks, and we starve waiting for stdout that the child can no longer produce.

**Decision**: Drain stderr in a daemon thread that appends lines to an in-memory buffer. Main loop reads stdout uninterrupted. On exit, join the stderr thread, surface its content in any error message.

**Reason**: Two ways to fix the deadlock: (a) merge stderr into stdout via `stderr=STDOUT` — simple one-flag change; (b) drain stderr in a background thread — ~15 lines, preserves the stream separation. (a) would pollute the stream-json parsing path with raw non-JSON stderr lines. Our parser tolerates that (the `try / except json.JSONDecodeError: continue` already handles non-JSON lines), so functionally (a) works. But (a) destroys error-vs-output distinction in error reports. When a shift fails, the operator wants to see "what was on stderr" as a focused thing, not buried in the stream of normal output. The decisive trade-off: ~15 lines of thread plumbing vs losing diagnostic clarity. Diagnostics won because when a long unattended run fails at 3am, the operator's first question is "what did stderr say" and the answer needs to be one grep away, not buried in 50,000 lines of merged stream.

**Consequences**:
- ✅ Cannot deadlock from stderr backpressure regardless of how verbose claude gets.
- ✅ stderr preserved as a focused channel for error reporting.
- ❌ +15 lines of thread plumbing.
- ❌ One more daemon thread to reason about during process teardown (we handle this by joining with a 2-second timeout).

---

## ADR 008 — Watchdog with both total + idle timeouts

**Status**: Accepted.

**Context**: `claude -p` has no built-in timeout. A stuck tool call, hung network, or upstream API outage leaves the subprocess running indefinitely, blocking the harness loop forever.

**Decision**: Two timeouts enforced by a daemon thread:
- `HARNESS_SHIFT_TIMEOUT_SEC` (default 1800s / 30 min) — hard wall-clock ceiling.
- `HARNESS_IDLE_TIMEOUT_SEC` (default 300s / 5 min) — no stdout for this long = stuck.

On trip: SIGTERM, 5-second grace, SIGKILL. Raise `ShiftTimeoutError` (distinct from generic errors) so retry logic can treat timeouts as transient.

**Reason**: Three timeout designs considered: (a) no timeout — unbounded hang risk; (b) total-only (just wall-clock cap) — misses "slow but legitimately progressing" shifts that emit events; (c) idle-only (just no-output cap) — misses "tool loop emitting heartbeat but not real progress" cases. We have BOTH because the failure modes are orthogonal: a stuck network call presents as "idle but well within total budget"; a runaway tool loop presents as "active output but consumes all wall-clock". Either trigger alone leaves a gap. The decisive trade-off: minor extra complexity of tracking two thresholds (single watchdog thread can do both, no big deal) vs catching more failure modes for unattended runs. Both timeouts together close the gap. Also designed as separate env vars so an operator running expensive Opus shifts can tighten the idle threshold to fail-fast, while research scenarios can loosen both.

**Consequences**:
- ✅ Cannot hang forever on any one shift.
- ✅ Retry layer (ADR 010) can distinguish timeout (likely transient) from RuntimeError (likely persistent).
- ✅ Env-tunable for tight cost control or generous research budgets.
- ❌ Aggressive idle threshold could kill a legitimately slow long thinking phase. Default 5 min is a conservative starting point; tunable per deployment.

---

## ADR 009 — Validate planner output before commit

**Status**: Accepted.

**Context**: The Planner is an LLM. Its output is untrusted by default. If `feature_list.json` is malformed (missing fields, wrong category, duplicate ids, all `passes:true` from the start), every downstream Generator shift wastes money working against a broken spec.

**Decision**: After Planner returns, run `validate.py` against the working tree. Schema rules:
- 10–200 entries
- Every entry has required fields (id / category / priority / description / steps / passes)
- Categories ∈ {functional, style}; priorities ∈ {high, medium, low}
- All `passes` start `false`
- 3–10 entries have `is_smoke: true`
- ≥ N entries have ≥ 8 steps where N scales with plan size (see ADR 014 caveat)
- `init.sh` and `claude-progress.txt` exist

On any failure: do NOT commit, raise with the error list, leave the working tree for human inspection.

**Reason**: Three approaches considered: (a) no validation — trust the Planner, let downstream failures surface the issue; (b) validate after commit, with the option to revert; (c) validate before commit. (a) is what Anthropic's reference does, and the failure mode is expensive: a bad plan costs $5-20 in subsequent Generator shifts before the issue becomes visible. (b) means git history fills with "harness: bad plan" commits + their reverts — noisy and hard to bisect later. (c) catches the issue at ~$0.50 (one Planner shift's cost) before any commit pollutes history. The decisive trade-off: false positives (rejecting plans that are marginal but acceptable) vs avoidance of expensive downstream waste. False positives cost a Planner re-run ($0.30) + a few minutes of human attention. False negatives cost 20 Generator shifts. The economic asymmetry is ~30:1 in favor of strict validation. Thresholds should be tunable (e.g., scale required step-count by plan size) so marginal plans aren't rejected wholesale.

**Consequences**:
- ✅ Bad plans cost ~$0.50, not $5-20.
- ✅ Schema is mechanical — no judgment call on whether a plan is "good", just whether its shape is valid.
- ✅ Catches things eyeball review misses (duplicate ids, off-by-one step counts).
- ❌ Marginal plans get rejected (over-strict). Mitigated by scaling thresholds with plan size.
- ❌ No auto-retry on validation failure yet (BACKLOG). Risk if added: Planner could surface-match the rules without fixing the underlying problem.

---

## ADR 010 — Retry at loop driver, not inside backend

**Status**: Accepted.

**Context**: A single Generator shift can fail for many reasons: transient network blip, rate limit, `ShiftTimeoutError`, malformed JSON event, claude crash. Without retry, `run --loop 10` aborts on the first transient — wake up to find only 2 shifts done.

**Decision**: Loop drivers (`run.py`, `cli.py`'s `generate-loop`) wrap each `generate()` call in `generate_with_retry()` (3 attempts, 5/10/15s backoff). Backend's `run()` always one attempt — never retries internally. After exhausting retries on one shift, log the failure and **continue to the next shift** rather than aborting the loop.

**Reason**: Three placement options for retry logic: (a) inside the backend's `run()` — every caller gets retry by default; (b) inside the role module (`planner.py`, `generator.py`) — each role wraps its own; (c) at the loop driver. (a) is bad because not every caller wants retry — a debugging script that wants "run once and fail loudly" doesn't want hidden retries masking the failure. (b) means duplicated wrapping code in each role + the role becomes opinionated about retry semantics. (c) keeps backend semantics pure ("one call = one attempt, results in success or one of several typed errors") and lets retry policy be a property of *running in a loop* — which is the only context where retry makes sense. The decisive trade-off: small duplication between the 2-3 loop drivers (each wraps `generate()` similarly) vs backend purity + retry-as-opt-in. Backend purity wins because the backend is the most reused piece — keeping it free of policy means it's safe to use anywhere. The loop-driver duplication is one function call site, not a real maintenance burden.

**Consequences**:
- ✅ Backend stays pure (one call = one attempt; one of several typed exit conditions).
- ✅ Per-shift retries don't cross shift boundaries — a stuck shift can't poison the next one's fresh attempt.
- ✅ One transient doesn't burn the whole budget.
- ❌ A genuinely broken shift gets retried 3× before we give up, wasting ~$3-9.
- ❌ Loop drivers know about retry policy — a minor coupling, but only in 2-3 places.

---

## ADR 011 — Budget cap is a soft pre-shift check (not mid-shift abort)

**Status**: Accepted.

**Context**: `HARNESS_BUDGET_USD` lets an operator cap project cost. Two enforcement points: check before each new shift, or monitor cost mid-shift and abort as soon as the cumulative crosses the limit.

**Decision**: Soft pre-shift check. Read `.harness_history.jsonl`, sum `cost_usd`, raise `BudgetExceededError` if ≥ limit. Loop drivers catch and break out cleanly. The current shift always finishes.

**Reason**: Hard cap (mid-shift SIGTERM when cumulative crosses limit) considered first. It enforces the budget precisely. But the failure mode is: half-finished work, dirty git state (uncommitted changes from a Generator that was building a feature), possibly broken dev servers (init.sh started a server before the kill). To recover, an operator has to inspect the working tree, manually decide what's salvageable, manually commit or rollback. That's not a budget cap — that's an emergency stop with cleanup costs. Soft cap means the current shift always reaches a clean commit + history entry before any further shifts are blocked. Overshoot is possible (up to ~one shift's worth: $1-5 typically). The decisive trade-off: precise enforcement vs clean exit state. For a $15 budget, ~$3 overshoot is 20%; for a $1000 budget, 0.5%. The blast radius of soft cap is small in absolute terms; the blast radius of hard cap (dirty state, manual recovery) is unbounded in operator time. Clean exit wins.

**Consequences**:
- ✅ Current shift always produces a clean commit + history entry — no half-finished work.
- ✅ "Cap" is a "no more new shifts" signal, easier to reason about than "kill what's running".
- ✅ Implementation is ~20 lines (read + sum + compare). No mid-shift signal handling.
- ❌ Can overshoot by one shift (typically $1-5). Acceptable for a soft cap.
- ❌ Per-call cost from `claude -p` is reported only at the result event (end of shift). Live mid-shift tracking would require parsing every `assistant` event's usage — possible but invasive.

---

## ADR 012 — `is_smoke` as a Planner-output schema field (not Generator-picked)

**Status**: Accepted.

**Context**: Generator's regression check at startup needs to know which features are "core flows" worth re-verifying. One option is to instruct Generator at prompt time — e.g., "pick 1–2 features marked `passes:true` that exercise the app's core flows" — and let Generator judge "core" each shift.

**Decision**: Planner labels 3–5 critical features with `is_smoke: true` at plan time. Generator's regression check at startup reads `is_smoke:true AND passes:true` and verifies all of them. Validator enforces the count (3 ≤ smoke ≤ 10).

**Reason**: Two failure modes lurk in a prompt-level rule: (a) different shifts could pick different features as "core" — regression coverage drifts; (b) a shift could pick features that don't actually exercise the real core, missing regressions in the critical paths. The root cause: the judgment "what is core?" would be made at every Generator shift, with no shared anchor. Three fix options: (a) tighten the Generator prompt — try to make it pick consistently — but prompts are stochastic, this would only narrow the variance not eliminate it; (b) hard-code "always check features f001 + f002" — works but doesn't generalize across projects; (c) move the judgment from "every Generator shift" to "Planner once, validated against schema". Option (c) — schema-level — makes the smoke set: stable across shifts, validated once at plan time, deterministic from Generator's perspective. The decisive trade-off: extra Planner cognitive load (must now think about which 3-5 features are critical) + extra validator rules vs reduced variance in regression coverage. Planner's added load is small (one judgment among many); regression coverage stability is large because it makes Generator's startup protocol deterministic. The pattern is "lift judgment from runtime to definition time" — same pattern as schema validation in general.

**Consequences**:
- ✅ Smoke set is stable across shifts — regression check is reproducible.
- ✅ Choice of smoke features audited via `feature_list.json` diff history.
- ✅ Generator can't drift on regression coverage as the build evolves.
- ❌ One more schema field for Planner to manage; one more validator rule.
- ❌ If Planner picks bad smoke features, every shift re-verifies the wrong things.

---

## ADR 013 — Atomic per-feature commits, even when batching

**Status**: Accepted.

**Context**: When Generator does multiple features in one shift, two commit policies are possible: one commit per batch (simpler, fewer commits) or one commit per feature (one `feat: f<id>` per flipped feature, regardless of shift boundaries).

**Decision**: One commit per feature. Even if Generator does f003 + f004 + f005 in one shift, each gets its own `feat: f<id>` commit.

**Reason**: One-commit-per-batch is genuinely simpler — fewer git operations, cleaner `git log --oneline`. So why pay for the granularity? Because `git bisect` and `git blame` are the killer use cases for autonomous code generation. When a regression surfaces 50 commits later, the bisect resolution decides whether you find the cause in minutes (one feature's diff to inspect) or hours (a 5-feature batch's combined diff to untangle). The Anthropic-pattern shift log can summarize the batch in prose; git history needs to stay forensic-grade. The decisive trade-off: log noise (one commit per feature is noisier than one per batch) vs forensic granularity. Forensics wins by a wide margin because autonomous code generation WILL introduce regressions that need bisecting — that's not a hypothetical; it's a near-certainty. Once you've debugged one regression with `git bisect` and landed on a per-feature commit, the cost-benefit is obvious. Once you've debugged one regression with `git bisect` and landed on a 5-feature commit, you've already lost an hour vs the per-feature alternative.

**Consequences**:
- ✅ `git bisect` works at feature granularity.
- ✅ `git blame` is meaningful — every line of feature code traces to its feature's commit.
- ✅ Selective revert: dropping one bad feature doesn't drop the four around it.
- ✅ Shift log can describe a batch; git history stays granular.
- ❌ More commits, noisier `git log --oneline` (acceptable — commit count isn't a metric anyone optimizes).
- ❌ Generator must remember to commit between features, not batch at end-of-shift. Encoded as a Hard rule in `generator.md`.

---

## ADR 014 — Loosened "one feature per shift" to "declared batch of related features"

**Status**: Accepted 2026-05-20. Originally "exactly one feature per shift" until empirical evidence on flow-test (2026-05-19) showed the rule was bypassed in practice.

**Context**: Anthropic's autonomous-coding quickstart (Sonnet 4.5 era, Nov 2025) prescribed "exactly one feature per shift, only flip one `passes` flag". By Opus 4.7 (May 2026), context windows and reasoning capabilities had roughly doubled.

**Decision**: Allow Generator to pick 1+ related features per shift (a "batch"), declared in the shift log as the first line: `Batch: f003, f004 — <rationale>`. Each feature in the batch must still be verified against its own `steps`, flipped individually, and committed atomically (ADR 013). Soft cap suggestion: ~5 features per batch.

**Reason**: Empirical observation on the 2026-05-19 flow-test run: Generator strictly obeyed the `passes` flip rule (only flipped f001) but actually wrote *all 38 features' worth of code* in one shift, then logged its own behavior honestly: "scaffolding step naturally includes full implementation". The rule was being bypassed by the model's actual capability — Opus 4.7 simply does more in one session than Sonnet 4.5 could. Three options: (a) tighten the rule until it actually binds the model — requires the model to deliberately under-deliver, fights against training; (b) accept the rule is informational only — leaves the metric ("1/38 passing") misleading; (c) loosen to match observed behavior, ask the model to be honest about what it actually completed. The decisive trade-off: bypass-by-model (rule produces misleading progress metrics, undermines audit value) vs scope sprawl (Generator could grab too much per shift, blow context, lose discipline). Option (c) admits the model's increased capability and adds a new compensating rule — "Stay in declared scope" — to bound the autonomy: Generator declares its batch upfront, surprise-flipping features it didn't declare is the over-claim signal. The audit unit shifts from "shift" to "feature" — still preserved by ADR 013's atomic commits. The model's capability moved; the rules had to move with it.

**Consequences**:
- ✅ Rule matches model behavior — no more silent bypass.
- ✅ Per-feature audit unit preserved (one commit / one verification / one flip).
- ✅ Smaller projects can complete cleanly in fewer shifts.
- ❌ Per-shift cost variance increases — a 5-feature batch costs more than a 1-feature shift. Mitigated by budget cap (ADR 011).
- ❌ Risk of silent over-claim: Generator declares "Batch: f003" but writes code for f003-f005, only flipping f003. Mitigated by "Stay in declared scope" hard rule + future automated diff-vs-claim check (BACKLOG).

---

## ADR 015 — External integration via files only, no shared library with NeoClaw

**Status**: Accepted.

**Context**: harness-runner runs adjacent to NeoClaw on the same machine. Initial discussion floated three integration patterns:
- (Path C in original design discussion) Pull harness into NeoClaw as an `OrchestratorAgent`, share code.
- (Path B) Use Claude Code's subagent / Skills mechanism inside NeoClaw.
- (Path A) Keep harness fully independent, integrate externally via shell + file reads.

**Decision**: Path A. harness-runner is a standalone CLI. NeoClaw (or any external observer) interacts only through:
- `harness-runner <subcommand>` shell invocation
- Reading `projects/<name>/` files (feature_list / progress / history / git log)

No shared library, no in-process import, no RPC.

**Reason**: Path C (deep integration, share code) gives ergonomic in-process composition — NeoClaw could `import` from harness-runner directly, share types, call functions. The cost: tight coupling (every NeoClaw release becomes a coordinated harness-runner release), single repo (both projects' build/test/CI tangled), language lock-in (both must be the same language — see ADR 001's language churn for what happens when this assumption breaks). Path B (subagent / Skills) was lighter coupling but still in-process. Path A (CLI + files) is the maximum-decoupling option. The decisive trade-off: ergonomics of tight integration vs robustness / portability of decoupled contracts. Decoupled won because the harness is meant to be usable by anyone, not just by NeoClaw users — including future-me on a different machine, including someone reading the GitHub repo who has never heard of NeoClaw, including the harness being driven from a CI pipeline, from another bot, or by hand. The Path A interface (CLI invocations + file reads) works in all those scenarios; Path C only works inside NeoClaw. Also: ADR 001 (Python rewrite) wouldn't have been possible under Path C without disrupting NeoClaw — the decoupling was load-bearing for that decision.

**Consequences**:
- ✅ harness-runner is language-agnostic from NeoClaw's perspective.
- ✅ Integration surface is minimal and observable (CLI + files).
- ✅ harness-runner is usable by anyone, not just NeoClaw users.
- ✅ Cross-machine deployment is possible (run harness on machine A, observe from machine B via SSH + file sync).
- ❌ NeoClaw can't "stream" harness events as they happen unless it tails files.
- ❌ Some plumbing duplicated across both projects (e.g., cost tracking exists in both).

---

## Decisions still open

These are the major design choices we've identified but deferred — listed here so future-me knows what's still ahead and why we haven't decided.

- **ADR-pending: Add Evaluator role + Playwright MCP**. Deferred. Generator can self-install Playwright (e.g., `bunx playwright install chromium`) and use it for UI verification without any harness support — that path delivers some of Evaluator's value at zero harness cost, lowering Evaluator's near-term priority. **Decision criteria**: implement Evaluator when we see actual false-positive Generator flips (Generator marks feature passing but it's broken) at non-trivial rates. Until then, the existing regression check (ADR 012) + Generator's self-Playwright is enough.

- **ADR-pending: Switch to `anthropic_api` backend before 2026-06-15**. Currently using `claude_cli` which falls under Anthropic's programmatic-credit pool effective that date. If sustained usage exceeds Team Premium's $100/seat monthly credit, direct API likely cheaper. **Decision criteria**: trigger when one month's measured cost > the included credit. Implementation outline already in BACKLOG.md; the abstraction (ADR 003) makes the switch a single backend module away.

- **ADR-pending: Generator self-boundary detection**. The deeper question from ADR 014: how does Generator reliably know how much it can complete in one shift when context isn't the binding constraint? Currently we trust the model to self-bound based on prompt guidance + the `Batch:` declaration mechanism. **Decision criteria**: if shift cost variance creeps up (say one shift in 10 costs >$5 because Generator over-batched), revisit with structured tooling — claimed-vs-actual diff audit, mid-shift retrospection, or model-introspective planning calls.

- **ADR-pending: Cross-project cost aggregator**. Each project has its own `.harness_history.jsonl`. No global view yet. **Decision criteria**: defer until we're running 3+ projects in parallel or someone actually asks "how much did I spend this week across everything".

- **ADR-pending: Bash command allowlist for unattended runs**. Currently using `--dangerously-skip-permissions`. Anthropic's reference uses a shlex-based allowlist (see autonomous-coding/security.py). **Decision criteria**: implement when running unattended for ≥ 6 hours becomes routine. Until then, the operator-attended risk profile makes the allowlist work-not-yet-justified.

---

## Meta: how to use this document

For interview-portfolio purposes, this document complements the README. README answers *what is this and how do I run it*. This document answers *why is it this way and what did you consider*. Engineering judgment shows up most clearly in the **Reason** section of each ADR — that's where alternatives, trade-offs, and the decisive factor are spelled out. Without it, decisions look like rationalizations after the fact.

For self-reference: when re-evaluating any decision (e.g., "should I switch backends now"), find the relevant ADR's Reason and ask whether the original trade-off still holds. If the binding constraint has moved (e.g., model capability for ADR 014; calendar deadline for ADR 003), the decision should move too.
