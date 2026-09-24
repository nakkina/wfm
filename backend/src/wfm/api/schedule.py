"""Schedule endpoints: start a run, poll it, read queue/agent plans, roll-ups and CSV exports.

Runs execute in a worker subprocess (one at a time); results are read from the run folder.
"""

import io
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from wfm.config import load_config, resolve
from wfm.forecast.store import file_fingerprint
from wfm.schedule.run import apply_overrides
from wfm.schedule.store import (
    active_run,
    latest_run,
    new_run_dir,
    read_status,
    schedules_root,
    write_status,
)

router = APIRouter(prefix="/api")

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _runs_dir() -> Path:
    return resolve(load_config().paths.runs_dir)


# --- Runs -------------------------------------------------------------------------------


class RunRequest(BaseModel):
    staffing: dict[str, Any] = {}
    scheduling: dict[str, Any] = {}


@router.get("/schedule/config")
def schedule_config() -> dict[str, Any]:
    config = load_config()
    return {
        "staffing": config.staffing.model_dump(mode="json"),
        "scheduling": config.scheduling.model_dump(mode="json"),
    }


@router.post("/schedule/runs", status_code=202)
def start_run(request: RunRequest) -> dict[str, Any]:
    overrides = request.model_dump()
    try:
        apply_overrides(load_config(), overrides)  # validate before starting anything
    except ValidationError as e:
        raise HTTPException(
            status_code=422, detail=f"Invalid settings: {e.errors()[0]['msg']}"
        ) from e
    runs_dir = _runs_dir()
    if running := active_run(runs_dir):
        raise HTTPException(status_code=409, detail=f"Run {running.name} is still in progress")
    run_dir = new_run_dir(runs_dir)
    write_status(
        run_dir,
        {"run_id": run_dir.name, "state": "queued", "message": "Starting", "done": 0, "total": 1},
    )
    with (run_dir / "worker.log").open("w") as log:
        process = subprocess.Popen(  # noqa: S603 — fixed interpreter and module, no shell
            [
                sys.executable,
                "-m",
                "wfm.schedule.run",
                "--run-dir",
                str(run_dir),
                "--overrides",
                json.dumps(overrides),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    write_status(
        run_dir,
        {
            "run_id": run_dir.name,
            "pid": process.pid,
            "state": "queued",
            "message": "Starting",
            "done": 0,
            "total": 1,
        },
    )
    return {"run_id": run_dir.name}


@router.get("/schedule/runs/latest")
def latest() -> dict[str, Any]:
    runs_dir = _runs_dir()
    run_dir = latest_run(runs_dir)
    if run_dir is None:
        return {"state": "not_generated"}
    status = read_status(run_dir) or {}
    completed = latest_run(runs_dir, completed_only=True)
    body: dict[str, Any] = {
        **status,
        "latest_completed_run_id": completed.name if completed else None,
    }
    if completed:
        meta = _load(completed.name).meta
        body["summary"] = _run_summary(meta)
        body["stale"] = _is_stale(meta)
    return body


def _run_summary(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": meta["run_id"],
        "created_at": meta["created_at"],
        "horizon": meta["horizon"],
        "status_counts": meta["status_counts"],
        "shortage_agent_hours": meta["shortage_agent_hours"],
        "validation_passed": meta["validation_passed"],
        "runtime_seconds": meta["runtime_seconds"],
        "conflicts": meta["conflicts"],
        "excluded_agents": meta["agents"]["excluded"],
        "config": meta["config"],
        "assumptions": meta["assumptions"],
    }


def _is_stale(meta: dict[str, Any]) -> bool:
    """True when the roster or ACD file changed since the run.

    Runs saved before fingerprints were recorded are treated as not stale (unknown).
    """
    inputs = meta.get("inputs")
    if not inputs:
        return False
    paths = load_config().paths
    current = {
        "agents_sha256": file_fingerprint(resolve(paths.agents_xlsx)),
        "acd_sha256": file_fingerprint(resolve(paths.acd_xlsx)),
    }
    return any(inputs.get(k) != v for k, v in current.items())


# --- Loading run outputs -----------------------------------------------------------------


@dataclass(frozen=True)
class RunData:
    meta: dict[str, Any]
    validation: dict[str, Any]
    shifts: pd.DataFrame
    activities: pd.DataFrame
    coverage: pd.DataFrame
    requirements: pd.DataFrame


@lru_cache(maxsize=2)
def _load(run_id: str) -> RunData:
    run_dir = schedules_root(_runs_dir()) / run_id
    if not (run_dir / "meta.json").is_file():
        raise HTTPException(status_code=404, detail=f"Schedule run {run_id} has no results")

    def read(name: str) -> pd.DataFrame:
        return pd.read_csv(run_dir / f"{name}.csv", keep_default_na=False, na_values=[""])

    return RunData(
        meta=json.loads((run_dir / "meta.json").read_text()),
        validation=json.loads((run_dir / "validation.json").read_text()),
        shifts=read("shifts"),
        activities=read("activities"),
        coverage=read("coverage"),
        requirements=read("requirements"),
    )


def _completed() -> RunData | None:
    run_dir = latest_run(_runs_dir(), completed_only=True)
    return _load(run_dir.name) if run_dir else None


def _clean(value: Any) -> Any:
    if isinstance(value, float) and pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): _clean(v) for k, v in r.items()} for r in frame.to_dict("records")]


