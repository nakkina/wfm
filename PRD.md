# Workforce Management POC — Simplified PRD

**Version 0.2 · September 23, 2026**

## 1. Objective and scope

Demonstrate that open-source forecasting and Google OR-Tools CP-SAT can generate a valid agent schedule aligned with forecasted call demand, with a visible improvement over a simple baseline schedule.

**Confirmed:** approximately 1,000 agents across five queues; three months of 15-minute ACD history; daily call-volume and average handle time (AHT) forecasts for the next 42 days. Agents are scheduled **only in their home queue**, even when their skills qualify them for other queues.

**Demo flow:** Load known files → Generate forecast and schedule → Browse BU/MU/Queue → Review queue forecasts and calendars → Inspect an agent’s schedule → Compare baseline and optimized coverage.

Single-user, local/private POC. Assume one operating timezone and daytime shifts initially; confirm these assumptions against the files. Skills and languages are profile attributes and queue eligibility checks, not separate demand forecasts or cross-queue optimization dimensions.

## 2. Inputs: known files, no import UI

Read files from server-side configured paths. A small adapter maps source columns to the logical fields below. No upload wizard, column mapper, agent administration or work-plan editor.

| Input | Minimum information |
|---|---|
| Agent file | Agent ID/name, BU/MU/home queue, skills, languages, work-plan reference, contracted hours; availability/leave if present |
| ACD history file | 15-minute timestamp and timezone, queue ID, offered calls, handled calls, total handle seconds or AHT seconds |
| Small configuration file | File paths, hierarchy labels if absent from agent data, opening hours, timezone, shift templates, work rules, service target, occupancy ceiling and residual shrinkage |

Load on startup. **Reload data** rereads the files and marks previous results stale. Configuration holds paths rather than embedding them in UI code. When work rules or availability are absent, use explicitly labeled demo assumptions; do not infer employment rules from ACD history.

Validate IDs, duplicate intervals, units, negative values, missing intervals, home-queue eligibility and contradictory work rules. Show a simple error/warning panel; block generation on invalid required inputs. Missing data is not zero. Historical AHT is total handle seconds / handled calls; zero handled calls means unavailable AHT. Confirm transfer and queue-arrival definitions with the source adapter.

## 3. UI: one workspace and two detail windows

### Main workspace

- **Left pane:** expandable Business Unit → Management Unit → Queue hierarchy, with assigned-agent counts.
- **Right pane:** searchable assigned-agent table for the selected queue: name/ID, skills, languages, work plan and weekly contracted hours.
- **Top bar:** Reload data; Generate forecast & schedule; run status; last completed time; six-week date range.
- Selecting a BU or MU shows descendant agents with a queue column.
- Clicking a queue selects it, updates the agent table behind the detail window, and opens its queue window. Closing the window returns to that queue’s agents.
- Clicking an agent in a list or calendar opens their individual schedule.

```text
[Reload data] [Generate forecast & schedule]   Status: Complete
-----------------------------------------------------------------
Organization              | Assigned agents — Billing
▾ Business Unit A         | Search agents...
  ▾ Management Unit 1     | Name       Skills       Language  Plan
    • Billing             | Agent 01   Billing      English   FT
    • Support             | Agent 02   Billing      Spanish   FT
  ▾ Management Unit 2     | Agent 03   Billing      English   PT
    • Retention           |
```

### Queue detail window

Large modal with hierarchy breadcrumb, queue name and **two tabs**:

| Tab | Content and interactions |
|---|---|
| **Forecast** | Historical and next-42-day daily graph; Call volume / AHT toggle; clear forecast-start marker. Select a day to see its 15-minute call profile and required headcount. Show method, holdout error and data-quality warnings. |
| **Agent schedules** | Weekly calendar with agents as rows and seven days as columns; shift times in each cell; navigation across six weeks. Selecting a day opens the work/break/meal timeline at 15-minute resolution. Clicking an agent opens their calendar. |

The schedule tab includes a **Baseline / Optimized** toggle, required-versus-scheduled productive-agent chart, and cards for uncovered agent-hours, paid hours and hard-rule violations. Highlight shortage intervals using labels as well as color. Virtualize agent rows for large queues.

