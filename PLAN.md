# PLAN — PRD build step 3: staffing requirements, CP-SAT scheduling, agent shift plans

Pipeline: saved daily queue forecast → 15-minute interval demand → Erlang C requirements →
CP-SAT per queue (3 sequential stages) → independent validation → persisted shift plans → UI.

## Inputs (existing data first)
| Need | Source | Default when missing |
|---|---|---|
| Daily calls + AHT per queue | Latest saved forecast run (`runs/forecasts/`, 21 days from 2026-09-23) | — (required) |
| Intraday profile | Learned from hourly ACD history per queue × weekday × hour, event days excluded, normalised within open hours | none — no uniform-day assumption |
| Within-hour split (hour → 15 min) | Configurable proportions | equal across the *open* minutes of the hour (20:00 hour → 20:00, 20:15 only) |
| Opening hours, required skill | Agents workbook `Queues` sheet (08:00–20:30 daily) | — |
| Roster, work plans, rules | `Agents` sheet: weekly min/max hours, allowed/preferred shift, available days, preferred days off, rest 11 h, max 5 consecutive days | — |
| Shift templates, break offsets | `Shifts` sheet (s1/s2/s3 per plan; breaks at +120, +375 min, meal +240) | placement windows from the request if offsets are missing |
| Intraday availability windows | not in roster | whole day (documented) |
| Known leave | optional `leave_csv` in config | none (no leave simulated in the roster) |
| Prior schedule history | not available | agents start rested (documented) |

AHT is held constant within a day (PRD §4B); interval calls are estimated allocations (labelled).

## Staffing (per queue, per 15-minute interval)
Workload = calls × AHT; Erlangs = workload / 900 s. Erlang C via the stable Erlang B recurrence;
smallest N > A meeting SL ≥ target and A/N ≤ max occupancy. On-phone requirement =
ceil(N / (1 − residual shrinkage)). Defaults 80/20, 85 %, 10 % (config + per-run override).
Closing work: calls in progress at close need `ceil(A_last)` agents for `closing_minutes` (15)
after close, emitted as separate `closing` intervals. No template extends past 20:30, so this is
reported as shortage rather than silently dropped.

## CP-SAT model (one model per queue; agents only in home queue)
x[agent, date, pattern] Booleans over precomputed patterns (template × allowed break placements;
default = the Shifts-sheet offsets exactly, `break_stagger_slots` widens within windows).
Hard: eligibility (active, schedulable, required skill), available days, leave, allowed shifts,
≤1 shift/day, weekly paid hours within min/max per Mon–Sun week, rest ≥ 11 h (UTC durations,
DST-safe), ≤ 5 consecutive days (sliding 6-day windows across week boundaries).
Coverage: scheduled + shortage ≥ required; excess tracked separately.
Partial weeks: bounds pro-rated by the agent's available days inside the horizon week,
min rounded down / max rounded up to whole shifts, never above the weekly max.
Paid leave credits contracted daily hours (weekly_min / workdays_per_week) toward the week.
Stages: (1) min shortage agent-minutes → (2) with shortage ≤ stage-1 result, min excess
agent-minutes + paid minutes → (3) with stage-2 ≤ result, min preference/fairness penalties
(preferred shift, preferred days off, weekend and late-shift spread within employment type,
shift-start changes). Each stage has a time limit; FEASIBLE results bound the next stage and
are never reported as optimal. No names, demographics or performance scores are used.

## Outputs (runs/schedules/<run_id>/)
requirements.csv, coverage.csv, shifts.csv (every agent × date: Working/Off/Leave),
activities.csv (On Phone / Paid Break / Unpaid Meal / Leave; contiguous, tz-aware),
validation.json, meta.json (config, per-queue per-stage status, objective, bound, timings).
Runs execute in a worker subprocess; status.json is polled by the UI.

## UI
Header: Generate Schedule (config form), progress, completion/error. Queue → new **Schedule** tab:
summary cards, required-vs-scheduled chart with labelled shortages, weekly agent grid, daily
activity timeline with legend, CSV exports. Agent pop-up uses the real plan (prev/next week),
falling back to the labelled mock only when no run exists. BU/MU/org selection shows rolled-up totals.

## Acceptance (Definition of Done) — status on real data, run 20260924T032307
- [x] Erlang reference: 120 calls/h, AHT 360 → A=12, N=16, SL≈83.62 %, occ 75 %, on-phone 18 (test_staffing)
- [x] Interval allocation preserves daily totals (max error 0.0 calls on real data); zero demand → zero; invalid inputs rejected
- [x] Pattern paid/unpaid accounting, break placement, DST-safe durations (test_schedule_model)
- [x] Hard constraints pass an independent validator on the real roster and forecasts (12/12 checks, 0 violations)
- [x] Every agent has a status every day (1,000 × 21); every working shift has a complete timeline
- [x] Shortages visible (11,658 agent-h incl. 557 closing); queue totals reconcile to MU/BU/org
- [x] Queue calendar and agent calendar render; CSV export works (browser-checked)
- [x] Report solve time (480 s), statuses (27 Feasible / 3 Optimal; stage 1 optimal in all 30), objective/bound, shortages, conflicts (0)

## Findings
- Most shortage comes from fixed shift starts (08/10/12) and Shifts-sheet break times: every agent on a
  shift breaks together. Break stagger ±15 min cut Q-CB-CAP-05 shortage 475 → 399 h (−16 %) but makes
  the model ~24× larger (stage 1 no longer proven optimal in 60 s; ~78 min for all queues).
- Weekly demand (~35.7k on-phone h) slightly exceeds roster capacity (~34.3k h), so some shortage is
  unavoidable with 40/20 h contracts and no overtime.
- Closing work after 20:30 can't be covered by any template (latest shift ends 20:30): reported, not hidden.
