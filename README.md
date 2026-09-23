# Workforce Management POC

Forecasts call demand per queue (StatsForecast), converts it to 15-minute staffing
requirements (Erlang-C), and builds agent schedules with Google OR-Tools CP-SAT.
See [PRD.md](PRD.md) for scope and acceptance criteria.

## Stack

- **Backend:** Python 3.14, FastAPI, pandas, StatsForecast, OR-Tools CP-SAT, SQLite (run metadata)
- **Frontend:** React + TypeScript (strict) + Vite, Mantine, TanStack Query, AG Grid Community, Plotly.js
- Runs locally only — single user, no hosted services.

## Setup

Requires Python 3.14 and Node 24.

```bash
make install          # backend venv + pip install, frontend npm install
cp .env.example .env  # optional
```

## Input data

Put the CSV files in `data/` (gitignored):

- `data/agents.csv` — agent roster
- `data/acd_history.csv` — 15-minute ACD history

Paths, timezone and (later) shift templates and work rules live in
`config/wfm.example.yaml`. For local changes, copy it to `config/wfm.yaml` (gitignored)
and set `WFM_CONFIG_PATH=config/wfm.yaml` in `.env`.

## Commands

| Command | What it does |
|---|---|
| `make dev` | Backend on http://127.0.0.1:8000 and frontend on http://localhost:5173 (proxies `/api`) |
| `make lint` | ruff + oxlint |
| `make typecheck` | mypy (strict) + tsc |
| `make test` | pytest + vitest |
| `make check` | lint, typecheck and test |

## Layout

```
backend/src/wfm/   api, config, io, forecast, staffing, schedule, validate, worker, store
backend/tests/     pytest suite; fixtures/ holds synthetic test data
frontend/src/      React app
config/            configuration (demo assumptions labelled)
data/  runs/       local inputs and outputs (gitignored)
```
