"""Deterministic placeholder schedules for UI work until CP-SAT is integrated.

Uses each agent's real work plan, allowed shifts, available days and preferred days off,
plus the Shifts sheet's break/meal offsets. It does not look at demand, so it is not a
baseline or an optimized schedule, and it is labelled "mock" everywhere it appears.
"""

import random
from collections.abc import Mapping
from datetime import date, datetime, timedelta

from wfm.io.agents import ShiftTemplate
from wfm.schedule.models import AgentSchedule, ScheduleDay, ScheduleWeek, Segment, SegmentKind

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
PREFERRED_SHIFT_SHARE = 0.75


class MockScheduleError(ValueError):
    """The agent's profile doesn't match any shift template."""


def mock_schedule(
    agent: Mapping[str, object], shifts: list[ShiftTemplate], start: date, weeks: int
) -> AgentSchedule:
    if start.weekday() != 0:
        raise MockScheduleError(f"Mock schedules start on a Monday; got {start:%a %Y-%m-%d}")

    agent_id = str(agent["agent_id"])
    templates = {s.shift_code: s for s in shifts if s.work_plan_id == agent["work_plan_id"]}
    allowed = [c for c in _split(agent.get("allowed_shifts")) if c in templates]
    if not allowed:
        raise MockScheduleError(f"{agent_id}: no Shifts template for its work plan/allowed shifts")
    preferred = str(agent.get("preferred_shift"))
    if preferred not in allowed:
        preferred = allowed[0]

    workdays = _workdays(agent, next(iter(templates.values())).workdays_per_week)
    rng = random.Random(f"{agent_id}:{start.isoformat()}")  # same agent + horizon -> same mock

    result_weeks: list[ScheduleWeek] = []
    previous_end: datetime | None = None
    for w in range(weeks):
        week_start = start + timedelta(weeks=w)
        days: list[ScheduleDay] = []
        for offset, weekday in enumerate(WEEKDAYS):
            day = week_start + timedelta(days=offset)
            if weekday not in workdays:
                days.append(ScheduleDay(date=day, weekday=weekday, off=True))
                continue
            code = preferred
            if len(allowed) > 1 and rng.random() > PREFERRED_SHIFT_SHARE:
                code = rng.choice([c for c in allowed if c != preferred])
            template = templates[code]
            rest = datetime.combine(day, template.start_time) - (previous_end or datetime.min)
            if rest < timedelta(hours=template.minimum_rest_hours):
                template = templates[preferred]  # fall back to the agent's usual shift
            previous_end = datetime.combine(day, template.end_time)
            days.append(_working_day(day, weekday, template))
        result_weeks.append(
            ScheduleWeek(
                week_start=week_start,
                paid_hours=sum(d.paid_hours for d in days),
                days_off=sum(d.off for d in days),
                days=days,
            )
        )

    return AgentSchedule(
        agent_id=agent_id,
        source="mock",
        validation_status="Mock data — not validated",
        horizon_start=start,
        weeks=result_weeks,
    )


def _split(value: object) -> list[str]:
    return [v.strip() for v in str(value).split(";") if v.strip()] if value else []


def _workdays(agent: Mapping[str, object], count: int) -> set[str]:
    """Available days, skipping preferred days off, topped up if too few remain."""
    available = set(_split(agent.get("available_days"))) or set(WEEKDAYS)
    preferred_off = set(_split(agent.get("preferred_days_off")))
    chosen = [d for d in WEEKDAYS if d in available and d not in preferred_off]
    chosen += [d for d in WEEKDAYS if d in available and d in preferred_off]
    return set(chosen[:count])


def _working_day(day: date, weekday: str, template: ShiftTemplate) -> ScheduleDay:
    start = datetime.combine(day, template.start_time)
    end = datetime.combine(day, template.end_time)

    breaks: list[tuple[SegmentKind, datetime, datetime]] = []
    planned: list[tuple[int | None, SegmentKind, int]] = [
        (template.break1_start_offset_min, "break", template.paid_break_minutes),
        (template.meal_start_offset_min, "meal", template.unpaid_meal_minutes),
        (template.break2_start_offset_min, "break", template.paid_break_minutes),
    ]
    for offset, kind, minutes in planned:
        if offset is not None and minutes:
            b_start = start + timedelta(minutes=offset)
            breaks.append((kind, b_start, b_start + timedelta(minutes=minutes)))
    breaks.sort(key=lambda b: b[1])

    segments: list[Segment] = []
    cursor = start
    for kind, b_start, b_end in breaks:
        if b_start > cursor:
            segments.append(Segment(kind="work", start=cursor.time(), end=b_start.time()))
        segments.append(Segment(kind=kind, start=b_start.time(), end=b_end.time()))
        cursor = b_end
    if cursor < end:
        segments.append(Segment(kind="work", start=cursor.time(), end=end.time()))

    return ScheduleDay(
        date=day,
        weekday=weekday,
        off=False,
        shift_code=template.shift_code,
        shift_name=template.shift_name,
        start=template.start_time,
        end=template.end_time,
        paid_hours=template.paid_hours,
        segments=segments,
    )
