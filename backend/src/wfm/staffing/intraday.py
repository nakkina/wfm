"""Intraday demand: from daily queue forecasts to estimated 15-minute call allocations.

The daily forecasts need an intraday profile; a uniform day is never assumed. The profile is
learned from hourly ACD history per queue × weekday × hour on normal (non-event) days and is
normalised within opening hours. Within an hour, calls are split over the hour's *open* slots
using configurable proportions (default: equal). Totals are preserved exactly and nothing is
rounded — allocations are estimates and labelled as such.

Timestamps are timezone-aware in the queue's IANA zone. Slot boundaries are local wall-clock
times; durations are measured in UTC so daylight-saving transitions are handled correctly.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from wfm.io.agents import QueueInfo

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class IntradayError(ValueError):
    """No usable intraday profile, or the profile conflicts with opening hours."""


@dataclass(frozen=True)
class Slot:
    start: datetime  # timezone-aware local start
    end: datetime
    hour: int  # local hour the slot belongs to (for the hourly profile)

    @property
    def seconds(self) -> float:
        return (self.end.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds()


def learn_hourly_profile(hourly: pd.DataFrame) -> pd.DataFrame:
    """Share of each weekday's calls by hour, per queue, from normal days.

    `hourly`: queue_id, date, hour, calls_offered, is_event. Returns queue_id, weekday (0=Mon),
    hour, share — shares sum to 1 for every queue/weekday with history.
    """
    normal = hourly[~hourly["is_event"]].copy()
    normal["weekday"] = pd.to_datetime(normal["date"]).dt.dayofweek
    by_hour = normal.groupby(["queue_id", "weekday", "hour"], as_index=False)["calls_offered"].sum()
    totals = by_hour.groupby(["queue_id", "weekday"])["calls_offered"].transform("sum")
    by_hour = by_hour[totals > 0].copy()
    by_hour["share"] = by_hour["calls_offered"] / totals[totals > 0]
    profile: pd.DataFrame = by_hour[by_hour["share"] > 0][["queue_id", "weekday", "hour", "share"]]
    return profile.reset_index(drop=True)


def local_time(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)


def open_slots(queue: QueueInfo, day: date, interval_minutes: int) -> list[Slot]:
    """15-minute (configurable) slots covering the queue's open window on `day`."""
    if not queue.open_on(WEEKDAYS[day.weekday()]):
        return []
    tz = ZoneInfo(queue.timezone)
    step = timedelta(minutes=interval_minutes)
    wall = datetime.combine(day, queue.open_time)
    close = datetime.combine(day, queue.close_time)
    if close <= wall:
        raise IntradayError(f"{queue.queue_id}: close time must be after open time")
    slots = []
    while wall < close:
        end = min(wall + step, close)
        slots.append(
            Slot(start=wall.replace(tzinfo=tz), end=end.replace(tzinfo=tz), hour=wall.hour)
        )
        wall += step
    return slots


def closing_slots(queue: QueueInfo, day: date, interval_minutes: int, minutes: int) -> list[Slot]:
    """Slots after close in which calls admitted before close may still be finishing."""
    if minutes <= 0 or not queue.open_on(WEEKDAYS[day.weekday()]):
        return []
    tz = ZoneInfo(queue.timezone)
    step = timedelta(minutes=interval_minutes)
    wall = datetime.combine(day, queue.close_time)
    stop = wall + timedelta(minutes=minutes)
    slots = []
    while wall < stop:
        end = min(wall + step, stop)
        slots.append(
            Slot(start=wall.replace(tzinfo=tz), end=end.replace(tzinfo=tz), hour=wall.hour)
        )
        wall += step
    return slots


def allocate_day(
    daily_calls: float,
    hour_shares: dict[int, float],
    slots: list[Slot],
    interval_minutes: int,
    within_hour_proportions: list[float] | None,
    label: str,
) -> list[float]:
    """Calls per slot. Sum equals `daily_calls` (up to float rounding); nothing is rounded."""
    if daily_calls < 0:
        raise IntradayError(f"{label}: negative forecast calls")
    if daily_calls == 0:
        return [0.0] * len(slots)
    open_hours = {s.hour for s in slots}
    if not hour_shares:
        raise IntradayError(f"{label}: no intraday profile in history for this weekday")
    if outside := sorted(h for h in hour_shares if h not in open_hours):
        raise IntradayError(f"{label}: history has calls in closed hours {outside}")

    total_share = sum(hour_shares.values())
    calls = [0.0] * len(slots)
    for hour, share in hour_shares.items():
        idx = [i for i, s in enumerate(slots) if s.hour == hour]
        if within_hour_proportions is None:
            # Equal split across the open part of the hour (e.g. 20:00–20:30 → two slots).
            weights = [slots[i].seconds for i in idx]
        else:
            weights = [
                within_hour_proportions[slots[i].start.minute // interval_minutes] for i in idx
            ]
        weight_sum = sum(weights)
        if weight_sum <= 0:
            raise IntradayError(
                f"{label}: within-hour proportions give no weight to open slots at {hour}:00"
            )
        hour_calls = daily_calls * share / total_share
        for i, w in zip(idx, weights, strict=True):
            calls[i] = hour_calls * w / weight_sum
    return calls
