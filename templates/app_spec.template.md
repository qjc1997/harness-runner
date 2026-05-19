# Application Specification

> This file is read by the Planner at `harness-runner plan` time. Edit it before
> planning. After planning, the Generator can still read it to understand
> background, but `feature_list.json` becomes the binding contract for what's
> in scope.

## One-line description

<What this app is in one sentence.>

## Target users

<Who uses it, what they need to accomplish.>

## Core capabilities

<Bulleted list of major capabilities. The Planner will expand each into multiple
testable items in feature_list.json — do NOT pre-decompose to fine-grained tasks
here. Keep this at the level "User can do X" / "App can do Y".>

-
-

## Technology stack

<Override defaults only if needed. Defaults the Planner will pick:
  Frontend: React + Vite + TypeScript
  Backend: Python 3.11+ + FastAPI + uvicorn
  Database: SQLite via aiosqlite (DuckDB if SQL-on-files is part of the product)
  Tests: Playwright for E2E (browser) + pytest for backend units>

- Frontend:
- Backend:
- Database:
- Other:

## Design preferences

<Colors, typography, layout style, any specific visual requirements. Leave blank
if no strong opinion — the Planner will pick reasonable defaults.>

## Non-goals

<Explicitly out of scope. Helpful to keep the Planner from feature-creeping.>

-

## Success criteria

<How do we know v1 is "done"? E.g. "User can complete round-trip from X to Y",
"feature_list shows 80% passing on a fresh environment".>
