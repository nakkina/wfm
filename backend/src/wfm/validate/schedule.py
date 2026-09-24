"""Independent validation of a finished schedule.

Deliberately re-derives every rule from the raw roster, the Shifts sheet and the persisted
output tables. It does not import the optimizer's model, patterns or bounds code, so a bug in
the model can't hide itself here.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd

from wfm.io.agents import ShiftTemplate

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
STATUSES = {"Working", "Off", "Leave", "Unscheduled"}
ACTIVITY_TYPES = {"On Phone", "Paid Break", "Unpaid Meal", "Leave"}
BREAK_WINDOWS = {"break1": (90, 150), "meal": (180, 300), "break2": (330, 420)}


@dataclass
class ValidationReport:
    checks: dict[str, int] = field(default_factory=dict)  # rule → number of violations
    violations: list[dict[str, str]] = field(default_factory=list)  # first 200, for display

    def add(self, rule: str, agent: str, day: str, detail: str) -> None:
        self.checks[rule] = self.checks.get(rule, 0) + 1
        if len(self.violations) < 200:
            self.violations.append({"rule": rule, "agent_id": agent, "date": day, "detail": detail})

    def touch(self, rule: str) -> None:
        self.checks.setdefault(rule, 0)

    @property
    def passed(self) -> bool:
        return all(v == 0 for v in self.checks.values())

    def to_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "checks": self.checks, "violations": self.violations}


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _split(value: object) -> set[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return set()
    return {v.strip() for v in str(value).split(";") if v.strip()}


def validate_schedule(
    shifts: pd.DataFrame,
    activities: pd.DataFrame,
    coverage: pd.DataFrame,
    requirements: pd.DataFrame,
    roster: pd.DataFrame,
    templates: list[ShiftTemplate],
    days: list[date],
    leave: dict[str, set[date]],
    interval_minutes: int,
    break_stagger_slots: int,
) -> ValidationReport:
    report = ValidationReport()
    for rule in [
        "one status per agent per day",
        "home queue",
        "allowed shift",
        "available day",
        "no work on leave",
        "activity timeline complete",
        "paid/unpaid accounting",
        "breaks and meal",
        "weekly paid hours",
        "minimum rest",
        "consecutive workdays",
        "coverage recount",
    ]:
        report.touch(rule)

    roster_by_id = roster.set_index("agent_id")
    template = {(t.work_plan_id, t.shift_code): t for t in templates}
    horizon = [d.isoformat() for d in days]

    # 1. Exactly one status row per agent per horizon day.
    counts = shifts.groupby(["agent_id", "work_date"]).size()
    for agent_id in roster["agent_id"]:
        for d in horizon:
            n = int(counts.get((agent_id, d), 0))
            if n != 1:
                report.add("one status per agent per day", agent_id, d, f"{n} rows")
    for r in shifts[~shifts["status"].isin(STATUSES)].itertuples():
        report.add(
            "one status per agent per day", str(r.agent_id), str(r.work_date), f"status {r.status}"
        )

    acts_by_shift: dict[tuple[str, str], list[dict[Any, Any]]] = defaultdict(list)
    for a in activities.to_dict("records"):
        acts_by_shift[str(a["agent_id"]), str(a["work_date"])].append(a)

    working = shifts[shifts["status"] == "Working"]
    for s in working.to_dict("records"):
        agent_id, d = str(s["agent_id"]), str(s["work_date"])
        info = roster_by_id.loc[agent_id]
        # 2. Home queue, allowed shift, available day, not on leave.
        if s["queue_id"] != info["queue_id"]:
            report.add(
                "home queue", agent_id, d, f"scheduled in {s['queue_id']}, home {info['queue_id']}"
            )
        if s["shift_code"] not in _split(info["allowed_shifts"]):
            report.add(
                "allowed shift", agent_id, d, f"{s['shift_code']} not in {info['allowed_shifts']}"
            )
        weekday = DAYS[date.fromisoformat(d).weekday()]
        if weekday not in (_split(info["available_days"]) or set(DAYS)):
            report.add("available day", agent_id, d, f"{weekday} not available")
        if date.fromisoformat(d) in leave.get(agent_id, set()):
            report.add("no work on leave", agent_id, d, "working on a leave day")
        t = template.get((str(info["work_plan_id"]), str(s["shift_code"])))
        if t is None:
            report.add(
                "allowed shift",
                agent_id,
                d,
                f"no template {info['work_plan_id']}/{s['shift_code']}",
            )
            continue
        _check_timeline(
            report,
            s,
            acts_by_shift.get((agent_id, d), []),
            t,
            info,
            interval_minutes,
            break_stagger_slots,
        )

    for s in shifts[shifts["status"] == "Leave"].to_dict("records"):
        acts = acts_by_shift.get((str(s["agent_id"]), str(s["work_date"])), [])
        if len(acts) != 1 or acts[0]["activity_type"] != "Leave":
            report.add(
                "activity timeline complete",
                str(s["agent_id"]),
                str(s["work_date"]),
                "leave day needs one Leave activity",
            )
    for s in shifts[shifts["status"].isin(["Off", "Unscheduled"])].to_dict("records"):
        if acts_by_shift.get((str(s["agent_id"]), str(s["work_date"]))):
            report.add(
                "activity timeline complete",
                str(s["agent_id"]),
                str(s["work_date"]),
                f"{s['status']} day has activities",
            )

    _check_weeks_rest_runs(report, shifts, roster_by_id, template, days)
    _check_coverage(report, activities, coverage, requirements, roster_by_id, interval_minutes)
    return report


def _check_timeline(
    report: ValidationReport,
    shift: dict[Any, Any],
    acts: list[dict[Any, Any]],
    t: ShiftTemplate,
    info: Any,
    interval: int,
    stagger: int,
) -> None:
    agent_id, d = str(shift["agent_id"]), str(shift["work_date"])
    if not acts:
        report.add("activity timeline complete", agent_id, d, "working shift has no activities")
        return
    acts = sorted(acts, key=lambda a: _parse(str(a["start"])))
    start, end = _parse(str(shift["shift_start"])), _parse(str(shift["shift_end"]))
    if _parse(str(acts[0]["start"])) != start or _parse(str(acts[-1]["end"])) != end:
        report.add("activity timeline complete", agent_id, d, "activities don't span the shift")
    for a, b in zip(acts, acts[1:], strict=False):
        if _parse(str(a["end"])) != _parse(str(b["start"])):
            report.add("activity timeline complete", agent_id, d, f"gap or overlap at {a['end']}")
    for a in acts:
        if a["activity_type"] not in ACTIVITY_TYPES - {"Leave"}:
            report.add(
                "activity timeline complete", agent_id, d, f"unexpected {a['activity_type']}"
            )
        if a["activity_type"] == "On Phone" and a["queue_id"] != info["queue_id"]:
            report.add("home queue", agent_id, d, f"on phone for {a['queue_id']}")
        if _parse(str(a["start"])).minute % interval or _parse(str(a["end"])).minute % interval:
            report.add(
                "activity timeline complete", agent_id, d, "activity off the scheduling grid"
            )

    def mins(a: dict[Any, Any]) -> float:
        return (
            _parse(str(a["end"])).astimezone(UTC) - _parse(str(a["start"])).astimezone(UTC)
        ).total_seconds() / 60

    paid = sum(mins(a) for a in acts if a["activity_type"] != "Unpaid Meal")
    unpaid = sum(mins(a) for a in acts if a["activity_type"] == "Unpaid Meal")
    if round(paid) != int(shift["paid_minutes"]) or round(unpaid) != int(shift["unpaid_minutes"]):
        report.add(
            "paid/unpaid accounting",
            agent_id,
            d,
            f"activities give {paid:g} paid / {unpaid:g} unpaid",
        )
    if round(paid) != round(t.paid_hours * 60):
        report.add(
            "paid/unpaid accounting",
            agent_id,
            d,
            f"{paid:g} paid minutes, template {t.paid_hours} h",
        )

    breaks = [a for a in acts if a["activity_type"] == "Paid Break"]
    meals = [a for a in acts if a["activity_type"] == "Unpaid Meal"]
    if len(breaks) != t.paid_break_count or any(
        round(mins(b)) != t.paid_break_minutes for b in breaks
    ):
        report.add(
            "breaks and meal",
            agent_id,
            d,
            f"{len(breaks)} breaks, template {t.paid_break_count}×{t.paid_break_minutes} min",
        )
    if len(meals) != (1 if t.unpaid_meal_minutes else 0) or any(
        round(mins(m)) != t.unpaid_meal_minutes for m in meals
    ):
        report.add(
            "breaks and meal",
            agent_id,
            d,
            f"meal doesn't match template {t.unpaid_meal_minutes} min",
        )
    placed = sorted(breaks + meals, key=lambda a: _parse(str(a["start"])))
    names = (
        (["break1"] if t.paid_break_count >= 1 else [])
        + (["meal"] if t.unpaid_meal_minutes else [])
        + (["break2"] if t.paid_break_count >= 2 else [])
    )
    rules = {
        "break1": t.break1_start_offset_min,
        "meal": t.meal_start_offset_min,
        "break2": t.break2_start_offset_min,
    }
    for name, a in zip(names, placed, strict=False):
        offset = (
            _parse(str(a["start"])).astimezone(UTC) - start.astimezone(UTC)
        ).total_seconds() / 60
        rule = rules[name]
        lo, hi = BREAK_WINDOWS[name]
        ok = (
            (offset == rule)
            if (rule is not None and stagger == 0)
            else (
                lo <= offset <= hi
                if rule is None
                else abs(offset - rule) <= stagger * interval and lo <= offset <= hi
            )
        )
        if not ok:
            report.add("breaks and meal", agent_id, d, f"{name} at +{offset:g} min")


def _check_weeks_rest_runs(
    report: ValidationReport,
    shifts: pd.DataFrame,
    roster_by_id: pd.DataFrame,
    template: dict[tuple[str, str], ShiftTemplate],
    days: list[date],
) -> None:
    weeks: dict[date, list[str]] = defaultdict(list)
    for d in days:
        weeks[d - timedelta(days=d.weekday())].append(d.isoformat())
    scheduled = shifts[shifts["status"] != "Unscheduled"]
    for agent_id, g in scheduled.groupby("agent_id"):
        info = roster_by_id.loc[agent_id]
        plan_templates = [t for (p, _), t in template.items() if p == info["work_plan_id"]]
        shift_min = round(min(t.paid_hours for t in plan_templates) * 60)
        wmin, wmax = (
            round(_num(info["weekly_min_hours"]) * 60),
            round(_num(info["weekly_max_hours"]) * 60),
        )
        available = _split(info["available_days"]) or set(DAYS)
        rows = g.set_index("work_date")
        for week_days in weeks.values():
            paid = sum(
                round(_num(rows.loc[d, "paid_minutes"]))
                for d in week_days
                if d in rows.index and rows.loc[d, "status"] in ("Working", "Leave")
            )
            if len(week_days) == 7:
                lo, hi = wmin, wmax
            else:
                ratio = sum(
                    1 for d in week_days if DAYS[date.fromisoformat(d).weekday()] in available
                ) / len(available)
                lo = math.floor(wmin * ratio / shift_min + 1e-9) * shift_min
                hi = min(wmax, math.ceil(wmax * ratio / shift_min - 1e-9) * shift_min)
            if not lo <= paid <= hi:
                report.add(
                    "weekly paid hours",
                    str(agent_id),
                    week_days[0],
                    f"{paid / 60:g} h outside {lo / 60:g}–{hi / 60:g} h",
                )

        work = g[g["status"] == "Working"].sort_values("work_date")
        rest = timedelta(hours=_num(info["minimum_rest_hours"]))
        prev_end: datetime | None = None
        prev_day: date | None = None
        run = 0
        for s in work.to_dict("records"):
            day = date.fromisoformat(str(s["work_date"]))
            start = _parse(str(s["shift_start"])).astimezone(UTC)
            if prev_end is not None and start - prev_end < rest:
                report.add(
                    "minimum rest",
                    str(agent_id),
                    str(s["work_date"]),
                    f"{(start - prev_end).total_seconds() / 3600:.1f} h rest",
                )
            run = run + 1 if prev_day is not None and day - prev_day == timedelta(days=1) else 1
            if run > _num(info["max_consecutive_workdays"]):
                report.add(
                    "consecutive workdays",
                    str(agent_id),
                    str(s["work_date"]),
                    f"{run} days in a row",
                )
            prev_end, prev_day = _parse(str(s["shift_end"])).astimezone(UTC), day


def _check_coverage(
    report: ValidationReport,
    activities: pd.DataFrame,
    coverage: pd.DataFrame,
    requirements: pd.DataFrame,
    roster_by_id: pd.DataFrame,
    interval: int,
) -> None:
    counted: dict[tuple[str, datetime], int] = defaultdict(int)
    for a in activities[activities["activity_type"] == "On Phone"].to_dict("records"):
        start, end = _parse(str(a["start"])).astimezone(UTC), _parse(str(a["end"])).astimezone(UTC)
        t = start
        while t < end:
            counted[str(a["queue_id"]), t] += 1
            t += timedelta(minutes=interval)
    required = {
        (str(q), pd.Timestamp(s).tz_convert("UTC").to_pydatetime()): int(r)
        for q, s, r in zip(
            requirements["queue_id"],
            requirements["interval_start"],
            requirements["required_on_phone"],
            strict=True,
        )
    }
    for c in coverage.to_dict("records"):
        key = (str(c["queue_id"]), _parse(str(c["interval_start"])).astimezone(UTC))
        sched, req = int(c["scheduled_on_phone"]), int(c["required_on_phone"])
        if counted.get(key, 0) != sched:
            report.add(
                "coverage recount",
                str(c["queue_id"]),
                str(c["interval_start"]),
                f"table {sched}, activities {counted.get(key, 0)}",
            )
        if key in required and required[key] != req:
            report.add(
                "coverage recount",
                str(c["queue_id"]),
                str(c["interval_start"]),
                f"required {req} ≠ requirements {required[key]}",
            )
        if int(c["shortage"]) != max(req - sched, 0) or int(c["excess"]) != max(sched - req, 0):
            report.add(
                "coverage recount",
                str(c["queue_id"]),
                str(c["interval_start"]),
                "shortage/excess arithmetic",
            )


def _num(value: object) -> float:
    """A roster cell as a number (pandas types these cells loosely)."""
    return float(str(value))
