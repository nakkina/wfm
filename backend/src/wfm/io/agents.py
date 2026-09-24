"""Reads the agent roster workbook: Agents, Shifts and Dictionary sheets.

Workbook headers use spaces ("bu id"); the Dictionary lists underscore names ("bu_id").
Headers are normalized to the Dictionary form before validation.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import time
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, field_validator

from wfm.hierarchy import Organization

AGENTS_SHEET = "Agents"
SHIFTS_SHEET = "Shifts"
DICTIONARY_SHEET = "Dictionary"
ORG_COLUMNS = ["bu_id", "bu_name", "mu_id", "mu_name", "queue_id", "queue_name"]


class ShiftTemplate(BaseModel):
    """One row of the Shifts sheet: a shift pattern for a work plan, with break offsets."""

    work_plan_id: str
    shift_code: str
    shift_name: str
    start_time: time
    end_time: time
    paid_hours: float
    paid_break_count: int
    paid_break_minutes: int  # length of each paid break
    unpaid_meal_minutes: int
    break1_start_offset_min: int | None = None
    meal_start_offset_min: int | None = None
    break2_start_offset_min: int | None = None
    minimum_rest_hours: float
    max_consecutive_workdays: int
    workdays_per_week: int

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def parse_hhmm(cls, value: object) -> object:
        return time.fromisoformat(value) if isinstance(value, str) else value

    @field_validator(
        "break1_start_offset_min", "meal_start_offset_min", "break2_start_offset_min", mode="before"
    )
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, float) and pd.isna(value) else value


class ColumnInfo(BaseModel):
    field: str
    category: str
    type: str
    poc_use: str
    description: str


@dataclass(frozen=True)
class AgentRoster:
    agents: pd.DataFrame
    columns: list[ColumnInfo]
    shifts: list[ShiftTemplate]


class RosterError(ValueError):
    """The workbook doesn't match its Dictionary or the configured hierarchy."""


def normalize_header(name: object) -> str:
    return re.sub(r"\s+", "_", str(name).strip().lower())


def load_agent_roster(path: Path, org: Organization) -> AgentRoster:
    sheets = pd.read_excel(path, sheet_name=[AGENTS_SHEET, SHIFTS_SHEET, DICTIONARY_SHEET])
    agents = sheets[AGENTS_SHEET].rename(columns=normalize_header)
    dictionary = sheets[DICTIONARY_SHEET].rename(columns=normalize_header)
    shifts_frame = sheets[SHIFTS_SHEET].rename(columns=normalize_header)

    columns = [
        ColumnInfo.model_validate({k: str(v) for k, v in row.items()})
        for row in dictionary.to_dict("records")
    ]
    shifts = [ShiftTemplate.model_validate(row) for row in shifts_frame.to_dict("records")]
    _validate(agents, columns, org)
    return AgentRoster(agents=agents, columns=columns, shifts=shifts)


def _validate(agents: pd.DataFrame, columns: list[ColumnInfo], org: Organization) -> None:
    errors: list[str] = []

    fields = [c.field for c in columns]
    if missing := sorted(set(fields) - set(agents.columns)):
        errors.append(f"Agents sheet is missing Dictionary fields: {', '.join(missing)}")
    if undocumented := sorted(set(agents.columns) - set(fields)):
        errors.append(f"Agents sheet has fields not in the Dictionary: {', '.join(undocumented)}")
    if missing_org := [c for c in ORG_COLUMNS + ["agent_id"] if c not in agents.columns]:
        # Remaining checks depend on these columns.
        errors.append(f"Agents sheet lacks required columns: {', '.join(missing_org)}")
        raise RosterError("; ".join(errors))

    duplicate_ids = sorted(i for i, n in Counter(agents["agent_id"]).items() if n > 1)
    if duplicate_ids:
        errors.append(f"Duplicate agent IDs: {', '.join(map(str, duplicate_ids[:10]))}")

    blank = agents[agents[ORG_COLUMNS].isna().any(axis=1)]
    if not blank.empty:
        ids = ", ".join(blank["agent_id"].astype(str)[:10])
        errors.append(f"Agents missing BU/MU/queue: {ids}")

    known = {
        (bu.code, mu.code, q.id)
        for bu in org.business_units
        for mu in bu.management_units
        for q in mu.queues
    }
    assigned = agents.dropna(subset=ORG_COLUMNS)
    unknown = assigned[
        [
            (bu, mu, q) not in known
            for bu, mu, q in zip(
                assigned["bu_id"], assigned["mu_id"], assigned["queue_id"], strict=True
            )
        ]
    ]
    if not unknown.empty:
        examples = ", ".join(
            f"{r.agent_id} ({r.bu_id}/{r.mu_id}/{r.queue_id})"
            for r in unknown.head(10).itertuples()
        )
        errors.append(f"Agents assigned to a BU/MU/queue not in the hierarchy: {examples}")

    if errors:
        raise RosterError("; ".join(errors))


def agents_in_queue(roster: AgentRoster, queue_id: str) -> list[dict[str, object]]:
    """Agent rows for one queue as JSON-safe records: dates as ISO strings, blanks as None."""
    rows = roster.agents[roster.agents["queue_id"] == queue_id].sort_values("agent_id")
    records: list[dict[str, object]] = []
    for row in rows.to_dict("records"):
        records.append({str(k): json_value(v) for k, v in row.items()})
    return records


def json_value(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value
