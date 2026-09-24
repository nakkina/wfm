from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import shifts_frame
from wfm.api.main import app
from wfm.io.agents import ShiftTemplate
from wfm.schedule.mock import MockScheduleError, mock_schedule

MONDAY = date(2026, 9, 28)
SHIFTS = [ShiftTemplate.model_validate(r) for r in shifts_frame().rename(
    columns=lambda c: c.replace(" ", "_")).to_dict("records")]  # fmt: skip


def agent(**overrides: object) -> dict[str, object]:
    return {
        "agent_id": "AG1",
        "work_plan_id": "WP-FT",
        "preferred_shift": "s2",
        "allowed_shifts": "s1;s2;s3",
        "available_days": "Mon;Tue;Wed;Thu;Fri;Sat;Sun",
        "preferred_days_off": "Tue;Wed",
        **overrides,
    }


def test_full_time_week_shape() -> None:
    schedule = mock_schedule(agent(), SHIFTS, MONDAY, weeks=6)
    assert schedule.source == "mock"
    assert len(schedule.weeks) == 6
    for week in schedule.weeks:
        assert len(week.days) == 7
        assert week.days_off == 2
        assert week.paid_hours == 40
        assert {d.weekday for d in week.days if d.off} == {"Tue", "Wed"}


def test_part_time_paid_hours() -> None:
    schedule = mock_schedule(agent(work_plan_id="WP-PT"), SHIFTS, MONDAY, weeks=2)
    assert all(w.paid_hours == 20 for w in schedule.weeks)


def test_only_allowed_shifts_and_available_days() -> None:
    schedule = mock_schedule(
        agent(
            allowed_shifts="s1", available_days="Mon;Tue;Wed;Thu;Fri", preferred_days_off="Mon;Fri"
        ),
        SHIFTS,
        MONDAY,
        weeks=3,
    )
    for week in schedule.weeks:
        worked = [d for d in week.days if not d.off]
        assert {d.shift_code for d in worked} == {"s1"}
        # Only five days are available, so preferred days off must be worked.
        assert {d.weekday for d in worked} == {"Mon", "Tue", "Wed", "Thu", "Fri"}


def test_segments_cover_shift_with_breaks_and_meal() -> None:
    day = next(d for d in mock_schedule(agent(), SHIFTS, MONDAY, 1).weeks[0].days if not d.off)
    kinds = [s.kind for s in day.segments]
    assert kinds == ["work", "break", "work", "meal", "work", "break", "work"]
    assert day.segments[0].start == day.start and day.segments[-1].end == day.end
    for a, b in zip(day.segments, day.segments[1:], strict=False):
        assert a.end == b.start  # contiguous, no overlaps or gaps


def test_rest_and_consecutive_day_rules_hold_across_weeks() -> None:
    for agent_id in [f"AG{i}" for i in range(50)]:
        days = [d for w in mock_schedule(agent(agent_id=agent_id), SHIFTS, MONDAY, 6).weeks
                for d in w.days]  # fmt: skip
        run, previous_end = 0, None
        for d in days:
            if d.off:
                run = 0
                continue
            run += 1
            assert run <= 5
            assert d.start is not None and d.end is not None
            start = datetime.combine(d.date, d.start)
            if previous_end:
                assert start - previous_end >= timedelta(hours=11)
            previous_end = datetime.combine(d.date, d.end)


def test_deterministic_per_agent() -> None:
    first = mock_schedule(agent(agent_id="AG7"), SHIFTS, MONDAY, 6)
    assert first == mock_schedule(agent(agent_id="AG7"), SHIFTS, MONDAY, 6)


def test_rejects_non_monday_start_and_unknown_plan() -> None:
    with pytest.raises(MockScheduleError, match="start on a Monday"):
        mock_schedule(agent(), SHIFTS, MONDAY + timedelta(days=1), 1)
    with pytest.raises(MockScheduleError, match="no Shifts template"):
        mock_schedule(agent(work_plan_id="WP-XX"), SHIFTS, MONDAY, 1)


@pytest.mark.usefixtures("fixture_config")
def test_schedule_endpoint() -> None:
    client = TestClient(app)
    body = client.get("/api/agents/AG2/schedule?weeks=2").json()
    assert body["agent"]["agent_id"] == "AG2"
    assert body["agent"]["work_plan_id"] == "WP-PT"
    assert body["schedule"]["source"] == "mock"
    assert body["schedule"]["horizon_start"] == "2026-09-28"
    assert len(body["schedule"]["weeks"]) == 2
    assert len(client.get("/api/agents/AG1/schedule").json()["schedule"]["weeks"]) == 6
    assert client.get("/api/agents/NOPE/schedule").status_code == 404
    assert client.get("/api/agents/AG1/schedule?weeks=7").status_code == 422
