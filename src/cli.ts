#!/usr/bin/env bun
import { plan } from './roles/planner.ts';
import { generate } from './roles/generator.ts';

function usage(): never {
  console.error(
    [
      'usage:',
      '  bun src/cli.ts plan <project-name> "<brief>"',
      '  bun src/cli.ts generate <project-name>',
      '  bun src/cli.ts generate-loop <project-name> [n=5]',
    ].join('\n')
  );
  process.exit(1);
}

const [cmd, ...args] = process.argv.slice(2);

if (cmd === 'plan') {
  const [projectName, ...briefParts] = args;
  const brief = briefParts.join(' ').trim();
  if (!projectName || !brief) usage();
  await plan(projectName, brief);
} else if (cmd === 'generate') {
  const [projectName] = args;
  if (!projectName) usage();
  await generate(projectName);
} else if (cmd === 'generate-loop') {
  const [projectName, nStr] = args;
  if (!projectName) usage();
  const n = Number.parseInt(nStr ?? '5', 10);
  if (!Number.isFinite(n) || n <= 0) {
    console.error(`invalid shift count: ${nStr}`);
    process.exit(1);
  }
  for (let i = 1; i <= n; i++) {
    console.error(`\n========== Shift ${i}/${n} ==========\n`);
    await generate(projectName);
  }
} else {
  usage();
}
