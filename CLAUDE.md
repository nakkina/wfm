# CLAUDE.md — WFM POC

## Source of truth
- `PRD.md` defines scope, pipeline, acceptance criteria and build order — follow its build order (section 7)
- Anything outside the PRD's scope or in its exclusion list needs my approval

## Mode: prototype
Global rules apply, with these relaxations for this project:
- Plan briefly in chat; `PLAN.md` only for multi-step phases (one per PRD build step)
- I approve a dependency list once per phase; no per-package confirmation after that
- Committing on `main` is fine; `/review` before commits is optional
- Tests required for core logic: Erlang-C, interval allocation, validator, week-boundary state
- Skip superpowers brainstorming/TDD unless I ask
- Unchanged: Security rules, confirm before push/deploy, never delete files without asking

## Stack (fixed by the PRD — don't substitute)
- **Backend:** Python 3.14, FastAPI, pandas, StatsForecast, OR-Tools CP-SAT, SQLite for run metadata
- **Frontend:** React + TypeScript (strict) + Vite, Mantine, TanStack Query, AG Grid Community, Plotly.js
- **Schedule UI:** AG Grid week grid (agents × days) + custom SVG day timeline — no calendar library
- No PostgreSQL, Celery/Redis, paid APIs or hosted services; runs locally only

## Commands
- `make dev` — backend :8000 + frontend :5173
- `make check` — lint + typecheck + test (run before claiming done)

## Data
- Input CSVs live in `data/` (gitignored); config in `config/wfm.example.yaml`
- Unit tests use synthetic data in `backend/tests/fixtures/`, never files from `data/`
- Missing data is not zero; label demo assumptions explicitly (PRD §2)

## Scheduling model conventions
- Binary agent × day × shift-candidate variables with precomputed coverage — no agent × interval variables
- Report CP-SAT status honestly: OPTIMAL vs FEASIBLE; timeout without solution is not INFEASIBLE
- The validator is independent of the optimizer — never reuse optimizer constraint code in it
