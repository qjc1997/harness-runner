"""Grill new requirements before planning (asynchronous owner interview).

`harness-runner refine <project> "<requirements>"` first runs a Griller shift.
If it finds open decisions, the questions go to NEEDS_HUMAN.md (the owner is
notified, exit code 3) and the requirements are parked in
.harness/grill/pending.json. The owner answers with `harness-runner resolve`,
which attaches the answers to the open round; `refine <project> --continue`
then runs the next round. When a round has no questions, the Refiner plans with
the requirements plus every question and answer.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..backends import get_backend
from ..paths import prompts_dir
from ..stuck import EXIT_NEEDS_HUMAN, NEEDS_HUMAN_FILE, STATE_DIR, escalate

PENDING = Path(STATE_DIR) / "grill" / "pending.json"
MAX_ROUNDS = 4


def pending_path(project_dir: Path) -> Path:
    return project_dir / PENDING


def load_pending(project_dir: Path) -> Optional[dict]:
    p = pending_path(project_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_pending(project_dir: Path, state: dict) -> None:
    p = pending_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def attach_answers(project_dir: Path, answers: str) -> bool:
    """Called by `resolve`: record the owner's answers on the open grill round."""
    state = load_pending(project_dir)
    if not state or not state["rounds"] or state["rounds"][-1].get("answers"):
        return False
    state["rounds"][-1]["answers"] = answers.strip()
    save_pending(project_dir, state)
    return True


def parse_result(text: str) -> Tuple[str, str]:
    """Return ("questions"|"none"|"invalid", body)."""
    m = re.search(r"GRILL_RESULT:\s*(QUESTIONS|NO_QUESTIONS)\s*\n?(.*)", text or "", re.S)
    if not m:
        return "invalid", (text or "").strip()
    kind = "questions" if m.group(1) == "QUESTIONS" else "none"
    body = m.group(2).strip().strip("`").strip()
    if kind == "questions" and not re.search(r"^###\s*Q\d+", body, re.M):
        return "invalid", body
    return kind, body


def transcript(state: dict) -> str:
    """Earlier rounds as Markdown, for the next Griller round and for the Refiner."""
    parts: List[str] = []
    for i, r in enumerate(state["rounds"], 1):
        parts.append(f"## Grill round {i} — questions\n\n{r['questions']}\n\n## Grill round {i} — owner answers\n\n{r.get('answers') or '(unanswered)'}")
    return "\n\n".join(parts)


def run_griller(project_dir: Path, requirements: str, state: Optional[dict]) -> Tuple[str, str, float]:
    system = (prompts_dir() / "griller.md").read_text(encoding="utf-8")
    prompt = f"# New requirements\n\n{requirements.strip()}\n"
    if state and state["rounds"]:
        prompt += f"\n# Earlier rounds (settled)\n\n{transcript(state)}\n"
    prompt += "\nRead the project files you need, then output GRILL_RESULT."
    result = get_backend().run(cwd=str(project_dir), prompt=prompt, system_prompt_append=system,
                               on_event=lambda e: None)
    kind, body = parse_result(result.result or "")
    return kind, body, result.cost_usd or 0.0


def grill_then_refine(project_name: str, project_dir: Path, requirements: Optional[str],
                      cont: bool, refine_fn) -> int:
    """Run one grill round; plan when nothing is open. Returns the process exit code."""
    state = load_pending(project_dir)
    if cont:
        if not state:
            print("[grill] nothing pending; run refine with requirements", file=sys.stderr)
            return 1
        if state["rounds"] and not state["rounds"][-1].get("answers"):
            escalate(project_dir, project_name, "grill questions are still unanswered", state["rounds"][-1]["questions"])
            return EXIT_NEEDS_HUMAN
        requirements = state["requirements"]
    else:
        if state:
            print("[grill] earlier requirements are still being grilled; answer them and run `refine --continue`, "
                  "or delete .harness/grill/pending.json to drop them", file=sys.stderr)
            return 1
        state = {"requirements": requirements, "started": datetime.now().isoformat(timespec="seconds"), "rounds": []}

    if len(state["rounds"]) >= MAX_ROUNDS:
        print(f"[grill] {MAX_ROUNDS} rounds reached; planning with the answers so far", file=sys.stderr)
        kind, body = "none", ""
    else:
        print(f"\n========== Grill round {len(state['rounds']) + 1} ==========\n", file=sys.stderr)
        kind, body, cost = run_griller(project_dir, requirements, state)
        print(f"[grill] result={kind} cost=${cost:.3f}", file=sys.stderr)
        if kind == "invalid":
            print("[grill] griller output had no GRILL_RESULT block; not planning", file=sys.stderr)
            return 1

    if kind == "questions":
        state["rounds"].append({"questions": body})
        save_pending(project_dir, state)
        (project_dir / NEEDS_HUMAN_FILE).write_text(
            "# Needs owner decision — planning questions\n\n"
            f"**Requirements being planned:** {requirements.strip()[:400]}\n\n{body}\n\n"
            f"Answer with `harness-runner resolve {project_name} \"Q1: …; Q2: …\"`, "
            f"then run `harness-runner refine {project_name} --continue`.\n",
            encoding="utf-8")
        escalate(project_dir, project_name, f"grill round {len(state['rounds'])}: planning questions", body)
        return EXIT_NEEDS_HUMAN

    full = requirements.strip()
    if state["rounds"]:
        full += "\n\n# Owner decisions from grilling (binding)\n\n" + transcript(state)
    archive = project_dir / STATE_DIR / "grill" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-done.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending_path(project_dir).unlink(missing_ok=True)
    if body:
        print(f"[grill] no open questions — assuming: {body[:300]}", file=sys.stderr)
    refine_fn(project_name, full)
    return 0
