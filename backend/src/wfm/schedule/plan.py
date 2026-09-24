"""Turns queue solutions into persisted shift plans, activity timelines and coverage tables.

Every agent gets one row per horizon date: Working, Off, Leave, or Unscheduled (ineligible
agent or unsolved queue — with the reason in `note`). Working shifts are split into contiguous
activities that fully explain the shift. Residual shrinkage is a planning allowance and never
appears as an activity or on any agent.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from wfm.io.agents import ShiftTemplate
from wfm.schedule.inputs import WEEKDAYS, AgentSpec, Exclusion
from wfm.schedule.model import QueueSolution

SHIFT_COLUMNS = [
    "run_id",
    "agent_id",
    "agent_name",
    "bu_id",
    "mu_id",
    "queue_id",
    "work_date",
    "timezone",
    "status",
    "shift_code",
    "shift_name",
    "shift_start",
    "shift_end",
    "paid_minutes",
    "unpaid_minutes",
    "preferred_shift_matched",
    "preferred_day_off",
    "note",
]
ACTIVITY_COLUMNS = [
    "run_id",
    "agent_id",
    "work_date",
    "activity_type",
    "start",
    "end",
    "minutes",
    "paid",
    "queue_id",
]
COVERAGE_COLUMNS = [
    "queue_id",
    "interval_start",
    "interval_end",
    "interval_minutes",
    "kind",
    "required_on_phone",
    "scheduled_on_phone",
    "shortage",
    "excess",
]


def build_plan(
    run_id: str,
    days: list[date],
    agents: list[AgentSpec],
    excluded: list[Exclusion],
    roster: pd.DataFrame,
    solutions: dict[str, QueueSolution],
    templates: list[ShiftTemplate],
    timezones: dict[str, str],
    requirements: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    shifts: list[dict[str, object]] = []
    activities: list[dict[str, object]] = []
    by_plan = {(t.work_plan_id, t.shift_code): t for t in templates}

    for a in agents:
        tz_name = timezones[a.queue_id]
        tz = ZoneInfo(tz_name)
        solution = solutions.get(a.queue_id)
        solved = solution is not None and solution.status in ("Optimal", "Feasible")
        for d in days:
            base = _base(run_id, a.agent_id, a.agent_name, a.bu_id, a.mu_id, a.queue_id, d, tz_name)
            base["preferred_day_off"] = WEEKDAYS[d.weekday()] in a.preferred_days_off
            if not solved:
                status = solution.status if solution else "not solved"
                shifts.append({**base, "status": "Unscheduled", "note": f"queue solve: {status}"})
                continue
            pattern = solution.assignment.get((a.agent_id, d)) if solution else None
            if pattern is not None:
                shifts.append(
                    {
                        **base,
                        "status": "Working",
                        "shift_code": pattern.shift_code,
                        "shift_name": pattern.shift_name,
                        "shift_start": pattern.start.isoformat(),
                        "shift_end": pattern.end.isoformat(),
                        "paid_minutes": pattern.paid_minutes,
                        "unpaid_minutes": pattern.unpaid_minutes,
                        "preferred_shift_matched": pattern.shift_code == a.preferred_shift,
                    }
                )
                for act in pattern.activities:
                    activities.append(
                        {
                            "run_id": run_id,
                            "agent_id": a.agent_id,
                            "work_date": d.isoformat(),
                            "activity_type": act.kind,
                            "start": act.start.isoformat(),
                            "end": act.end.isoformat(),
                            "minutes": act.minutes,
                            "paid": act.paid,
                            "queue_id": a.queue_id if act.kind == "On Phone" else "",
                        }
                    )
            elif d in a.leave_dates:
                credit = _leave_credit_minutes(a, by_plan)
                start, end = _leave_window(a, d, tz, by_plan, credit)
                shifts.append(
                    {
                        **base,
                        "status": "Leave",
                        "shift_start": start.isoformat(),
                        "shift_end": end.isoformat(),
                        "paid_minutes": credit,
                        "unpaid_minutes": 0,
                        "note": "paid leave (credits contracted daily hours)",
                    }
                )
                activities.append(
                    {
                        "run_id": run_id,
                        "agent_id": a.agent_id,
                        "work_date": d.isoformat(),
                        "activity_type": "Leave",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "minutes": credit,
                        "paid": True,
                        "queue_id": "",
                    }
                )
            else:
                shifts.append({**base, "status": "Off", "paid_minutes": 0, "unpaid_minutes": 0})

    info = roster.set_index("agent_id")
    for ex in excluded:
        row = info.loc[ex.agent_id]
        for d in days:
            shifts.append(
                {
                    **_base(
                        run_id,
                        ex.agent_id,
                        str(row["agent_name"]),
                        str(row["bu_id"]),
                        str(row["mu_id"]),
                        ex.queue_id,
                        d,
                        timezones.get(ex.queue_id, ""),
                    ),
                    "status": "Unscheduled",
                    "note": f"not eligible: {ex.reason}",
                }
            )

    coverage = _coverage_table(solutions, requirements, timezones)
    return (
        pd.DataFrame(shifts, columns=SHIFT_COLUMNS).sort_values(
            ["queue_id", "agent_id", "work_date"]
        ),
        pd.DataFrame(activities, columns=ACTIVITY_COLUMNS),
        coverage,
    )


def _base(
    run_id: str, agent_id: str, name: str, bu: str, mu: str, queue: str, d: date, tz: str
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "agent_name": name,
        "bu_id": bu,
        "mu_id": mu,
        "queue_id": queue,
        "work_date": d.isoformat(),
        "timezone": tz,
        "paid_minutes": 0,
        "unpaid_minutes": 0,
    }


def _leave_credit_minutes(a: AgentSpec, by_plan: dict[tuple[str, str], ShiftTemplate]) -> int:
    template = next(t for (plan, _), t in by_plan.items() if plan == a.work_plan_id)
    return round(a.weekly_min_minutes / template.workdays_per_week)


def _leave_window(
    a: AgentSpec, d: date, tz: ZoneInfo, by_plan: dict[tuple[str, str], ShiftTemplate], minutes: int
) -> tuple[datetime, datetime]:
    """Leave is shown at the agent's usual (preferred) shift start for its credited length."""
    preferred = a.preferred_shift or ""
    code = preferred if (a.work_plan_id, preferred) in by_plan else a.allowed_shifts[0]
    start = datetime.combine(d, by_plan[a.work_plan_id, code].start_time, tzinfo=tz)
    return start, (start.astimezone(UTC) + timedelta(minutes=minutes)).astimezone(tz)


def _coverage_table(
    solutions: dict[str, QueueSolution], requirements: pd.DataFrame, timezones: dict[str, str]
) -> pd.DataFrame:
    kinds = {
        (q, pd.Timestamp(s).tz_convert("UTC").to_pydatetime()): k
        for q, s, k in zip(
            requirements["queue_id"],
            requirements["interval_start"],
            requirements["kind"],
            strict=True,
        )
    }
    rows = []
    for queue_id, solution in solutions.items():
        tz = ZoneInfo(timezones[queue_id])
        for c in solution.coverage:
            local = c.slot.astimezone(tz)
            rows.append(
                {
                    "queue_id": queue_id,
                    "interval_start": local.isoformat(),
                    "interval_end": (c.slot + timedelta(minutes=c.minutes))
                    .astimezone(tz)
                    .isoformat(),
                    "interval_minutes": c.minutes,
                    "kind": kinds.get((queue_id, c.slot), "no demand"),
                    "required_on_phone": c.required,
                    "scheduled_on_phone": c.scheduled,
                    "shortage": c.shortage,
                    "excess": c.excess,
                }
            )
    return pd.DataFrame(rows, columns=COVERAGE_COLUMNS)