# --- Queue schedule ------------------------------------------------------------------------


@router.get("/queues/{queue_id}/schedule")
def queue_schedule(queue_id: str) -> dict[str, Any]:
    data = _completed()
    if data is None:
        raise HTTPException(status_code=404, detail="No schedule has been generated yet")
    queue_meta = data.meta["queues"].get(queue_id)
    shifts = data.shifts[data.shifts["queue_id"] == queue_id]
    if queue_meta is None and shifts.empty:
        raise HTTPException(status_code=404, detail=f"Queue {queue_id} is not in the schedule")
    agent_ids = set(shifts["agent_id"])
    acts = data.activities[data.activities["agent_id"].isin(agent_ids)]
    acts_by = {
        k: _records(g.sort_values("start")) for k, g in acts.groupby(["agent_id", "work_date"])
    }
    agents = []
    for agent_id, g in shifts.groupby("agent_id"):
        g = g.sort_values("work_date")
        first = g.iloc[0]
        days: list[dict[str, Any]] = []
        for r in _records(g):
            r["activities"] = [
                {k: a[k] for k in ("activity_type", "start", "end", "minutes", "paid", "queue_id")}
                for a in acts_by.get((agent_id, r["work_date"]), [])
            ]
            days.append({k: r[k] for k in (
                "work_date", "status", "shift_code", "shift_name", "shift_start", "shift_end",
                "paid_minutes", "unpaid_minutes", "preferred_shift_matched", "preferred_day_off",
                "note",
                "activities",
            )})  # fmt: skip
        agents.append(
            {
                "agent_id": agent_id,
                "agent_name": first["agent_name"],
                "paid_minutes": int(g["paid_minutes"].sum()),
                "working_days": int((g["status"] == "Working").sum()),
                "preferred_shift_matches": int((g["preferred_shift_matched"] == True).sum()),  # noqa: E712
                "days": days,
            }
        )
    coverage = data.coverage[data.coverage["queue_id"] == queue_id]
    return {
        "run_id": data.meta["run_id"],
        "created_at": data.meta["created_at"],
        "horizon": data.meta["horizon"],
        "stale": _is_stale(data.meta),
        "validation_passed": data.meta["validation_passed"],
        "queue": {"queue_id": queue_id, **(queue_meta or {"status": "not solved", "stages": []})},
        "totals": _totals(coverage, shifts),
        "coverage": _records(coverage),
        "agents": agents,
    }


def _totals(coverage: pd.DataFrame, shifts: pd.DataFrame) -> dict[str, float]:
    hours = coverage["interval_minutes"] / 60
    return {
        "required_on_phone_hours": round(float((coverage["required_on_phone"] * hours).sum()), 2),
        "scheduled_on_phone_hours": round(float((coverage["scheduled_on_phone"] * hours).sum()), 2),
        "shortage_agent_hours": round(float((coverage["shortage"] * hours).sum()), 2),
        "excess_agent_hours": round(float((coverage["excess"] * hours).sum()), 2),
        "paid_hours": round(
            float(shifts.loc[shifts["status"] == "Working", "paid_minutes"].sum() / 60), 2
        ),
        "leave_hours": round(
            float(shifts.loc[shifts["status"] == "Leave", "paid_minutes"].sum() / 60), 2
        ),
        "working_shifts": int((shifts["status"] == "Working").sum()),
        "agents": int(shifts["agent_id"].nunique()),
        "peak_required_on_phone": int(coverage["required_on_phone"].max()) if len(coverage) else 0,
    }


# --- Roll-ups (queue → MU → BU → organization) ---------------------------------------------


