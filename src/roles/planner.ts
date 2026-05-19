import { execSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { runClaude } from '../claude.ts';
import { formatEvent, projectsDir, promptsDir } from '../paths.ts';

export async function plan(projectName: string, brief: string): Promise<void> {
  const projectDir = join(projectsDir(), projectName);

  if (existsSync(projectDir)) {
    throw new Error(`project already exists: ${projectDir}\nremove it first if you want to re-plan.`);
  }
  mkdirSync(projectDir, { recursive: true });

  // Empty git repo so the Generator's first shift can commit cleanly.
  execSync('git init -q', { cwd: projectDir });
  execSync('git commit -q --allow-empty -m "harness: project scaffold"', {
    cwd: projectDir,
    env: {
      ...process.env,
      GIT_AUTHOR_NAME: 'harness-runner',
      GIT_AUTHOR_EMAIL: 'harness@local',
      GIT_COMMITTER_NAME: 'harness-runner',
      GIT_COMMITTER_EMAIL: 'harness@local',
    },
  });

  const systemPrompt = readFileSync(join(promptsDir(), 'planner.md'), 'utf8');
  const userPrompt = `# Product brief\n\n${brief}\n\nProduce feature_list.json, init.sh, and claude-progress.txt now.`;

  console.error(`[planner] project=${projectName} cwd=${projectDir}`);

  const result = await runClaude({
    cwd: projectDir,
    prompt: userPrompt,
    systemPromptAppend: systemPrompt,
    onEvent: (e) => process.stderr.write(formatEvent(e) + '\n'),
  });

  // Commit whatever the planner produced.
  try {
    execSync('git add -A', { cwd: projectDir });
    execSync('git commit -q -m "harness: planner output"', {
      cwd: projectDir,
      env: {
        ...process.env,
        GIT_AUTHOR_NAME: 'harness-runner',
        GIT_AUTHOR_EMAIL: 'harness@local',
        GIT_COMMITTER_NAME: 'harness-runner',
        GIT_COMMITTER_EMAIL: 'harness@local',
      },
    });
  } catch {
    // No changes to commit — planner produced nothing. The caller will see this from the result.
  }

  console.error(`\n[planner] done.`);
  console.log(result.result);
}
