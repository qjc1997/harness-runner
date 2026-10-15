"""Audit of test changes made during a Generator shift.

Generators may update an existing test when an owner-approved requirement
changed the expected behaviour (see prompts/generator.md "Test changes"),
but must never weaken tests to make their own code pass. After each shift:

  1. Deterministic checks on every commit of the shift that MODIFIES an
     existing test file:
       - it must be a `test-update:` commit that cites the requirement
         (a feature id like f297, or "Requirement:" in the message);
       - assertion count in the file must not drop;
       - no new weakening patterns (`|| true`, skips, `.only`, empty catch).
  2. An independent LLM review of each `test-update:` commit: does the cited
     requirement really demand this change, and is coverage preserved?

Any problem → NEEDS_HUMAN.md (the loop then stops and notifies the owner).
Never raises: an auditor crash must not break the shift's bookkeeping.
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .paths import prompts_dir
from .stuck import NEEDS_HUMAN_FILE

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|_test\.(go|py)$|(^|/)test_[^/]+\.py$|\.(test|spec)\.[jt]sx?$|(^|/)scripts/check-[^/]+\.(m?js|sh|py)$"
)
_ASSERTION = re.compile(
    r"\bt\.(Fatal|Fatalf|Error|Errorf)\(|\b(assert\w*|expect|check|require\.\w+)\s*\(|self\.assert\w+\(|\bfail\(|\bFAIL\b"
)
# Lines that state an expectation: assertions, plus conditions/comparisons that guard them
# (Go's `if got != 2 { t.Fatal(...) }` keeps the expected value on the if line).
_EXPECTATION = re.compile(_ASSERTION.pattern + r"|^\s*(if|elif|while)\b.*(==|!=|<=|>=|<|>)|===|!==|\b(toBe|toEqual|toContain|toMatch)\(")

_WEAKENING = [
    (re.compile(r"\|\|\s*true\b"), "'|| true' makes a check always pass"),
    (re.compile(r"\bt\.Skip(f|Now)?\("), "test skipped"),
    (re.compile(r"\b(it|test|describe)\.skip\(|\bxit\(|\bxdescribe\("), "test skipped"),
    (re.compile(r"\b(it|test|describe)\.only\("), ".only() silently disables other tests"),
    (re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*\}"), "empty catch swallows failures"),
    (re.compile(r"@unittest\.skip|pytest\.mark\.skip"), "test skipped"),
]
_CITATION = re.compile(r"\bf\d{3,}\b|Requirement:|Owner decision|app_spec\.md|docs/[\w./-]+\.md", re.I)


@dataclass
class AuditIssue:
    commit: str
    path: str
    problem: str


@dataclass
class AuditResult:
    issues: List[AuditIssue] = field(default_factory=list)
    reviewed_commits: int = 0
    cost_usd: float = 0.0
    review_commits: set = field(default_factory=set)  # non-test-update edits that need LLM review


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def _git(project_dir: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=project_dir, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _show(project_dir: Path, rev: str, path: str) -> Optional[str]:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=project_dir, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _count_assertions(text: str) -> int:
    return sum(1 for ln in text.splitlines() if _ASSERTION.search(ln))


def _shift_commits(project_dir: Path, sha_before: str) -> List[str]:
    if not sha_before:
        return []
    return [c for c in _git(project_dir, "rev-list", "--reverse", f"{sha_before}..HEAD").split() if c]


def deterministic_audit(project_dir: Path, sha_before: str) -> AuditResult:
    """Check every commit of the shift that modifies an existing test file."""
    result = AuditResult()
    created: set = set()  # test files added during this shift are new work, not legacy edits
    for c in _shift_commits(project_dir, sha_before):
        message = _git(project_dir, "log", "-1", "--format=%B", c)
        short = c[:8]
        changed = [ln.split("\t") for ln in _git(project_dir, "diff-tree", "--no-commit-id", "-r", "--name-status", c).splitlines()]
        for parts in changed:
            status, path = parts[0], parts[-1]
            if not is_test_path(path) or path in created:
                continue
            if status.startswith("A"):
                created.add(path)  # new test files are always welcome
                continue
            if status.startswith("D"):
                result.issues.append(AuditIssue(short, path, "an existing test file was deleted"))
                continue
            patch = _git(project_dir, "show", "--format=", c, "--", path).splitlines()
            added = "\n".join(ln[1:] for ln in patch if ln.startswith("+") and not ln.startswith("+++"))
            removed = "\n".join(ln[1:] for ln in patch if ln.startswith("-") and not ln.startswith("---"))
            touches_assertions = any(_EXPECTATION.search(ln) for ln in (added + "\n" + removed).splitlines())
            if message.lower().startswith("test-update:"):
                if not _CITATION.search(message):
                    result.issues.append(AuditIssue(short, path, "test-update commit does not cite the requirement (feature id or 'Requirement:')"))
            elif touches_assertions:
                result.issues.append(AuditIssue(short, path, "existing test assertions edited outside a 'test-update:' commit"))
            else:
                result.review_commits.add(c)  # paths/setup/comments only: independent review, not a hard stop
            old, new = _show(project_dir, f"{c}^", path) or "", _show(project_dir, c, path) or ""
            if _count_assertions(new) < _count_assertions(old):
                result.issues.append(AuditIssue(short, path, f"assertions dropped from {_count_assertions(old)} to {_count_assertions(new)}"))
            for pat, why in _WEAKENING:
                if len(pat.findall(added)) > len(pat.findall(removed)):
                    result.issues.append(AuditIssue(short, path, why))
    return result


def _parse_verdict(text: str) -> Optional[dict]:
    m = re.search(r"TEST_AUDIT_JSON\s*```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{[^{}]*\"justified\"[^{}]*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def llm_audit(project_dir: Path, sha_before: str, result: AuditResult) -> AuditResult:
    """Independent review of each test-update commit (model: HARNESS_TEST_AUDIT_MODEL, default sonnet)."""
    from .backends import get_backend

    system = (prompts_dir() / "test_auditor.md").read_text(encoding="utf-8")
    features = {}
    fl = project_dir / "feature_list.json"
    if fl.exists():
        try:
            features = {f["id"]: f for f in json.loads(fl.read_text(encoding="utf-8")) if isinstance(f, dict) and "id" in f}
        except ValueError:
            pass
    for c in _shift_commits(project_dir, sha_before):
        message = _git(project_dir, "log", "-1", "--format=%B", c)
        if not message.lower().startswith("test-update:") and c not in result.review_commits:
            continue
        cited = {fid: features[fid] for fid in re.findall(r"\bf\d{3,}\b", message) if fid in features}
        diff = _git(project_dir, "show", "--format=", c)[:20000]
        prompt = (
            f"# Commit message\n\n{message}\n\n# Cited features (owner-approved requirements)\n\n"
            f"```json\n{json.dumps(cited, ensure_ascii=False, indent=1)[:8000]}\n```\n\n"
            f"# Diff of the test change\n\n```diff\n{diff}\n```\n\n"
            "Judge this test change and output TEST_AUDIT_JSON."
        )
        result.reviewed_commits += 1
        try:
            run = get_backend().run(cwd=str(project_dir), prompt=prompt, system_prompt_append=system,
                                    model=os.environ.get("HARNESS_TEST_AUDIT_MODEL", "sonnet"), on_event=None)
        except Exception as e:
            result.issues.append(AuditIssue(c[:8], "-", f"test-change auditor could not run: {str(e).splitlines()[0] if str(e) else e!r}"))
            continue
        result.cost_usd += run.cost_usd or 0.0
        verdict = _parse_verdict(run.result or "")
        if verdict is None:
            result.issues.append(AuditIssue(c[:8], "-", "test-change auditor returned no verdict"))
        elif not (verdict.get("justified") and verdict.get("coverage_preserved")):
            result.issues.append(AuditIssue(c[:8], ", ".join(verdict.get("files", [])) or "-", f"auditor: {verdict.get('reason', 'not justified')}"))
    return result


def audit_shift(project_dir: Path, sha_before: str, *, use_llm: bool = True) -> AuditResult:
    """Run both audits; on any issue write NEEDS_HUMAN.md (unless one exists). Never raises."""
    try:
        result = deterministic_audit(project_dir, sha_before)
        if use_llm and os.environ.get("HARNESS_TEST_AUDIT_LLM", "1") != "0":
            result = llm_audit(project_dir, sha_before, result)
    except Exception as e:
        print(f"[test_audit] unexpected error (skipping): {e}", file=sys.stderr)
        return AuditResult()
    if result.issues:
        for i in result.issues:
            print(f"[test_audit] ✗ {i.commit} {i.path}: {i.problem}", file=sys.stderr)
        _escalate(project_dir, result)
    elif result.reviewed_commits:
        print(f"[test_audit] ✓ {result.reviewed_commits} test-update commit(s) justified", file=sys.stderr)
    return result


def _escalate(project_dir: Path, result: AuditResult) -> None:
    f = project_dir / NEEDS_HUMAN_FILE
    if f.exists():
        return
    commits = sorted({i.commit for i in result.issues if i.commit != "-"})
    lines = [
        "# Needs owner decision",
        "**Blocked:** test changes made in the last shift failed the test-change audit.",
        "**Question:** Keep these test changes, or revert them?",
        "**Findings:**",
        *[f"- `{i.commit}` {i.path}: {i.problem}" for i in result.issues],
        "**Options:**",
        f"- A) Revert: `git revert --no-edit {' '.join(commits)}` and have the next shift fix the code instead  ← recommended",
        "- B) Keep: the changes are correct; say why in the decision so the next shift records it",
    ]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
