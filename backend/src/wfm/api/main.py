import logging
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query

from wfm import __version__
from wfm.api.schedule import agent_plan
from wfm.api.schedule import router as schedule_router
from wfm.config import load_config, resolve
from wfm.forecast.api_models import QueueForecast, queue_forecast
from wfm.forecast.daily import ForecastError, forecast_all
from wfm.forecast.store import StoredForecast, file_fingerprint, load_matching, save_run
from wfm.hierarchy import Organization, load_hierarchy, with_agent_counts
from wfm.io.acd import AcdError, AcdHistory, QueueHistory, load_acd_history, queue_history
from wfm.io.agents import (
    AgentRoster,
    ColumnInfo,
    RosterError,
    agents_in_queue,
    json_value,
    load_agent_roster,
)
from wfm.schedule.mock import MockScheduleError, mock_schedule
from wfm.schedule.models import AgentSchedule

logger = logging.getLogger("wfm")


def _warm_forecast() -> None:
    try:
        current_forecast()
    except Exception:  # startup must not fail; the endpoint will report the error on request
        logger.exception("Background forecast preparation failed")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Load (or generate once) the saved forecast run in the background so the first
    # Forecasts tab request doesn't wait for StatsForecast.
    threading.Thread(target=_warm_forecast, name="forecast-warmup", daemon=True).start()
    yield


app = FastAPI(title="Workforce Management - POC by Akkina", version=__version__, lifespan=lifespan)
app.include_router(schedule_router)


# Loaded inputs keyed by name; each entry is re-read only when one of its files changes.
_cache: dict[str, tuple[tuple[tuple[Path, float], ...], Any]] = {}


def _cached[T](name: str, paths: list[Path], load: Callable[[], T]) -> T:
    try:
        stamp = tuple((p, p.stat().st_mtime) for p in paths)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"Input file not found: {e.filename}") from e
    hit = _cache.get(name)
    if hit is None or hit[0] != stamp:
        try:
            _cache[name] = (stamp, load())
        except (RosterError, AcdError, ValueError) as e:
            raise HTTPException(status_code=500, detail=f"Invalid input for {name}: {e}") from e
    value: T = _cache[name][1]
    return value


@dataclass(frozen=True)
class LoadedData:
    org: Organization
    roster: AgentRoster


def loaded_data() -> LoadedData:
    paths = load_config().paths
    hierarchy_path, agents_path = resolve(paths.hierarchy_yaml), resolve(paths.agents_xlsx)

    def load() -> LoadedData:
        org = load_hierarchy(hierarchy_path)
        return LoadedData(org=org, roster=load_agent_roster(agents_path, org))

    return _cached("agents", [hierarchy_path, agents_path], load)


def acd_history() -> AcdHistory:
    paths = load_config().paths
    hierarchy_path, acd_path = resolve(paths.hierarchy_yaml), resolve(paths.acd_xlsx)
    return _cached(
        "acd",
        [hierarchy_path, acd_path],
        lambda: load_acd_history(acd_path, load_hierarchy(hierarchy_path)),
    )


_forecast_lock = threading.Lock()


def current_forecast() -> StoredForecast:
    """Forecast for the current ACD file and settings: reuse a saved run or generate one."""
    config = load_config()
    hierarchy_path, acd_path = resolve(config.paths.hierarchy_yaml), resolve(config.paths.acd_xlsx)
    runs_dir = resolve(config.paths.runs_dir)
    settings = config.forecast

    def load() -> StoredForecast:
        fingerprint = file_fingerprint(acd_path)
        stored = load_matching(runs_dir, fingerprint, settings)
        if stored is not None:
            return stored
        started = time.perf_counter()
        try:
            result = forecast_all(acd_history().daily, settings)
        except ForecastError as e:
            raise HTTPException(status_code=422, detail=f"Cannot forecast: {e}") from e
        return save_run(
            runs_dir, result, settings, acd_path, fingerprint, time.perf_counter() - started
        )

    # One generation at a time; concurrent requests wait and then reuse the saved run.
    with _forecast_lock:
        return _cached(f"forecast:{settings.model_dump_json()}", [hierarchy_path, acd_path], load)


def _require_queue(data: LoadedData, queue_id: str) -> None:
    if queue_id not in {q.id for q in data.org.queues()}:
        raise HTTPException(status_code=404, detail=f"Unknown queue: {queue_id}")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/hierarchy")