@router.get("/schedule/summary")
def schedule_summary(
    level: Annotated[Literal["org", "bu", "mu"], Query()], code: str = ""
) -> dict[str, Any]:
    """Totals for the children of a node, plus their sum — which equals the node's own total."""
    data = _completed()
    if data is None:
        raise HTTPException(status_code=404, detail="No schedule has been generated yet")
    shifts = data.shifts
    queue_parent = shifts.drop_duplicates("queue_id").set_index("queue_id")[["bu_id", "mu_id"]]
    cov = data.coverage.join(queue_parent, on="queue_id")
    child = {"org": "bu_id", "bu": "mu_id", "mu": "queue_id"}[level]
    if level == "bu":
        cov, shifts = cov[cov["bu_id"] == code], shifts[shifts["bu_id"] == code]
    elif level == "mu":
        cov, shifts = cov[cov["mu_id"] == code], shifts[shifts["mu_id"] == code]
    rows = [
        {"code": key, **_totals(cov[cov[child] == key], shifts[shifts[child] == key])}
        for key in sorted(set(shifts[child]))
    ]
    return {
        "run_id": data.meta["run_id"],
        "level": level,
        "code": code,
        "children": rows,
        "total": _totals(cov, shifts),
    }


# --- Agent schedule (real plan) ------------------------------------------------------------


def agent_plan(agent_id: str) -> dict[str, Any] | None:
    """The agent's weekly calendar from the latest completed run, or None if not scheduled."""
    data = _completed()
    if data is None:
        return None
    rows = data.shifts[data.shifts["agent_id"] == agent_id].sort_values("work_date")
    if rows.empty:
        return None
    acts = data.activities[data.activities["agent_id"] == agent_id]
    by_day = {d: _records(g.sort_values("start")) for d, g in acts.groupby("work_date")}
    plan = {r["work_date"]: r for r in _records(rows)}
    first = date.fromisoformat(min(plan))
    last = date.fromisoformat(max(plan))
    weeks = []
    week_start = first - timedelta(days=first.weekday())
    while week_start <= last:
        days: list[dict[str, Any]] = []
        for i in range(7):
            d = week_start + timedelta(days=i)
            r = plan.get(d.isoformat())
            if r is None:
                days.append(
                    {
                        "date": d.isoformat(),
                        "weekday": WEEKDAYS[i],
                        "status": "Outside horizon",
                        "off": True,
                    }
                )
                continue
            days.append(
                {
                    "date": d.isoformat(),
                    "weekday": WEEKDAYS[i],
                    "status": r["status"],
                    "off": r["status"] != "Working",
                    "shift_code": r["shift_code"],
                    "shift_name": r["shift_name"],
                    "start": r["shift_start"],
                    "end": r["shift_end"],
                    "paid_hours": (r["paid_minutes"] or 0) / 60,
                    "preferred_shift_matched": r["preferred_shift_matched"],
                    "preferred_day_off": r["preferred_day_off"],
                    "note": r["note"],
                    "activities": [
                        {
                            k: a[k]
                            for k in (
                                "activity_type",
                                "start",
                                "end",
                                "minutes",
                                "paid",
                                "queue_id",
                            )
                        }
                        for a in by_day.get(d.isoformat(), [])
                    ],
                }
            )
        in_horizon = [x for x in days if x["status"] != "Outside horizon"]
        weeks.append(
            {
                "week_start": week_start.isoformat(),
                "paid_hours": sum(x.get("paid_hours") or 0 for x in in_horizon),
                "days_off": sum(1 for x in in_horizon if x["status"] == "Off"),
                "leave_days": sum(1 for x in in_horizon if x["status"] == "Leave"),
                "preferred_shift_matches": sum(
                    1 for x in in_horizon if x.get("preferred_shift_matched") is True
                ),
                "working_days": sum(1 for x in in_horizon if x["status"] == "Working"),
                "partial": len(in_horizon) < 7,
                "days": days,
            }
        )
        week_start += timedelta(days=7)
    return {
        "source": "optimized",
        "run_id": data.meta["run_id"],
        "validation_status": "Validated" if data.meta["validation_passed"] else "Validation failed",
        "queue_status": data.meta["queues"].get(str(rows.iloc[0]["queue_id"]), {}).get("status"),
        "timezone": rows.iloc[0]["timezone"],
        "weeks": weeks,
    }


# --- CSV export ----------------------------------------------------------------------------


@router.get("/schedule/runs/{run_id}/export/{kind}.csv")
def export_csv(
    run_id: str,
    kind: Literal["shifts", "activities", "coverage", "requirements"],
    queue_id: str | None = None,
) -> StreamingResponse:
    data = _load(run_id)
    frame: pd.DataFrame = getattr(data, kind)
    if queue_id:
        if kind == "activities":
            frame = frame[
                frame["agent_id"].isin(
                    data.shifts.loc[data.shifts["queue_id"] == queue_id, "agent_id"]
                )
            ]
        else:
            frame = frame[frame["queue_id"] == queue_id]
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    name = f"schedule_{run_id}_{kind}{'_' + queue_id if queue_id else ''}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
