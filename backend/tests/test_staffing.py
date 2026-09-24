from datetime import UTC, date, time

import pandas as pd
import pytest

from wfm.config import StaffingSettings
from wfm.io.agents import QueueInfo
from wfm.staffing.erlang import erlang_c, on_phone_agents, required_agents, service_level
from wfm.staffing.intraday import IntradayError, allocate_day, closing_slots, open_slots
from wfm.staffing.requirements import RequirementsError, build_requirements, summarize

QUEUE = QueueInfo(
    queue_id="Q-1",
    timezone="America/New_York",
    open_days="Mon;Tue;Wed;Thu;Fri;Sat;Sun",
    open_time=time(8),
    close_time=time(20, 30),
)


# --- Erlang C -----------------------------------------------------------------------------


def test_erlang_reference_case() -> None:
    # 120 calls/hour, AHT 360 s, 80/20, max occupancy 85 %.
    load = 120 * 360 / 3600
    assert load == 12
    r = required_agents(load, 360, 0.80, 20, 0.85)
    assert r.agents == 16
    assert r.service_level == pytest.approx(0.8362, abs=5e-5)
    assert r.occupancy == pytest.approx(0.75)
    assert on_phone_agents(r.agents, 0.10) == 18


def test_one_fewer_agent_misses_the_target() -> None:
    assert service_level(15, 12, 360, 20) < 0.80


def test_occupancy_cap_can_bind_before_service_level() -> None:
    # Long answer threshold: service level is easy, so the 85 % occupancy cap decides N.
    r = required_agents(100, 300, 0.80, 600, 0.85)
    assert r.agents == 118  # ceil(100 / 0.85)
    assert r.occupancy <= 0.85


def test_zero_workload_needs_nobody() -> None:
    r = required_agents(0, 0, 0.8, 20, 0.85)
    assert (r.agents, r.service_level, r.occupancy) == (0, 1.0, 0.0)
    assert on_phone_agents(0, 0.1) == 0


def test_invalid_erlang_inputs() -> None:
    with pytest.raises(ValueError):
        required_agents(-1, 300, 0.8, 20, 0.85)
    with pytest.raises(ValueError, match="AHT must be positive"):
        required_agents(5, 0, 0.8, 20, 0.85)


def test_large_loads_are_numerically_stable() -> None:
    assert 0 < erlang_c(3000, 2900.5) < 1  # no overflow from factorials
    r = required_agents(2900.5, 300, 0.8, 20, 0.85)
    assert r.agents >= 2900.5 / 0.85


def test_shrinkage_applied_once() -> None:
    # 9 productive at 10 % shrinkage is exactly 10 on-phone — not 11 from float noise.
    assert on_phone_agents(9, 0.10) == 10
    assert on_phone_agents(16, 0.10) == 18
    assert on_phone_agents(16, 0.0) == 16


# --- Intraday allocation ------------------------------------------------------------------


def test_slots_cover_open_window_including_half_open_last_hour() -> None:
    slots = open_slots(QUEUE, date(2026, 9, 23), 15)
    assert len(slots) == 50  # 08:00–20:30
    assert slots[0].start.isoformat() == "2026-09-23T08:00:00-04:00"
    assert slots[-1].start.time() == time(20, 15)
    assert [s.hour for s in slots[-2:]] == [20, 20]


def test_allocation_preserves_totals_and_follows_profile() -> None:
    slots = open_slots(QUEUE, date(2026, 9, 23), 15)
    shares = {8: 0.1, 13: 0.6, 20: 0.3}  # 20:00 hour is only half open
    calls = allocate_day(1000.0, shares, slots, 15, None, "t")
    assert sum(calls) == pytest.approx(1000.0, abs=1e-9)
    by_hour = pd.Series(calls).groupby([s.hour for s in slots]).sum()
    assert by_hour[13] == pytest.approx(600)
    assert by_hour[20] == pytest.approx(300)
    assert calls[-1] == pytest.approx(150)  # two open 20:00 slots share the hour equally
    assert calls[0] == pytest.approx(25)  # not rounded


def test_configured_within_hour_proportions() -> None:
    slots = open_slots(QUEUE, date(2026, 9, 23), 15)
    calls = allocate_day(100.0, {9: 1.0}, slots, 15, [0.4, 0.3, 0.2, 0.1], "t")
    nine = [c for c, s in zip(calls, slots, strict=True) if s.hour == 9]
    assert nine == pytest.approx([40, 30, 20, 10])


def test_no_uniform_day_is_assumed() -> None:
    slots = open_slots(QUEUE, date(2026, 9, 23), 15)
    with pytest.raises(IntradayError, match="no intraday profile"):
        allocate_day(100.0, {}, slots, 15, None, "Q-1 2026-09-23")
    with pytest.raises(IntradayError, match="closed hours"):
        allocate_day(100.0, {22: 1.0}, slots, 15, None, "t")


