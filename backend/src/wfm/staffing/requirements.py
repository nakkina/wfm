"""Interval staffing requirements per queue from daily forecasts.

For every open interval: workload = calls × AHT; Erlangs = workload / interval seconds;
Erlang C gives the smallest productive N meeting the service-level target and occupancy cap;
on-phone requirement = ceil(N / (1 − residual shrinkage)) — shrinkage applied once.

Closing intervals (after the queue closes) carry no new arrivals, but calls admitted before
close are still being handled: they require ceil(A_last) agents — the mean number of busy
agents in the last open interval — for `closing_minutes`. They are separate rows (kind =
"closing") so they are neither mixed with open-hour demand nor silently discarded.
"""

import math
from datetime import date, timedelta

import pandas as pd

from wfm.config import StaffingSettings
from wfm.io.agents import QueueInfo
from wfm.staffing.erlang import on_phone_agents, required_agents
from wfm.staffing.intraday import Slot, allocate_day, closing_slots, open_slots

COLUMNS = [
    "queue_id", "date", "interval_start", "interval_end", "interval_minutes", "kind",
    "offered_calls", "aht_seconds", "workload_agent_hours", "offered_load_erlangs",
    "required_productive", "expected_service_level", "occupancy", "required_on_phone",
    "required_on_phone_hours", "allocation",
]  # fmt: skip


class RequirementsError(ValueError):
    """Forecast inputs are missing, inconsistent or invalid."""


def validate_forecast(points: pd.DataFrame, queues: dict[str, QueueInfo]) -> pd.DataFrame:
    """Daily forecast → one row per queue/date with `calls` and `aht_seconds`."""
    errors: list[str] = []
    if unknown := sorted(set(points["queue_id"]) - set(queues)):
        errors.append(f"Forecast queues missing from the Queues sheet: {', '.join(unknown)}")
    wide = points.pivot_table(
        index=["queue_id", "date"], columns="target", values="value", aggfunc="first"
    )
    for target in ("volume", "aht"):
        if target not in wide.columns:
            raise RequirementsError(f"Forecast has no '{target}' values")
    wide = wide.rename(columns={"volume": "calls", "aht": "aht_seconds"}).reset_index()
    if wide[["calls", "aht_seconds"]].isna().any().any():
        bad = wide[wide[["calls", "aht_seconds"]].isna().any(axis=1)]
        first = f"{bad.iloc[0]['queue_id']} {bad.iloc[0]['date']}"
        errors.append(f"Missing volume or AHT for {len(bad)} queue-days, e.g. {first}")
    if (wide["calls"] < 0).any():
        errors.append("Negative forecast calls")
    if ((wide["calls"] > 0) & ~(wide["aht_seconds"] > 0)).any():
        errors.append("AHT must be positive where forecast calls are positive")
    for queue_id, g in wide.groupby("queue_id"):
        dates = pd.to_datetime(g["date"]).sort_values()
        if len(dates) != (dates.iloc[-1] - dates.iloc[0]).days + 1:
            errors.append(f"{queue_id}: forecast dates are not contiguous")
    if errors:
        raise RequirementsError("; ".join(errors))
    return wide


def build_requirements(
    points: pd.DataFrame,
    profile: pd.DataFrame,
    queues: dict[str, QueueInfo],
    settings: StaffingSettings,
) -> pd.DataFrame:
    daily = validate_forecast(points, queues)
    shares = {
        (q, wd): dict(zip(g["hour"], g["share"], strict=True))
        for (q, wd), g in profile.groupby(["queue_id", "weekday"])
    }
    rows: list[dict[str, object]] = []
    for r in daily.sort_values(["queue_id", "date"]).to_dict("records"):
        queue = queues[str(r["queue_id"])]
        day: date = pd.Timestamp(r["date"]).date()
        slots = open_slots(queue, day, settings.interval_minutes)
        label = f"{queue.queue_id} {day}"
        calls = allocate_day(
            float(r["calls"]),
            shares.get((queue.queue_id, day.weekday()), {}),
            slots,
            settings.interval_minutes,
            settings.within_hour_proportions,
            label,
        )
        aht = float(r["aht_seconds"])
        last_load = 0.0
        for slot, c in zip(slots, calls, strict=True):
            workload = c * aht
            load = workload / slot.seconds
            er = required_agents(
                load,
                aht,
                settings.service_level_target,
                settings.answer_threshold_seconds,
                settings.max_occupancy,
            )
            on_phone = on_phone_agents(er.agents, settings.residual_shrinkage)
            rows.append(
                _row(
                    queue.queue_id,
                    day,
                    slot,
                    "open",
                    c,
                    aht,
                    workload,
                    load,
                    er.agents,
                    er.service_level,
                    er.occupancy,
                    on_phone,
                    "estimated",
                )
            )
            last_load = load
        closing_need = math.ceil(last_load - 1e-9) if last_load > 0 else 0
        for slot in closing_slots(queue, day, settings.interval_minutes, settings.closing_minutes):
            rows.append(
                _row(
                    queue.queue_id,
                    day,
                    slot,
                    "closing",
                    0.0,
                    aht,
                    0.0,
                    0.0,
                    closing_need,
                    math.nan,
                    math.nan,
                    closing_need,
                    "closing allowance",
                )
            )
    return pd.DataFrame(rows, columns=COLUMNS)


def _row(
    queue_id: str, day: date, slot: Slot, kind: str, calls: float, aht: float, workload: float,
    load: float, productive: int, sl: float, occupancy: float, on_phone: int, allocation: str,
) -> dict[str, object]:  # fmt: skip
    minutes = slot.seconds / 60  # measured in UTC, so DST days are handled
    return {
        "queue_id": queue_id,
        "date": day,
        "interval_start": slot.start,
        "interval_end": slot.end,
        "interval_minutes": minutes,
        "kind": kind,
        "offered_calls": calls,
        "aht_seconds": aht,
        "workload_agent_hours": workload / 3600,
        "offered_load_erlangs": load,
        "required_productive": productive,
        "expected_service_level": sl,
        "occupancy": occupancy,
        "required_on_phone": on_phone,
        "required_on_phone_hours": on_phone * minutes / 60,
        "allocation": allocation,
    }


def summarize(requirements: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Totals by the given keys. Agent-hours are summed; concurrent agents are only ever
    reported as a peak (`peak_required_on_phone`), never summed across intervals."""
    frame = requirements.copy()
    frame["week_start"] = [d - timedelta(days=d.weekday()) for d in frame["date"]]
    return frame.groupby(by, as_index=False).agg(
        offered_calls=("offered_calls", "sum"),
        workload_agent_hours=("workload_agent_hours", "sum"),
        required_on_phone_hours=("required_on_phone_hours", "sum"),
        peak_required_on_phone=("required_on_phone", "max"),
    )
