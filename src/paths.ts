import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
// src/paths.ts → repo root is one level up
const repoRoot = resolve(here, '..');

export function projectsDir(): string {
  return join(repoRoot, 'projects');
}

export function promptsDir(): string {
  return join(repoRoot, 'prompts');
}

/** One-line summary of a stream-json event from `claude -p --output-format stream-json`. */
export function formatEvent(e: unknown): string {
  if (typeof e !== 'object' || e === null) return '[?]';
  const ev = e as Record<string, unknown>;
  const type = ev.type;

  if (type === 'system' && ev.subtype === 'init') {
    return `[init] session=${ev.session_id} model=${ev.model}`;
  }

  if (type === 'assistant') {
    const msg = ev.message as { content?: Array<Record<string, unknown>> } | undefined;
    const blocks = msg?.content ?? [];
    return blocks
      .map((b) => {
        const bt = b.type as string;
        if (bt === 'text') {
          const t = String(b.text ?? '').replace(/\s+/g, ' ').slice(0, 160);
          return `[text] ${t}`;
        }
        if (bt === 'tool_use') {
          const input = JSON.stringify(b.input ?? {}).slice(0, 140);
          return `[tool] ${b.name}(${input})`;
        }
        if (bt === 'thinking') {
          const t = String(b.thinking ?? '').replace(/\s+/g, ' ').slice(0, 140);
          return `[think] ${t}`;
        }
        return `[${bt}]`;
      })
      .join('\n');
  }

  if (type === 'user') {
    return '[tool_result]';
  }

  if (type === 'result') {
    const cost = (ev.total_cost_usd as number | undefined) ?? 0;
    const turns = (ev.num_turns as number | undefined) ?? 0;
    const dur = (ev.duration_ms as number | undefined) ?? 0;
    return `[result] cost=$${cost.toFixed(4)} turns=${turns} duration=${(dur / 1000).toFixed(1)}s`;
  }

  return `[${String(type)}]`;
}
