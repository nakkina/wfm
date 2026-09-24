"""Schedule generation run: saved forecast → requirements → CP-SAT per queue → validated plan.

Runs in a worker subprocess (`python -m wfm.schedule.run --run-dir DIR [--overrides JSON]`)
so the API stays responsive; progress is written to DIR/status.json.

Erlang C gives approximate interval requirements and CP-SAT optimises assignments against
them. A feasible schedule does not by itself prove real-world service levels will be met.
"""

import argparse
import json
import os
import platform
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from wfm.config import Config, load_config, resolve
from wfm.forecast.store import file_fingerprint, load_matching
from wfm.hierarchy import load_hierarchy
from wfm.io.acd import load_acd_history
from wfm.io.agents import load_agent_roster
from wfm.schedule.inputs import (
    WEEKDAYS,
    build_agents,
    capacity_conflicts,
    horizon_weeks,
    load_leave,
    week_bounds,
)
from wfm.schedule.model import QueueProblem, QueueSolution, Requirement, solve_queue
from wfm.schedule.patterns import Pattern, build_patterns
from wfm.schedule.plan import build_plan
from wfm.schedule.store import write_status
from wfm.staffing.intraday import learn_hourly_profile
from wfm.staffing.requirements import build_requirements
from wfm.validate.schedule import validate_schedule

Progress = Callable[[str, int, int], None]


class ScheduleRunError(RuntimeError):
    """Inputs can't support a schedule run (e.g. no saved forecast)."""


def apply_overrides(config: Config, overrides: dict[str, Any]) -> Config:
    data = config.model_dump()
    for section in ("staffing", "scheduling"):
        data[section].update(overrides.get(section, {}))
    return Config.model_validate(data)