def test_zero_calls_allocate_zero() -> None:
    slots = open_slots(QUEUE, date(2026, 9, 23), 15)
    assert allocate_day(0.0, {}, slots, 15, None, "t") == [0.0] * 50


def test_dst_fall_back_day_uses_real_durations() -> None:
    day = date(2026, 11, 1)  # clocks go back at 02:00; opening hours are unaffected
    slots = open_slots(QUEUE, day, 15)
    assert slots[0].start.utcoffset().total_seconds() == -5 * 3600  # type: ignore[union-attr]
    assert all(s.seconds == 900 for s in slots)
    assert closing_slots(QUEUE, day, 15, 15)[0].start.astimezone(UTC).hour == 1  # 20:30 EST


# --- Requirements -------------------------------------------------------------------------


def forecast(days: list[date], calls: float = 120.0, aht: float = 360.0) -> pd.DataFrame:
    rows = []
    for d in days:
        rows.append({"queue_id": "Q-1", "target": "volume", "date": d, "value": calls})
        rows.append({"queue_id": "Q-1", "target": "aht", "date": d, "value": aht})
    return pd.DataFrame(rows)


def profile(hours: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"queue_id": "Q-1", "weekday": wd, "hour": h, "share": s}
            for wd in range(7)
            for h, s in hours.items()
        ]
    )


def test_requirements_rows_and_reference_interval() -> None:
    days = [date(2026, 9, 23), date(2026, 9, 24)]
    # All 120 daily calls in the 12:00 hour → 30 per 15 min = 120 calls/hour at AHT 360.
    req = build_requirements(forecast(days), profile({12: 1.0}), {"Q-1": QUEUE}, StaffingSettings())
    open_rows = req[req["kind"] == "open"]
    assert len(open_rows) == 2 * 50
    assert open_rows.groupby("date")["offered_calls"].sum().tolist() == pytest.approx([120, 120])
    noon = open_rows[open_rows["interval_start"].map(lambda t: t.hour == 12)].iloc[0]
    assert noon["offered_load_erlangs"] == pytest.approx(12)
    assert noon["workload_agent_hours"] == pytest.approx(3.0)  # 30 calls × 360 s
    assert (noon["required_productive"], noon["required_on_phone"]) == (16, 18)
    assert noon["required_on_phone_hours"] == pytest.approx(4.5)
    assert noon["expected_service_level"] == pytest.approx(0.8362, abs=5e-5)
    quiet = open_rows[open_rows["interval_start"].map(lambda t: t.hour == 9)]
    assert (quiet["required_on_phone"] == 0).all()  # no calls → no new-arrival workload


def test_closing_rows_are_separate_and_visible() -> None:
    days = [date(2026, 9, 23)]
    req = build_requirements(forecast(days), profile({20: 1.0}), {"Q-1": QUEUE}, StaffingSettings())
    closing = req[req["kind"] == "closing"]
    assert len(closing) == 1  # 15 minutes
    last_open = req[req["kind"] == "open"].iloc[-1]
    assert closing.iloc[0]["required_on_phone"] == int(-(-last_open["offered_load_erlangs"] // 1))
    assert closing.iloc[0]["offered_calls"] == 0
    none = build_requirements(
        forecast(days), profile({20: 1.0}), {"Q-1": QUEUE}, StaffingSettings(closing_minutes=0)
    )
    assert (none["kind"] == "open").all()


def test_summaries_report_peaks_not_summed_headcount() -> None:
    days = [date(2026, 9, 23), date(2026, 9, 24)]
    req = build_requirements(
        forecast(days), profile({12: 1.0}), {"Q-1": QUEUE}, StaffingSettings(closing_minutes=0)
    )
    s = summarize(req, ["queue_id"]).iloc[0]
    assert s["peak_required_on_phone"] == 18
    assert s["required_on_phone_hours"] == pytest.approx(2 * 4 * 4.5)
    assert s["offered_calls"] == pytest.approx(240)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda f: f.assign(value=f["value"].where(f["target"] != "volume", -5)),
            "Negative forecast calls",
        ),
        (
            lambda f: f.assign(value=f["value"].where(f["target"] != "aht", 0)),
            "AHT must be positive",
        ),
        (lambda f: f.assign(queue_id="Q-9"), "missing from the Queues sheet"),
        (lambda f: f[f["date"] != date(2026, 9, 24)], "not contiguous"),
        (lambda f: f[f["target"] != "aht"], "no 'aht' values"),
    ],
)
def test_invalid_forecasts_rejected(mutate: object, message: str) -> None:
    days = [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
    bad = mutate(forecast(days))  # type: ignore[operator]
    with pytest.raises(RequirementsError, match=message):
        build_requirements(bad, profile({12: 1.0}), {"Q-1": QUEUE}, StaffingSettings())


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        StaffingSettings(within_hour_proportions=[0.5, 0.5, 0.5, 0.5])
    with pytest.raises(ValueError, match="divide 60"):
        StaffingSettings(interval_minutes=25)
