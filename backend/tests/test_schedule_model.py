"""Pattern building, the CP-SAT model and the independent validator on small synthetic problems."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from wfm.config import StageWeights
from wfm.io.agents import QueueInfo, ShiftTemplate
from wfm.schedule.inputs import (
    WEEKDAYS,
    AgentSpec,
    Exclusion,
    capacity_conflicts,
    horizon_weeks,
    week_bounds,
)
from wfm.schedule.model import QueueProblem, Requirement, solve_queue
from wfm.schedule.patterns import PatternError, build_patterns
from wfm.schedule.plan import build_plan
from wfm.validate.schedule import validate_schedule

TZ = ZoneInfo("America/New_York")
QUEUE = QueueInfo(
    queue_id="Q-1",
    timezone="America/New_York",
    open_days=";".join(WEEKDAYS),
    open_time=time(6),
    close_time=time(23),
)


def template(
    code: str, start: int, plan: str = "WP-FT", paid: float = 8, **kw: object
) -> ShiftTemplate:
    elapsed = paid + (0.5 if plan == "WP-FT" else 0)
    end = datetime(2000, 1, 3, start) + timedelta(hours=elapsed)
    fields: dict[str, object] = {
        "work_plan_id": plan,
        "shift_code": code,
        "shift_name": code.upper(),
        "start_time": time(start),
        "end_time": end.time(),
        "paid_hours": paid,
        "paid_break_count": 2 if plan == "WP-FT" else 1,
        "paid_break_minutes": 15,
        "unpaid_meal_minutes": 30 if plan == "WP-FT" else 0,
        "break1_start_offset_min": 120,
        "meal_start_offset_min": 240 if plan == "WP-FT" else None,
        "break2_start_offset_min": 375 if plan == "WP-FT" else None,
        "minimum_rest_hours": 11,
        "max_consecutive_workdays": 5,
        "workdays_per_week": 5,
    }
    fields.update(kw)
    return ShiftTemplate.model_validate(fields)


EARLY, LATE = template("s1", 6), template("s3", 14)  # late ends 22:30, early starts 06:00
PT = template("s1", 8, plan="WP-PT", paid=4)


def agent(agent_id: str, **kw: object) -> AgentSpec:
    fields: dict[str, object] = {
        "agent_id": agent_id,
        "agent_name": f"Agent {agent_id}",
        "bu_id": "BU-1",
        "mu_id": "MU-1",
        "queue_id": "Q-1",
        "employment_type": "Full-time",
        "work_plan_id": "WP-FT",
        "weekly_min_minutes": 2400,
        "weekly_max_minutes": 2400,
        "allowed_shifts": ("s1", "s3"),
        "preferred_shift": "s1",
        "available_days": frozenset(WEEKDAYS),
        "preferred_days_off": frozenset({"Sat", "Sun"}),
        "min_rest_hours": 11.0,
        "max_consecutive_days": 5,
    }
    fields.update(kw)
    return AgentSpec(**fields)  # type: ignore[arg-type]


# --- Patterns -----------------------------------------------------------------------------


def test_full_time_pattern_accounting_and_placement() -> None:
    [p] = build_patterns(template("s2", 10), date(2026, 9, 23), TZ, 15, 0)
    kinds = [a.kind for a in p.activities]
    assert kinds == [
        "On Phone",
        "Paid Break",
        "On Phone",
        "Unpaid Meal",
        "On Phone",
        "Paid Break",
        "On Phone",
    ]
    assert (p.paid_minutes, p.unpaid_minutes) == (480, 30)
    assert p.activities[1].start.time() == time(12, 0)  # +120
    assert p.activities[3].start.time() == time(14, 0)  # meal +240
    assert p.activities[5].start.time() == time(16, 15)  # +375
    assert all(a.end == b.start for a, b in zip(p.activities, p.activities[1:], strict=False))
    assert len(p.on_phone_slots) == (480 - 30) // 15  # breaks and meal give no coverage


def test_part_time_pattern_has_one_paid_break() -> None:
    [p] = build_patterns(PT, date(2026, 9, 23), TZ, 15, 0)
    assert [a.kind for a in p.activities] == ["On Phone", "Paid Break", "On Phone"]
    assert (p.paid_minutes, p.unpaid_minutes) == (240, 0)


def test_stagger_stays_inside_placement_windows() -> None:
    pats = build_patterns(template("s2", 10), date(2026, 9, 23), TZ, 15, 1)
    assert len(pats) == 27  # 3 × 3 × 3 placements
    for p in pats:
        offsets = [
            (a.start - p.start).total_seconds() / 60 for a in p.activities if a.kind != "On Phone"
        ]
        assert 90 <= offsets[0] <= 150 and 180 <= offsets[1] <= 300 and 330 <= offsets[2] <= 420


def test_inconsistent_templates_rejected() -> None:
    with pytest.raises(PatternError, match="paid hours"):
        build_patterns(template("s2", 10, paid_hours=7.5), date(2026, 9, 23), TZ, 15, 0)
    with pytest.raises(PatternError, match="aligned"):
        build_patterns(
            template("s2", 10, start_time=time(10, 5), end_time=time(18, 35)),
            date(2026, 9, 23),
            TZ,
            15,
            0,
        )


def test_overnight_shift_across_dst_keeps_real_paid_time() -> None:
    night = template("n", 22, end_time=time(6, 30))
    [p] = build_patterns(night, date(2026, 10, 31), TZ, 15, 0)  # clocks go back Nov 1, 02:00
    assert p.paid_minutes == 480  # real elapsed time, not wall clock
    assert p.end.astimezone(UTC) - p.start.astimezone(UTC) == timedelta(hours=8, minutes=30)
    assert p.end.time() == time(5, 30)  # an hour earlier on the wall clock


# --- Week bounds --------------------------------------------------------------------------


def test_partial_week_bounds_are_prorated_by_available_days() -> None:
    days = [date(2026, 9, 23) + timedelta(days=i) for i in range(21)]  # Wed → Tue
    weeks = horizon_weeks(days)
    a = agent("A")
    b = [week_bounds(a, ws, ds, 480, 5) for ws, ds in weeks.items()]
    assert [(x.min_paid_minutes / 60, x.max_paid_minutes / 60) for x in b] == [
        (24, 32),
        (40, 40),
        (40, 40),
        (8, 16),
    ]
    weekday_only = agent("B", available_days=frozenset(WEEKDAYS[:5]))
    first = week_bounds(weekday_only, *next(iter(weeks.items())), 480, 5)
    assert (first.min_paid_minutes, first.max_paid_minutes) == (1440, 1440)  # Wed–Fri: 3 of 5 days


def test_leave_credit_and_conflict_detection() -> None:
    days = tuple(date(2026, 9, 28) + timedelta(days=i) for i in range(7))
    on_leave = agent("A", available_days=frozenset(WEEKDAYS[:5]), leave_dates=frozenset(days[:2]))
    b = week_bounds(on_leave, days[0], days, 480, 5)
    assert b.leave_credit_minutes == 960
    assert capacity_conflicts(on_leave, b, 480) is None  # 3 shifts + 2 leave days = 40 h
    short = agent("B", available_days=frozenset(WEEKDAYS[:3]))
    assert "needs 40 paid h" in (
        capacity_conflicts(short, week_bounds(short, days[0], days, 480, 5), 480) or ""
    )


# --- Model --------------------------------------------------------------------------------


def problem(
    agents: list[AgentSpec], days: list[date], required: int, templates: list[ShiftTemplate]
) -> QueueProblem:
    by_code = {t.shift_code: t for t in templates}
    patterns = {}
    for a in agents:
        for d in days:
            if WEEKDAYS[d.weekday()] in a.available_days and d not in a.leave_dates:
                patterns[a.agent_id, d] = [
                    p
                    for c in a.allowed_shifts
                    if c in by_code
                    for p in build_patterns(by_code[c], d, TZ, 15, 0)
                ]
    reqs = []
    for d in days:
        t = datetime.combine(d, time(9), tzinfo=TZ).astimezone(UTC)
        while t < datetime.combine(d, time(21), tzinfo=TZ).astimezone(UTC):
            reqs.append(Requirement(t, 15, required))
            t += timedelta(minutes=15)
    weeks = horizon_weeks(days)
    return QueueProblem(
        queue_id="Q-1",
        agents=agents,
        days=days,
        patterns=patterns,
        requirements=reqs,
        week_bounds={
            a.agent_id: [week_bounds(a, ws, ds, 480, 5) for ws, ds in weeks.items()] for a in agents
        },
        late_shift_codes={"s3"},
        weights=StageWeights(),
    )


WEEK = [date(2026, 9, 28) + timedelta(days=i) for i in range(14)]  # two full Mon–Sun weeks


def solve(p: QueueProblem):  # type: ignore[no-untyped-def]
    return solve_queue(p, [10, 5, 5], 4)


def test_model_respects_hard_rules() -> None:
    agents = [agent(f"A{i}") for i in range(4)] + [
        agent("W", available_days=frozenset(WEEKDAYS[:5]), allowed_shifts=("s1",)),
    ]
    sol = solve(problem(agents, WEEK, 3, [EARLY, LATE]))
    assert sol.status in ("Optimal", "Feasible")
    for a in agents:
        worked = sorted(d for (aid, d) in sol.assignment if aid == a.agent_id)
        assert len(worked) == 10  # 40 h/week in two full weeks, 8 h shifts
        for ws in (WEEK[0], WEEK[7]):
            assert sum(1 for d in worked if ws <= d < ws + timedelta(days=7)) == 5
        run = best = 0
        for d in WEEK:
            run = run + 1 if (a.agent_id, d) in sol.assignment else 0
            best = max(best, run)
        assert best <= 5
        for d in worked:  # rest: never late then early next day (7.5 h)
            nxt = sol.assignment.get((a.agent_id, d + timedelta(days=1)))
            if nxt and sol.assignment[a.agent_id, d].shift_code == "s3":
                assert nxt.shift_code != "s1"
    w_days = [d for (aid, d) in sol.assignment if aid == "W"]
    assert all(d.weekday() < 5 for d in w_days)
    assert all(sol.assignment["W", d].shift_code == "s1" for d in w_days)


def test_leave_days_are_not_worked_and_reduce_required_shifts() -> None:
    a = agent("L", leave_dates=frozenset({WEEK[0], WEEK[1]}))
    sol = solve(problem([a], WEEK, 1, [EARLY, LATE]))
    worked = {d for (_, d) in sol.assignment}
    assert not worked & {WEEK[0], WEEK[1]}
    assert sum(1 for d in worked if d < WEEK[7]) == 3  # 2 leave days credit 16 h


def test_insufficient_capacity_shows_as_shortage_not_infeasible() -> None:
    sol = solve(problem([agent("A"), agent("B")], WEEK, 5, [EARLY, LATE]))
    assert sol.status in ("Optimal", "Feasible")
    assert sum(c.shortage for c in sol.coverage) > 0
    for c in sol.coverage:
        assert c.shortage == max(c.required - c.scheduled, 0)
        assert c.excess == max(c.scheduled - c.required, 0)


def test_impossible_hours_are_reported_infeasible() -> None:
    a = agent("A", available_days=frozenset(WEEKDAYS[:3]))  # 3 days can't make 40 h
    sol = solve(problem([a], WEEK, 1, [EARLY, LATE]))
    assert sol.status == "Infeasible"
    assert sol.assignment == {}


def test_stages_report_status_objective_and_bound() -> None:
    sol = solve(problem([agent(f"A{i}") for i in range(3)], WEEK, 2, [EARLY, LATE]))
    assert [s.stage for s in sol.stages] == ["shortage", "excess_and_paid", "preferences"]
    for s in sol.stages:
        assert s.status in ("Optimal", "Feasible")
        assert s.objective is not None and s.bound is not None and s.bound <= s.objective + 1e-6


# --- Plan output and independent validation ------------------------------------------------


def plan_and_validate(agents: list[AgentSpec], leave: dict[str, set[date]] | None = None):  # type: ignore[no-untyped-def]
    templates = [EARLY, LATE]
    sol = solve(problem(agents, WEEK, 2, templates))
    roster = pd.DataFrame(
        [
            {
                "agent_id": a.agent_id,
                "agent_name": a.agent_name,
                "bu_id": a.bu_id,
                "mu_id": a.mu_id,
                "queue_id": a.queue_id,
                "work_plan_id": a.work_plan_id,
                "allowed_shifts": ";".join(a.allowed_shifts),
                "available_days": ";".join(d for d in WEEKDAYS if d in a.available_days),
                "weekly_min_hours": a.weekly_min_minutes / 60,
                "weekly_max_hours": a.weekly_max_minutes / 60,
                "minimum_rest_hours": a.min_rest_hours,
                "max_consecutive_workdays": a.max_consecutive_days,
            }
            for a in agents
        ]
    )
    requirements = pd.DataFrame(
        [
            {
                "queue_id": "Q-1",
                "interval_start": r.slot.astimezone(TZ),
                "required_on_phone": r.required,
                "kind": "open",
            }
            for r in problem(agents, WEEK, 2, templates).requirements
        ]
    )
    shifts, activities, coverage = build_plan(
        "run-1",
        WEEK,
        agents,
        [],
        roster,
        {"Q-1": sol},
        templates,
        {"Q-1": "America/New_York"},
        requirements,
    )
    report = validate_schedule(
        shifts, activities, coverage, requirements, roster, templates, WEEK, leave or {}, 15, 0
    )
    return shifts, activities, coverage, roster, requirements, report


def test_plan_is_complete_and_passes_validation() -> None:
    agents = [agent("A0"), agent("A1", leave_dates=frozenset({WEEK[2]})), agent("A2")]
    shifts, activities, coverage, *_, report = plan_and_validate(agents, {"A1": {WEEK[2]}})
    assert report.passed, report.violations[:5]
    assert len(shifts) == 3 * 14
    assert set(shifts["status"]) == {"Working", "Off", "Leave"}
    leave = shifts[shifts["status"] == "Leave"].iloc[0]
    assert (leave["agent_id"], leave["work_date"], leave["paid_minutes"]) == (
        "A1",
        WEEK[2].isoformat(),
        480,
    )
    working = shifts[shifts["status"] == "Working"]
    assert (working["paid_minutes"] == 480).all() and (working["unpaid_minutes"] == 30).all()
    assert set(activities["activity_type"]) == {"On Phone", "Paid Break", "Unpaid Meal", "Leave"}
    assert (activities.loc[activities["activity_type"] == "On Phone", "queue_id"] == "Q-1").all()
    assert activities["start"].str.contains(r"[+-]\d\d:\d\d$").all()  # timezone-aware


def test_validator_catches_violations() -> None:
    agents = [agent("A0"), agent("A1")]
    shifts, activities, coverage, roster, requirements, report = plan_and_validate(agents)
    assert report.passed
    templates = [EARLY, LATE]

    # Remove one activity (creates a gap), move one agent to another queue, drop a status row.
    broken_acts = activities.drop(
        index=activities.index[activities["activity_type"] == "Paid Break"][0]
    )
    broken_shifts = shifts.copy()
    first_working = broken_shifts.index[broken_shifts["status"] == "Working"][0]
    broken_shifts.loc[first_working, "queue_id"] = "Q-9"
    broken_shifts = broken_shifts.drop(index=broken_shifts.index[-1])
    # Coverage table no longer matches the activities.
    broken_cov = coverage.copy()
    broken_cov.loc[broken_cov.index[0], "scheduled_on_phone"] += 1

    r = validate_schedule(
        broken_shifts, broken_acts, broken_cov, requirements, roster, templates, WEEK, {}, 15, 0
    )
    assert not r.passed
    for rule in [
        "activity timeline complete",
        "home queue",
        "one status per agent per day",
        "coverage recount",
    ]:
        assert r.checks[rule] > 0, rule


def test_excluded_agents_still_get_daily_status() -> None:
    a = agent("A0")
    templates = [EARLY, LATE]
    sol = solve(problem([a], WEEK, 1, templates))
    roster = pd.DataFrame(
        [
            {
                "agent_id": "A0",
                "agent_name": "A0",
                "bu_id": "BU-1",
                "mu_id": "MU-1",
                "queue_id": "Q-1",
            },
            {
                "agent_id": "X",
                "agent_name": "X",
                "bu_id": "BU-1",
                "mu_id": "MU-1",
                "queue_id": "Q-1",
            },
        ]
    )
    shifts, *_ = build_plan(
        "r",
        WEEK,
        [a],
        [Exclusion("X", "Q-1", "not schedulable")],
        roster,
        {"Q-1": sol},
        templates,
        {"Q-1": "America/New_York"},
        pd.DataFrame(columns=["queue_id", "interval_start", "kind"]),
    )
    x = shifts[shifts["agent_id"] == "X"]
    assert len(x) == 14 and (x["status"] == "Unscheduled").all()
    assert x["note"].str.contains("not schedulable").all()
