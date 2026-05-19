import { spawn } from 'bun';

export interface ClaudeRunOptions {
  cwd: string;
  prompt: string;
  systemPromptAppend?: string;
  model?: string;
  onEvent?: (event: unknown) => void;
}

export interface ClaudeResult {
  result: string;
  sessionId: string;
  costUsd: number;
  durationMs: number;
  numTurns: number;
}

/**
 * Run `claude -p` as a one-shot subprocess with stream-json output.
 *
 * Each line of stdout is a JSON event (system/init, assistant, user/tool_result, result).
 * The terminal `result` event carries the final response and session stats.
 */
export async function runClaude(opts: ClaudeRunOptions): Promise<ClaudeResult> {
  const args: string[] = [
    '-p',
    '--output-format',
    'stream-json',
    '--verbose',
    '--dangerously-skip-permissions',
    '--model',
    opts.model ?? 'sonnet',
  ];
  if (opts.systemPromptAppend) {
    args.push('--append-system-prompt', opts.systemPromptAppend);
  }
  args.push(opts.prompt);

  const proc = spawn({
    cmd: ['claude', ...args],
    cwd: opts.cwd,
    stdout: 'pipe',
    stderr: 'pipe',
  });

  let finalResult: ClaudeResult | null = null;
  const reader = proc.stdout.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let event: Record<string, unknown>;
      try {
        event = JSON.parse(trimmed);
      } catch {
        continue;
      }
      opts.onEvent?.(event);
      if (event.type === 'result') {
        finalResult = {
          result: String(event.result ?? ''),
          sessionId: String(event.session_id ?? ''),
          costUsd: Number(event.total_cost_usd ?? 0),
          durationMs: Number(event.duration_ms ?? 0),
          numTurns: Number(event.num_turns ?? 0),
        };
      }
    }
  }

  const exit = await proc.exited;
  const stderr = await new Response(proc.stderr).text();
  if (exit !== 0) {
    throw new Error(`claude exited ${exit}\nstderr:\n${stderr}`);
  }
  if (!finalResult) {
    throw new Error(`claude finished without a result event\nstderr:\n${stderr}`);
  }
  return finalResult;
}
