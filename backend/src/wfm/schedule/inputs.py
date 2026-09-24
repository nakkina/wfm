"""Scheduling inputs: eligible agents, known leave and weekly paid-hour bounds.

Policies (configurable POC choices, not legal rules):
- Eligible = active, schedulable, and holding the queue's required skill (when the Queues
  sheet names one). Ineligible agents are listed with a reason, never silently dropped.
- Weeks are Monday–Sunday. In a full week the agent's weekly min/max paid hours apply as-is.
  In a partial horizon week, bounds are pro-rated by the agent's available days that fall in
  the horizon (min rounded down, max rounded up to whole shifts, never above the weekly max),
  so a full week's hours are never required inside a partial week.
- Paid leave credits the contracted daily paid time (weekly minimum ÷ workdays per week)
  toward that week's paid hours.
- No prior schedule history exists, so every agent is assumed rested at the horizon start.
"""

import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from wfm.io.agents import QueueInfo, ShiftTemplate

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class InputError(ValueError):
    """Roster, leave or shift-template data can't support scheduling."""


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    agent_name: str
    bu_id: str
    mu_id: str
    queue_id: str
    employment_type: str
    work_plan_id: str
    weekly_min_minutes: int
    weekly_max_minutes: int
    allowed_shifts: tuple[str, ...]
    preferred_shift: str | None
    available_days: frozenset[str]
    preferred_days_off: frozenset[str]
    min_rest_hours: float
    max_consecutive_days: int
    leave_dates: frozenset[date] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Exclusion:
    agent_id: str
    queue_id: str
    reason: str


@dataclass(frozen=True)
class WeekBounds:
    week_start: date
    days: tuple[date, ...]  # horizon days inside this Mon–Sun week
    min_paid_minutes: int  # applied to worked paid minutes + leave credit
    max_paid_minutes: int
    leave_credit_minutes: int
    partial: bool


def _split(value: object) -> list[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    return [v.strip() for v in str(value).split(";") if v.strip()]


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def load_leave(path: Path | None, known_agents: set[str]) -> dict[str, set[date]]:
    """Optional CSV with columns agent_id,date (one paid leave day per row)."""
    leave: dict[str, set[date]] = defaultdict(set)
    if path is None:
        return leave
    if not path.is_file():
        raise InputError(f"Leave file not found: {path}")
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            agent = (row.get("agent_id") or "").strip()
            if agent not in known_agents:
                raise InputError(f"{path.name} line {i}: unknown agent_id {agent!r}")
            try:
                leave[agent].add(date.fromisoformat((row.get("date") or "").strip()))
            except ValueError as e:
                raise InputError(f"{path.name} line {i}: invalid date") from e
    return leave


def build_agents(
    roster: pd.DataFrame,
    queues: dict[str, QueueInfo],
    templates: list[ShiftTemplate],
    leave: dict[str, set[date]],
) -> tuple[list[AgentSpec], list[Exclusion]]:
    plans = {t.work_plan_id for t in templates}
    agents: list[AgentSpec] = []
    excluded: list[Exclusion] = []
    for r in roster.to_dict("records"):
        agent_id, queue_id = str(r["agent_id"]), str(r["queue_id"])
        reason = None
        if str(r.get("employment_status", "Active")) != "Active":
            reason = f"employment status {r.get('employment_status')}"
        elif "can_schedule" in r and not _truthy(r["can_schedule"]):
            reason = "not schedulable"
        elif queue_id not in queues:
            reason = "home queue has no opening hours in the Queues sheet"
        elif (skill := queues[queue_id].required_skill) and str(r.get("primary_skill")) != skill:
            reason = f"lacks required skill '{skill}'"
        elif str(r["work_plan_id"]) not in plans:
            reason = f"work plan {r['work_plan_id']} has no shift templates"
        if reason:
            excluded.append(Exclusion(agent_id, queue_id, reason))
            continue
        plan_codes = {t.shift_code for t in templates if t.work_plan_id == r["work_plan_id"]}
        allowed = tuple(s for s in _split(r.get("allowed_shifts")) if s in plan_codes) or tuple(
            sorted(plan_codes)
        )
        agents.append(
            AgentSpec(
                agent_id=agent_id,
                agent_name=str(r["agent_name"]),
                bu_id=str(r["bu_id"]),
                mu_id=str(r["mu_id"]),
                queue_id=queue_id,
                employment_type=str(r.get("employment_type", "")),
                work_plan_id=str(r["work_plan_id"]),
                weekly_min_minutes=round(float(r["weekly_min_hours"]) * 60),
                weekly_max_minutes=round(float(r["weekly_max_hours"]) * 60),
                allowed_shifts=allowed,
                preferred_shift=str(r["preferred_shift"]) if r.get("preferred_shift") else None,
                available_days=frozenset(_split(r.get("available_days")) or WEEKDAYS),
                preferred_days_off=frozenset(_split(r.get("preferred_days_off"))),
                min_rest_hours=float(r.get("minimum_rest_hours", 11)),
                max_consecutive_days=int(r.get("max_consecutive_workdays", 5)),
                leave_dates=frozenset(leave.get(agent_id, set())),
            )
        )
    return agents, excluded


def horizon_weeks(days: list[date]) -> dict[date, tuple[date, ...]]:
    """Mon–Sun week start → horizon days in that week."""
    weeks: dict[date, list[date]] = defaultdict(list)
    for d in sorted(days):
        weeks[d - timedelta(days=d.weekday())].append(d)
    return {k: tuple(v) for k, v in weeks.items()}


def week_bounds(
    agent: AgentSpec,
    week_start: date,
    days: tuple[date, ...],
    shift_paid_minutes: int,
    workdays_per_week: int,
) -> WeekBounds:
    leave_days = [d for d in days if d in agent.leave_dates]
    credit = round(len(leave_days) * agent.weekly_min_minutes / workdays_per_week)
    if len(days) == 7:
        lo, hi = agent.weekly_min_minutes, agent.weekly_max_minutes
    else:
        available_per_week = sum(1 for wd in WEEKDAYS if wd in agent.available_days) or 7
        available_here = sum(1 for d in days if WEEKDAYS[d.weekday()] in agent.available_days)
        ratio = available_here / available_per_week
        lo = (
            math.floor(agent.weekly_min_minutes * ratio / shift_paid_minutes + 1e-9)
            * shift_paid_minutes
        )
        hi = min(
            agent.weekly_max_minutes,
            math.ceil(agent.weekly_max_minutes * ratio / shift_paid_minutes - 1e-9)
            * shift_paid_minutes,
        )
    return WeekBounds(week_start, days, lo, hi, credit, partial=len(days) < 7)


def capacity_conflicts(agent: AgentSpec, bounds: WeekBounds, shift_paid_minutes: int) -> str | None:
    """A week whose minimum can't be reached even working every available non-leave day."""
    workable = sum(
        1
        for d in bounds.days
        if WEEKDAYS[d.weekday()] in agent.available_days and d not in agent.leave_dates
    )
    most = workable * shift_paid_minutes + bounds.leave_credit_minutes
    if most < bounds.min_paid_minutes:
        return (
            f"{agent.agent_id} week of {bounds.week_start}: "
            f"needs {bounds.min_paid_minutes / 60:g} paid h "
            f"but at most {most / 60:g} h is possible ({workable} workable days)"
        )
    if bounds.leave_credit_minutes > bounds.max_paid_minutes:
        return (
            f"{agent.agent_id} week of {bounds.week_start}: leave credit exceeds maximum paid hours"
        )
    return None