def hierarchy() -> Organization:
    data = loaded_data()
    counts = data.roster.agents["queue_id"].value_counts().to_dict()
    return with_agent_counts(data.org, {str(k): int(v) for k, v in counts.items()})


@app.get("/api/agents/columns")
def agent_columns() -> list[ColumnInfo]:
    return loaded_data().roster.columns


@app.get("/api/queues/{queue_id}/agents")
def queue_agents(queue_id: str) -> list[dict[str, object]]:
    data = loaded_data()
    _require_queue(data, queue_id)
    return agents_in_queue(data.roster, queue_id)


@app.get("/api/queues/{queue_id}/history")
def queue_history_daily(queue_id: str) -> QueueHistory:
    _require_queue(loaded_data(), queue_id)
    history = queue_history(acd_history(), queue_id)
    if history is None:
        raise HTTPException(status_code=404, detail=f"No ACD history for queue {queue_id}")
    return history


@app.get("/api/queues/{queue_id}/forecast")
def queue_forecast_daily(queue_id: str) -> QueueForecast:
    _require_queue(loaded_data(), queue_id)
    forecast = queue_forecast(current_forecast(), queue_id)
    if forecast is None:
        raise HTTPException(status_code=404, detail=f"No forecast for queue {queue_id}")
    return forecast


PROFILE_FIELDS = [
    "agent_id", "agent_name", "bu_name", "mu_name", "queue_id", "queue_name", "team_id", "role",
    "employment_type", "primary_skill", "additional_skills", "skill_proficiency", "languages",
    "work_plan_id", "weekly_contracted_hours", "preferred_shift", "allowed_shifts",
    "available_days", "preferred_days_off",
]  # fmt: skip


@app.get("/api/agents/{agent_id}/schedule")
def agent_schedule(
    agent_id: str, weeks: Annotated[int | None, Query(ge=1, le=6)] = None
) -> dict[str, Any]:
    """The agent's plan from the latest schedule run; a labelled mock until one exists."""
    data = loaded_data()
    rows = data.roster.agents[data.roster.agents["agent_id"] == agent_id]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
    agent = {str(k): v for k, v in rows.iloc[0].to_dict().items()}
    profile = {f: json_value(agent.get(f)) for f in PROFILE_FIELDS if f in agent}
    plan = agent_plan(agent_id)
    if plan is not None:
        return {"agent": profile, "schedule": plan}

    settings = load_config().schedule
    tz = ZoneInfo(load_config().timezone)
    try:
        mock = mock_schedule(
            agent, data.roster.shifts, settings.horizon_start, weeks or settings.horizon_weeks
        )
    except MockScheduleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"agent": profile, "schedule": _mock_as_plan(mock, tz)}


MOCK_ACTIVITY = {"work": "On Phone", "break": "Paid Break", "meal": "Unpaid Meal"}


def _mock_as_plan(mock: AgentSchedule, tz: ZoneInfo) -> dict[str, Any]:
    """The mock in the same shape as a real plan, so the UI has one format."""

    def iso(d: date, t: dtime | None) -> str | None:
        return datetime.combine(d, t, tzinfo=tz).isoformat() if t else None

    weeks = []
    for w in mock.weeks:
        days = []
        for d in w.days:
            days.append(
                {
                    "date": d.date.isoformat(),
                    "weekday": d.weekday,
                    "status": "Off" if d.off else "Working",
                    "off": d.off,
                    "shift_code": d.shift_code,
                    "shift_name": d.shift_name,
                    "start": iso(d.date, d.start),
                    "end": iso(d.date, d.end),
                    "paid_hours": d.paid_hours,
                    "preferred_shift_matched": None,
                    "preferred_day_off": None,
                    "note": None,
                    "activities": [
                        {
                            "activity_type": MOCK_ACTIVITY[s.kind],
                            "start": iso(d.date, s.start),
                            "end": iso(d.date, s.end),
                            "paid": s.kind != "meal",
                        }
                        for s in d.segments
                    ],
                }
            )
        weeks.append(
            {
                "week_start": w.week_start.isoformat(),
                "paid_hours": w.paid_hours,
                "days_off": w.days_off,
                "leave_days": 0,
                "preferred_shift_matches": None,
                "working_days": sum(1 for d in w.days if not d.off),
                "partial": False,
                "days": days,
            }
        )
    return {
        "source": "mock",
        "run_id": None,
        "validation_status": mock.validation_status,
        "queue_status": None,
        "timezone": str(tz),
        "weeks": weeks,
    }