def generate(config: Config, run_dir: Path, run_id: str, progress: Progress) -> dict[str, Any]:
    started = time.perf_counter()
    paths = config.paths
    progress("Loading roster, ACD history and saved forecast", 0, 1)
    org = load_hierarchy(resolve(paths.hierarchy_yaml))
    roster = load_agent_roster(resolve(paths.agents_xlsx), org)
    if not roster.queues:
        raise ScheduleRunError("Agents workbook has no Queues sheet (opening hours are required)")
    acd_path = resolve(paths.acd_xlsx)
    forecast = load_matching(resolve(paths.runs_dir), file_fingerprint(acd_path), config.forecast)
    if forecast is None:
        raise ScheduleRunError(
            "No saved forecast matches the current ACD file and settings; "
            "open a queue's Forecasts tab first"
        )
    acd = load_acd_history(acd_path, org)

    progress("Computing interval staffing requirements", 0, 1)
    profile = learn_hourly_profile(acd.hourly)
    requirements = build_requirements(forecast.points, profile, roster.queues, config.staffing)
    days: list[date] = sorted(set(requirements["date"]))

    leave = load_leave(
        resolve(config.scheduling.leave_csv) if config.scheduling.leave_csv else None,
        set(roster.agents["agent_id"]),
    )
    agents, excluded = build_agents(roster.agents, roster.queues, roster.shifts, leave)
    timezones = {q: info.timezone for q, info in roster.queues.items()}

    # Patterns are identical for every agent on a plan/shift/date: build each once.
    interval = config.staffing.interval_minutes
    cache: dict[tuple[str, str, date], list[Pattern]] = {}
    templates = {(t.work_plan_id, t.shift_code): t for t in roster.shifts}

    def patterns_for(plan: str, code: str, day: date, tz: str) -> list[Pattern]:
        key = (plan, code, day)
        if key not in cache:
            cache[key] = build_patterns(
                templates[plan, code],
                day,
                ZoneInfo(tz),
                interval,
                config.scheduling.break_stagger_slots,
            )
        return cache[key]

    late_codes = _late_shift_codes(roster.shifts)
    weeks = horizon_weeks(days)
    queues = sorted({a.queue_id for a in agents})
    solutions: dict[str, QueueSolution] = {}
    conflicts: list[str] = []
    for i, queue_id in enumerate(queues):
        progress(f"Optimising {queue_id}", i, len(queues))
        info = roster.queues[queue_id]
        members = [a for a in agents if a.queue_id == queue_id]
        patterns: dict[tuple[str, date], list[Pattern]] = {}
        bounds = {}
        for a in members:
            plan_templates = [t for t in roster.shifts if t.work_plan_id == a.work_plan_id]
            shift_paid = round(min(t.paid_hours for t in plan_templates) * 60)
            per_week = plan_templates[0].workdays_per_week
            bounds[a.agent_id] = [
                week_bounds(a, ws, ds, shift_paid, per_week) for ws, ds in weeks.items()
            ]
            for b in bounds[a.agent_id]:
                if msg := capacity_conflicts(a, b, shift_paid):
                    conflicts.append(msg)
            for d in days:
                weekday = WEEKDAYS[d.weekday()]
                if weekday in a.available_days and d not in a.leave_dates and info.open_on(weekday):
                    patterns[a.agent_id, d] = [
                        p
                        for code in a.allowed_shifts
                        for p in patterns_for(a.work_plan_id, code, d, info.timezone)
                    ]
        q_req = requirements[requirements["queue_id"] == queue_id]
        problem = QueueProblem(
            queue_id=queue_id,
            agents=members,
            days=days,
            patterns=patterns,
            requirements=[
                Requirement(pd.Timestamp(s).tz_convert("UTC").to_pydatetime(), round(m), int(r))
                for s, m, r in zip(
                    q_req["interval_start"],
                    q_req["interval_minutes"],
                    q_req["required_on_phone"],
                    strict=True,
                )
            ],
            week_bounds=bounds,
            late_shift_codes=late_codes,
            weights=config.scheduling.weights,
        )
        solutions[queue_id] = solve_queue(
            problem, config.scheduling.stage_time_limit_seconds, config.scheduling.num_workers
        )

    progress("Building and validating shift plans", len(queues), len(queues))
    shifts, activities, coverage = build_plan(
        run_id,
        days,
        agents,
        excluded,
        roster.agents,
        solutions,
        roster.shifts,
        timezones,
        requirements,
    )
    validation = validate_schedule(
        shifts,
        activities,
        coverage,
        requirements,
        roster.agents,
        roster.shifts,
        days,
        leave,
        interval,
        config.scheduling.break_stagger_slots,
    )

    req_out = requirements.copy()
    for col in ("interval_start", "interval_end"):
        req_out[col] = [pd.Timestamp(v).isoformat() for v in req_out[col]]
    req_out.to_csv(run_dir / "requirements.csv", index=False)
    coverage.to_csv(run_dir / "coverage.csv", index=False)
    shifts.to_csv(run_dir / "shifts.csv", index=False)
    activities.to_csv(run_dir / "activities.csv", index=False)
    (run_dir / "validation.json").write_text(json.dumps(validation.to_dict(), indent=2))

    total_short = float((coverage["shortage"] * coverage["interval_minutes"]).sum() / 60)
    meta = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "forecast_run_id": forecast.run_id,
        "inputs": {
            "agents_sha256": file_fingerprint(resolve(paths.agents_xlsx)),
            "acd_sha256": file_fingerprint(acd_path),
        },
        "horizon": {"start": days[0].isoformat(), "end": days[-1].isoformat(), "days": len(days)},
        "config": {
            "staffing": config.staffing.model_dump(mode="json"),
            "scheduling": config.scheduling.model_dump(mode="json"),
        },
        "queues": {
            q: {
                "status": s.status,
                "stages": [st.__dict__ for st in s.stages],
                "shortage_agent_hours": round(
                    float(sum(c.shortage * c.minutes for c in s.coverage) / 60), 2
                ),
            }
            for q, s in solutions.items()
        },
        "status_counts": pd.Series([s.status for s in solutions.values()]).value_counts().to_dict(),
        "agents": {"scheduled": len(agents), "excluded": [e.__dict__ for e in excluded]},
        "conflicts": conflicts,
        "shortage_agent_hours": round(total_short, 2),
        "validation_passed": validation.passed,
        "assumptions": [
            "Intraday profile learned from hourly ACD history "
            "(queue × weekday × hour, event days excluded)",
            "Calls split equally across the open minutes of each hour unless configured; "
            "estimated allocations",
            "AHT held constant within each day",
            "No prior schedule history: agents assumed rested at the horizon start",
            "Partial horizon weeks: paid-hour bounds pro-rated by available days, whole shifts",
            "Intraday availability windows not supplied: agents available for any allowed shift",
            f"Closing allowance: {config.staffing.closing_minutes} min after close "
            "at ceil(last-interval load) agents",
            "Erlang C ignores abandonment; service levels are modelled estimates",
        ],
        "runtime_seconds": round(time.perf_counter() - started, 2),
        "versions": {
            "python": platform.python_version(),
            "ortools": version("ortools"),
            "pandas": version("pandas"),
        },
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return meta


def _late_shift_codes(templates: list[Any]) -> set[str]:
    """Per work plan, the latest-starting template counts as the 'late' shift for fairness."""
    latest: dict[str, Any] = {}
    for t in templates:
        if t.work_plan_id not in latest or t.start_time > latest[t.work_plan_id].start_time:
            latest[t.work_plan_id] = t
    return {t.shift_code for t in latest.values()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a WFM schedule run")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--overrides", default="{}")
    args = parser.parse_args()
    run_dir: Path = args.run_dir
    run_id = run_dir.name
    base = {
        "run_id": run_id,
        "pid": os.getpid(),
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    def progress(message: str, done: int, total: int) -> None:
        write_status(
            run_dir, {**base, "state": "running", "message": message, "done": done, "total": total}
        )

    try:
        config = apply_overrides(load_config(), json.loads(args.overrides))
        meta = generate(config, run_dir, run_id, progress)
        state = "completed_with_shortages" if meta["shortage_agent_hours"] > 0 else "completed"
        if not meta["validation_passed"]:
            state = "failed"
        write_status(
            run_dir,
            {
                **base,
                "state": state,
                "message": "Done",
                "done": 1,
                "total": 1,
                "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
    except Exception as e:  # report every failure to the UI instead of dying silently
        write_status(
            run_dir,
            {
                **base,
                "state": "failed",
                "message": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=5),
                "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
