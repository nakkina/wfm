"""Agent schedule shape shared by the mock generator and (later) the CP-SAT output."""

from datetime import date, time
from typing import Literal

from pydantic import BaseModel

SegmentKind = Literal["work", "break", "meal"]


class Segment(BaseModel):
    kind: SegmentKind
    start: time
    end: time


class ScheduleDay(BaseModel):
    date: date
    weekday: str  # "Mon".."Sun"
    off: bool
    shift_code: str | None = None
    shift_name: str | None = None
    start: time | None = None
    end: time | None = None
    paid_hours: float = 0
    segments: list[Segment] = []


class ScheduleWeek(BaseModel):
    week_start: date
    paid_hours: float
    days_off: int
    days: list[ScheduleDay]


class AgentSchedule(BaseModel):
    agent_id: str
    source: Literal["mock", "baseline", "optimized"]
    validation_status: str
    horizon_start: date
    weeks: list[ScheduleWeek]
