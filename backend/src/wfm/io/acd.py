"""Reads hourly ACD history (the "Queue hourly" sheet) and rolls it up to daily per queue.

Per the ACD Dictionary, aht_seconds is a weighted ratio that must be recomputed after
aggregation: daily AHT = sum(handle_seconds) / sum(calls_handled), blank when nothing
was handled. Hourly aht_seconds values are never summed or averaged.

The optional "Events" sheet lists holidays, incidents and campaigns (scope ALL or an MU).
Each queue-day touched by an event is labelled in the `event` column so forecasting can
keep one-off shocks out of model training.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from wfm.hierarchy import Organization
from wfm.io.agents import normalize_header

QUEUE_HOURLY_SHEET = "Queue hourly"
EVENTS_SHEET = "Events"
REQUIRED_COLUMNS = [
    "bu_id",
    "mu_id",
    "queue_id",
    "interval_start_local",
    "calls_offered",
    "calls_handled",
    "handle_seconds",
]
SUMMED_COLUMNS = ["calls_offered", "calls_handled", "handle_seconds"]


class AcdError(ValueError):
    """The ACD workbook is inconsistent with itself or with the configured hierarchy."""


class DailyPoint(BaseModel):
    date: date
    calls_offered: int
    calls_handled: int
    handle_seconds: int
    aht_seconds: float | None  # None when no calls were handled that day


class QueueHistory(BaseModel):
    queue_id: str
    start: date
    end: date
    days: list[DailyPoint]


@dataclass(frozen=True)
class AcdHistory:
    daily: pd.DataFrame  # one row per queue per local date; `event` names or None


def load_acd_history(path: Path, org: Organization) -> AcdHistory:
    with pd.ExcelFile(path) as workbook:
        hourly = workbook.parse(QUEUE_HOURLY_SHEET).rename(columns=normalize_header)
        events = (
            workbook.parse(EVENTS_SHEET).rename(columns=normalize_header)
            if EVENTS_SHEET in workbook.sheet_names
            else None
        )
    _validate(hourly, org)
    hourly["date"] = pd.to_datetime(hourly["interval_start_local"]).dt.date
    daily = hourly.groupby(["queue_id", "date"], as_index=False)[SUMMED_COLUMNS].sum()
    daily["event"] = _event_labels(daily, events, org)
    return AcdHistory(daily=daily)


def _event_labels(
    daily: pd.DataFrame, events: pd.DataFrame | None, org: Organization
) -> list[str | None]:
    if events is None or events.empty:
        return [None] * len(daily)
    missing = [
        c for c in ("start_date", "end_date", "scope", "event_name") if c not in events.columns
    ]
    if missing:
        raise AcdError(f"'{EVENTS_SHEET}' sheet lacks columns: {', '.join(missing)}")

    queues_by_mu = {
        mu.code: [q.id for q in mu.queues]
        for bu in org.business_units
        for mu in bu.management_units
    }
    all_queues = [q.id for q in org.queues()]
    labels: dict[tuple[str, date], list[str]] = {}
    for row in events.to_dict("records"):
        scope = str(row["scope"])
        if scope != "ALL" and scope not in queues_by_mu:
            raise AcdError(f"Event '{row['event_name']}' has unknown scope {scope}")
        queues = all_queues if scope == "ALL" else queues_by_mu[scope]
        for day in pd.date_range(row["start_date"], row["end_date"], freq="D"):
            for q in queues:
                labels.setdefault((q, day.date()), []).append(str(row["event_name"]))
    return [
        "; ".join(labels[(q, d)]) if (q, d) in labels else None
        for q, d in zip(daily["queue_id"], daily["date"], strict=True)
    ]


def _validate(hourly: pd.DataFrame, org: Organization) -> None:
    if missing := [c for c in REQUIRED_COLUMNS if c not in hourly.columns]:
        raise AcdError(f"'{QUEUE_HOURLY_SHEET}' sheet lacks columns: {', '.join(missing)}")

    errors: list[str] = []
    known = {
        (bu.code, mu.code, q.id)
        for bu in org.business_units
        for mu in bu.management_units
        for q in mu.queues
    }
    combos = hourly[["bu_id", "mu_id", "queue_id"]].drop_duplicates()
    unknown = [
        f"{bu}/{mu}/{q}" for bu, mu, q in combos.itertuples(index=False) if (bu, mu, q) not in known
    ]
    if unknown:
        errors.append(f"Rows for BU/MU/queue not in the hierarchy: {', '.join(unknown[:10])}")

    duplicates = hourly.duplicated(["queue_id", "interval_start_local"]).sum()
    if duplicates:
        errors.append(f"{duplicates} duplicate queue/interval rows")

    for column in SUMMED_COLUMNS:
        if (hourly[column] < 0).any():
            errors.append(f"Negative values in {column}")
        if hourly[column].isna().any():
            # Missing data is not zero (PRD §2).
            errors.append(f"Blank values in {column}")

    if errors:
        raise AcdError("; ".join(errors))


def queue_history(history: AcdHistory, queue_id: str) -> QueueHistory | None:
    rows = history.daily[history.daily["queue_id"] == queue_id].sort_values("date")
    if rows.empty:
        return None
    days: list[DailyPoint] = []
    for row in rows.to_dict("records"):
        handled, handle_seconds = int(row["calls_handled"]), int(row["handle_seconds"])
        days.append(
            DailyPoint(
                date=row["date"],
                calls_offered=int(row["calls_offered"]),
                calls_handled=handled,
                handle_seconds=handle_seconds,
                aht_seconds=round(handle_seconds / handled, 1) if handled else None,
            )
        )
    return QueueHistory(queue_id=queue_id, start=days[0].date, end=days[-1].date, days=days)