### Agent detail window

Show agent identity, home queue, skills, languages and work plan above an individual weekly calendar with six-week navigation. Selecting a day shows shift start/end, breaks and meals. Include weekly paid hours, days off and validation status. Read-only for the POC.

**Required states:** not generated, running, completed, completed with shortages, failed, and stale after reload. Keep the last successful result visible during regeneration. Display run progress by queue/week. Never label a partial result as a complete six-week schedule.

## 4. Forecast-to-schedule pipeline

### A. Forecast daily demand per queue

Use **StatsForecast** with weekly SeasonalNaive and AutoETS for call volume; compare with the recent same-weekday average. Choose using historical testing, retaining the baseline when the more complex candidate does not improve. Defer MLForecast, Prophet comparisons and reconciliation libraries.

Forecast AHT using a recent handled-call-weighted estimate by weekday, falling back to the queue’s weighted average when sparse. This is a deliberately simple, transparent AHT forecast. Generate 42 daily volume/AHT points per queue. Do not learn annual seasonality from three months of history.

For exactly 90 days, train on the first 48 and test on the last 42; show volume WAPE/bias and handled-call-weighted AHT MAE by forecast week. This single holdout offers limited evidence; do not claim robust six-week accuracy or calibrated confidence bands. Fit all backtest features and intraday profiles on training data only, then refit on full history for future forecasts. Use MAE where all-zero actual volume makes WAPE undefined.

### B. Translate forecasts into 15-minute staffing requirements

1. Learn intraday call shares by queue and weekday from history; normalize within opening hours.
2. Distribute each daily call forecast across 15-minute intervals. Hold daily AHT constant within the day initially.
3. Calculate offered load: **interval calls × AHT seconds / 900**.
4. Use a tested **Erlang-C** calculation to obtain productive headcount meeting configured service and occupancy targets. Example demo assumptions: 80% answered within 20 seconds and maximum occupancy of 85%.
5. Account for residual unplanned shrinkage with `ceil(net_required / (1 - shrinkage))`. Explicitly scheduled breaks, meals and known leave are excluded from that percentage to prevent double-counting.

Daily calls must equal the sum of their interval allocations. CP-SAT consumes these headcount requirements; it does not calculate them. Erlang C ignores abandonment and assumes a simplified stationary queue; label service levels as modeled estimates, not guaranteed operational results.

### C. Optimize named-agent schedules

Use **Python OR-Tools CP-SAT** to select among a bounded set of predefined shift candidates with valid break/meal patterns. Each agent can receive at most one shift per day, in their home queue only.

| Hard rules: always enforced | Optimization priorities |
|---|---|
| Home queue and mandatory queue skill/language eligibility | First minimize understaffed agent-minutes |
| Availability, known leave and no overlaps | Then reduce excess coverage and paid hours within contract rules |
| Contracted paid-hour bounds | Keep the POC focused; defer preferences and fairness objectives |
| Minimum rest and maximum consecutive workdays | |
| Required breaks/meals and their permitted windows | |

Use configured rule values; sample work plans are demo assumptions, not legal defaults. Breaks and meals remove productive coverage for their exact intervals; paid breaks still count toward paid hours.

Solve each queue independently, in rolling weeks, carrying rest/consecutive-day state and contractual-hour accounting across boundaries. Include carry-in schedule history if available; otherwise clearly assume agents are rested at the start. Validate the entire 42-day schedule afterward. Handle partial contract weeks explicitly if the forecast start is midweek.

Use binary agent/day/shift-candidate variables with precomputed coverage coefficients; avoid unnecessary agent × interval × queue variables. Apply a total solve-time budget. Return feasible solutions when available, distinguish FEASIBLE from OPTIMAL, and do not label timeout-without-solution as INFEASIBLE. Weekly decomposition does not prove global six-week optimality.

Demand shortfalls are allowed and reported; hard-rule violations are not. A separate validator checks every schedule before it is shown as valid. If rules conflict, show an actionable error rather than silently relaxing them.

## 5. Evidence that optimization helps

