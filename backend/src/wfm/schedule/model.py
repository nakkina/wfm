"""CP-SAT shift assignment for one queue.

Decision variables: x[agent, date, pattern] ∈ {0, 1} over precomputed patterns (template +
break placement). Coverage per interval = number of chosen patterns on the phone in it.

Hard constraints (configurable POC policies): home queue only (problems are built per queue),
available days, leave, allowed shifts, at most one shift per day, weekly paid minutes within
bounds, minimum rest between consecutive shifts (UTC durations), at most N consecutive
workdays (sliding windows that cross week boundaries; agents start rested).

Coverage is soft: coverage + shortage − excess = required, so a lack of capacity shows up as
shortage instead of making the model infeasible.

Sequential objectives (each stage bounded by the best value found in the previous one):
1. minimise shortage agent-minutes;
2. minimise excess agent-minutes + paid minutes;
3. minimise preference/fairness penalties: preferred shift misses, preferred days off worked,
   weekend and late-shift spread within an employment type, shift-start changes.
A stage stopped by its time limit keeps its best solution (FEASIBLE) as the bound for the next
stage; optimality is claimed only when CP-SAT proves it. Agent names, demographics and
performance scores are never inputs.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ortools.sat.python import cp_model

from wfm.config import StageWeights
from wfm.schedule.inputs import WEEKDAYS, AgentSpec, WeekBounds
from wfm.schedule.patterns import Pattern

STATUS_LABELS = {
    cp_model.OPTIMAL: "Optimal",
    cp_model.FEASIBLE: "Feasible",
    cp_model.INFEASIBLE: "Infeasible",
    cp_model.UNKNOWN: "Unknown",
    cp_model.MODEL_INVALID: "Model invalid",
}


@dataclass(frozen=True)
class Requirement:
    slot: datetime  # UTC start of the interval
    minutes: int
    required: int


@dataclass
class QueueProblem:
    queue_id: str
    agents: list[AgentSpec]
    days: list[date]
    patterns: dict[tuple[str, date], list[Pattern]]  # only workable agent-days are present
    requirements: list[Requirement]
    week_bounds: dict[str, list[WeekBounds]]
    late_shift_codes: set[str]
    weights: StageWeights


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    objective: float | None
    bound: float | None
    seconds: float


@dataclass(frozen=True)
class CoverageRow:
    slot: datetime
    minutes: int
    required: int
    scheduled: int
    shortage: int
    excess: int


@dataclass
class QueueSolution:
    queue_id: str
    status: str  # Optimal / Feasible / Infeasible / Unknown / Model invalid
    stages: list[StageResult]
    assignment: dict[tuple[str, date], Pattern]  # chosen pattern per agent-day (working only)
    coverage: list[CoverageRow] = field(default_factory=list)


def solve_queue(problem: QueueProblem, time_limits: list[float], num_workers: int) -> QueueSolution:
    model = cp_model.CpModel()
    x: dict[tuple[str, date, int], cp_model.IntVar] = {}
    for (agent_id, day), pats in problem.patterns.items():
        for k in range(len(pats)):
            x[agent_id, day, k] = model.new_bool_var(f"x_{agent_id}_{day}_{k}")

    work: dict[tuple[str, date], Any] = {}
    for a in problem.agents:
        for d in problem.days:
            vs = [
                x[a.agent_id, d, k] for k in range(len(problem.patterns.get((a.agent_id, d), [])))
            ]
            if vs:
                model.add_at_most_one(vs)  # at most one shift per local day
            work[a.agent_id, d] = sum(vs) if vs else 0

    # Weekly paid minutes (worked + leave credit) within bounds.
    for a in problem.agents:
        for wb in problem.week_bounds[a.agent_id]:
            paid = sum(
                x[a.agent_id, d, k] * p.paid_minutes
                for d in wb.days
                for k, p in enumerate(problem.patterns.get((a.agent_id, d), []))
            )
            model.add(paid + wb.leave_credit_minutes >= wb.min_paid_minutes)
            model.add(paid + wb.leave_credit_minutes <= wb.max_paid_minutes)

    # Minimum rest between shifts on consecutive days (UTC so DST days are exact).
    for a in problem.agents:
        rest = timedelta(hours=a.min_rest_hours)
        for d in problem.days:
            nxt = d + timedelta(days=1)
            for i, p in enumerate(problem.patterns.get((a.agent_id, d), [])):
                for j, q in enumerate(problem.patterns.get((a.agent_id, nxt), [])):
                    if q.start.astimezone(UTC) - p.end.astimezone(UTC) < rest:
                        model.add_bool_or(
                            [x[a.agent_id, d, i].negated(), x[a.agent_id, nxt, j].negated()]
                        )

    # At most N consecutive workdays: every window of N+1 days has a day off.
    for a in problem.agents:
        n = a.max_consecutive_days
        for start in range(len(problem.days) - n):
            window = problem.days[start : start + n + 1]
            model.add(sum(work[a.agent_id, d] for d in window) <= n)

    # Coverage with explicit shortage and excess.
    covering: dict[datetime, list[cp_model.IntVar]] = defaultdict(list)
    for (agent_id, day), pats in problem.patterns.items():
        for k, p in enumerate(pats):
            for slot in p.on_phone_slots:
                covering[slot].append(x[agent_id, day, k])
    requirement = {r.slot: r for r in problem.requirements}
    slots = sorted(set(requirement) | set(covering))
    minutes = {
        s: requirement[s].minutes if s in requirement else _slot_minutes(problem) for s in slots
    }
    shortage, excess = {}, {}
    for s in slots:
        req = requirement[s].required if s in requirement else 0
        cov = covering.get(s, [])
        shortage[s] = model.new_int_var(0, req, f"short_{s:%Y%m%d%H%M}")
        excess[s] = model.new_int_var(0, len(cov), f"excess_{s:%Y%m%d%H%M}")
        model.add(sum(cov) + shortage[s] - excess[s] == req)

    shortage_obj = sum(shortage[s] * minutes[s] for s in slots)
    paid_obj = sum(
        x[a_id, d, k] * p.paid_minutes
        for (a_id, d), pats in problem.patterns.items()
        for k, p in enumerate(pats)
    )
    excess_obj = sum(excess[s] * minutes[s] for s in slots) + paid_obj
    preference_obj = _preference_objective(model, problem, x, work)

    stages: list[StageResult] = []
    best: cp_model.CpSolver | None = None
    values: dict[tuple[str, date, int], int] = {}
    for name, objective, limit in zip(
        ["shortage", "excess_and_paid", "preferences"],
        [shortage_obj, excess_obj, preference_obj],
        time_limits,
        strict=True,
    ):
        model.minimize(objective)
        if values:
            model.clear_hints()  # type: ignore[no-untyped-call]
            for key, v in values.items():
                model.add_hint(x[key], v)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = limit
        solver.parameters.num_workers = num_workers
        started = time.perf_counter()
        solve_status = solver.solve(model)
        elapsed = time.perf_counter() - started
        has_solution = solve_status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        stages.append(
            StageResult(
                stage=name,
                status=STATUS_LABELS.get(solve_status, "Unknown"),
                objective=solver.objective_value if has_solution else None,
                bound=solver.best_objective_bound if has_solution else None,
                seconds=round(elapsed, 3),
            )
        )
        if not has_solution:
            break  # keep the previous stage's solution (if any)
        best = solver
        values = {key: int(solver.value(var)) for key, var in x.items()}
        # Bound the next stage by what this one achieved (optimal or not).
        model.add(objective <= round(solver.objective_value))

    if best is None:
        label = stages[0].status if stages else "Unknown"
        return QueueSolution(problem.queue_id, label, stages, {})

    assignment = {
        (a_id, d): problem.patterns[a_id, d][k] for (a_id, d, k), v in values.items() if v
    }
    solved = [s for s in stages if s.status in ("Optimal", "Feasible")]
    # "Optimal" only when every stage ran and CP-SAT proved each one optimal.
    overall = (
        "Optimal" if len(solved) == 3 and all(s.status == "Optimal" for s in solved) else "Feasible"
    )
    return QueueSolution(
        problem.queue_id, overall, stages, assignment, _coverage(problem, assignment)
    )


def _slot_minutes(problem: QueueProblem) -> int:
    return problem.requirements[0].minutes if problem.requirements else 15


def _coverage(
    problem: QueueProblem, assignment: dict[tuple[str, date], Pattern]
) -> list[CoverageRow]:
    scheduled: dict[datetime, int] = defaultdict(int)
    for p in assignment.values():
        for s in p.on_phone_slots:
            scheduled[s] += 1
    requirement = {r.slot: r for r in problem.requirements}
    rows = []
    for s in sorted(set(requirement) | set(scheduled)):
        req = requirement[s].required if s in requirement else 0
        mins = requirement[s].minutes if s in requirement else _slot_minutes(problem)
        sch = scheduled.get(s, 0)
        rows.append(CoverageRow(s, mins, req, sch, max(req - sch, 0), max(sch - req, 0)))
    return rows


def _preference_objective(
    model: cp_model.CpModel,
    problem: QueueProblem,
    x: dict[tuple[str, date, int], cp_model.IntVar],
    work: dict[tuple[str, date], Any],
) -> Any:
    w = problem.weights
    terms: list[Any] = []
    horizon_days = len(problem.days)

    for a in problem.agents:
        for d in problem.days:
            pats = problem.patterns.get((a.agent_id, d), [])
            if a.preferred_shift:
                misses = [
                    x[a.agent_id, d, k]
                    for k, p in enumerate(pats)
                    if p.shift_code != a.preferred_shift
                ]
                if misses:
                    terms.append(w.preferred_shift_miss * sum(misses))
            if WEEKDAYS[d.weekday()] in a.preferred_days_off and pats:
                terms.append(w.preferred_day_off_worked * work[a.agent_id, d])
            # Consistent starts: penalise working consecutive days on different start times.
            nxt = problem.patterns.get((a.agent_id, d + timedelta(days=1)), [])
            for i, p in enumerate(pats):
                for j, q in enumerate(nxt):
                    if p.start.time() != q.start.time():
                        change = model.new_bool_var("")
                        model.add(
                            change
                            >= x[a.agent_id, d, i] + x[a.agent_id, d + timedelta(days=1), j] - 1
                        )
                        terms.append(w.start_change * change)

    # Fairness within comparable groups (same employment type, so contracted hours match).
    groups: dict[str, list[AgentSpec]] = defaultdict(list)
    for a in problem.agents:
        groups[a.employment_type].append(a)
    for members in groups.values():
        weekend = [a for a in members if a.available_days & {"Sat", "Sun"}]
        _add_spread(
            model,
            terms,
            w.weekend_spread,
            horizon_days,
            [sum(work[a.agent_id, d] for d in problem.days if d.weekday() >= 5) for a in weekend],
        )
        late = [a for a in members if set(a.allowed_shifts) & problem.late_shift_codes]
        _add_spread(
            model,
            terms,
            w.late_shift_spread,
            horizon_days,
            [
                sum(
                    x[a.agent_id, d, k]
                    for d in problem.days
                    for k, p in enumerate(problem.patterns.get((a.agent_id, d), []))
                    if p.shift_code in problem.late_shift_codes
                )
                for a in late
            ],
        )
    return sum(terms) if terms else 0


def _add_spread(
    model: cp_model.CpModel,
    terms: list[Any],
    weight: int,
    upper: int,
    counts: list[Any],
) -> None:
    """Adds weight × (max − min) of the counts, so the burden is shared evenly."""
    if len(counts) < 2 or weight == 0:
        return
    vars_ = []
    for c in counts:
        v = model.new_int_var(0, upper, "")
        model.add(v == c)
        vars_.append(v)
    hi, lo = model.new_int_var(0, upper, ""), model.new_int_var(0, upper, "")
    model.add_max_equality(hi, vars_)
    model.add_min_equality(lo, vars_)
    terms.append(weight * (hi - lo))
