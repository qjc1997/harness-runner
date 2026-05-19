import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { runClaude } from '../claude.ts';
import { formatEvent, projectsDir, promptsDir } from '../paths.ts';

export async function generate(projectName: string): Promise<void> {
  const projectDir = join(projectsDir(), projectName);
  if (!existsSync(projectDir)) {
    throw new Error(`project not found: ${projectDir}\nrun "plan" first.`);
  }
  if (!existsSync(join(projectDir, 'feature_list.json'))) {
    throw new Error(`feature_list.json missing in ${projectDir}\nthe planner shift did not produce it.`);
  }

  const systemPrompt = readFileSync(join(promptsDir(), 'generator.md'), 'utf8');
  const userPrompt = 'Begin your shift. Follow the startup protocol, then implement exactly one feature.';

  console.error(`[generator] project=${projectName} cwd=${projectDir}`);

  const result = await runClaude({
    cwd: projectDir,
    prompt: userPrompt,
    systemPromptAppend: systemPrompt,
    onEvent: (e) => process.stderr.write(formatEvent(e) + '\n'),
  });

  console.error(`\n[generator] done.`);
  console.log(result.result);
}