Build a deterministic **first-fit baseline schedule** using the same roster, allowed shift patterns, availability and hard rules, without optimizing against forecast demand. If it cannot produce a valid baseline, label the comparison unavailable; do not compare against an invalid schedule.

Evaluate both schedules against identical forecast-driven requirements and the same paid-hour budget/caps. Show paid hours alongside coverage so extra staffing does not masquerade as optimization.

**Uncovered agent-hours = sum(max(required − scheduled productive agents, 0)) × 0.25.** Calculate overcoverage similarly.

| Acceptance criterion | Evidence shown |
|---|---|
| Zero hard violations | Independent validation report for every valid schedule |
| Better demand alignment | Lower uncovered agent-hours on a representative fixture with room to improve; actual change, no promised percentage |
| Honest forecasting | Holdout error versus weekly baseline; baseline fallback if needed |
| Correct accounting | Daily/interval call totals agree; no overlapping agent capacity; breaks and meals accounted for |
| Complete demonstration | 1,000 agents scheduled over 42 days; all queue and agent views usable |
| Practical runtime | Record runtime/memory; provisional full-run target of 30 minutes on an 8-vCPU/16-GB test host, subject to measurement |

Forecast accuracy and schedule optimization quality are separate outcomes. Matching predicted demand does not prove future service-level achievement. Add an intentionally understaffed fixture to demonstrate honest shortage reporting.

**Demo script:** generate → select queue → inspect daily demand and interval peaks → switch Baseline to Optimized → show coverage and equal-budget comparison → inspect one agent’s valid schedule.

## 6. Minimal technical stack

| Component | Recommendation |
|---|---|
| UI | React + TypeScript + Vite; Plotly.js graphs; simple custom weekly calendar and virtualized agent table |
| Backend | FastAPI; pandas for file loading and aggregation |
| Forecasting | StatsForecast plus weighted AHT baseline |
| Staffing | Small tested Python Erlang-C module |
| Optimization | Google OR-Tools CP-SAT |
| Storage | Existing input files; output CSV/JSON by run ID; SQLite for run metadata/status |
| Execution | One background worker subprocess, one generation job at a time; UI polls status |

No PostgreSQL, Celery, RabbitMQ, paid forecasting APIs or hosted optimization services. The worker writes a temporary run directory, validates results, then exposes the completed run. Record file hashes, configuration, package versions, model/solver status and runtime. Persist failures and mark interrupted runs on restart. Keep agent data in the configured local/private environment.

Minimal API: hierarchy/agents, reload data, start run, run status, queue forecast, queue schedule and agent schedule. The single **Generate forecast & schedule** action runs the complete pipeline.

## 7. Build order and exclusions

1. Read known files; build hierarchy, assigned-agent list and both detail windows using fixture results.
2. Implement forecasts, holdout metrics and interval staffing requirements.
3. Connect CP-SAT, baseline comparison and independent validator; populate calendars.
4. Test normal demand, sparse history, missing intervals, shortages, conflicting rules, week boundaries and 1,000-agent scale.

**Excluded:** upload UI, editable work plans, forecast overrides, manual schedule editing, approvals/publishing, agent self-service, live ACD connectors, cross-queue deployment and multi-skill routing simulation.

**Before implementation:** obtain exact file paths/schema and confirm timezone/opening hours, shift templates and missing availability assumptions. Home-queue-only scheduling is confirmed. These remaining details need not delay the UI shell.

## Research references retained from the original PRD

- [StatsForecast](https://github.com/Nixtla/statsforecast): forecasting models and license.
- [OR-Tools employee scheduling](https://developers.google.com/optimization/scheduling/employee_scheduling) and [CP-SAT statuses](https://developers.google.com/optimization/cp/cp_solver): model and result interpretation.
- [Call-center queueing and workforce-planning tutorial](https://columbia.edu/~ww2040/tutorial.pdf): staffing calculations and assumptions.
- [Amazon Connect staffing rules](https://docs.aws.amazon.com/connect/latest/adminguide/scheduling-create-staffing-groups.html): working-hours, rest and consecutive-day rule categories.

Scope, architecture and performance targets are proposed POC decisions, not measured results.
