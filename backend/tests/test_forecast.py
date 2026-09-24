import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import write_acd
from wfm.api import main
from wfm.config import ForecastSettings
from wfm.forecast.daily import (
    METHOD_VERSION,
    ForecastError,
    clean_events,
    conformal_quantile,
    forecast_all,
)
from wfm.forecast.store import file_fingerprint, load_matching, save_run

# Small settings keep tests fast: needs horizon 14 + one 7-day step + 28 = 49 days of history.
SETTINGS = ForecastSettings(horizon_days=14, levels=[80, 95], cv_windows=2, cv_step_days=7)
START = date(2026, 6, 23)
WEEKLY = [900, 950, 980, 1000, 870, 400, 350]  # weekday shape


def daily_frame(
    days: int = 60, noise: float = 0.0, queues: tuple[str, ...] = ("Q-1", "Q-3")
) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for q in queues:
        for i in range(days):
            d = START + timedelta(days=i)
            offered = WEEKLY[d.weekday()] * (1 + noise * rng.standard_normal())
            handled = round(offered * 0.95)
            rows.append(
                {
                    "queue_id": q,
                    "date": d,
                    "calls_offered": round(offered),
                    "calls_handled": handled,
                    "handle_seconds": handled * (600 + (30 if d.weekday() == 0 else 0)),
                }
            )
    return pd.DataFrame(rows)


def test_horizon_starts_after_history_for_both_targets() -> None:
    result = forecast_all(daily_frame(noise=0.08), SETTINGS)
    for (queue, target), pts in result.points.groupby(["queue_id", "target"]):
        assert len(pts) == 14, (queue, target)
        assert pts["date"].min() == START + timedelta(days=60)
    assert result.history_end == START + timedelta(days=59)


def test_intervals_are_nested_and_non_negative() -> None:
    p = forecast_all(daily_frame(noise=0.25), SETTINGS).points
    assert (p["lo_95"] <= p["lo_80"]).all()
    assert (p["lo_80"] <= p["value"]).all()
    assert (p["value"] <= p["hi_80"]).all()
    assert (p["hi_80"] <= p["hi_95"]).all()
    assert (p[["value", "lo_80", "lo_95"]] >= 0).all().all()


def test_baseline_kept_when_nothing_beats_it() -> None:
    # A perfectly repeating week: seasonal naive is exact, so nothing can be strictly better.
    result = forecast_all(daily_frame(noise=0.0), SETTINGS)
    chosen = result.scores[result.scores["chosen"]]
    assert set(chosen["model"]) == {"SeasonalNaive"}
    assert (result.points["model"] == "SeasonalNaive").all()
    # And its forecast repeats the weekly shape.
    vol = result.points[
        (result.points["queue_id"] == "Q-1") & (result.points["target"] == "volume")
    ]
    assert [round(v) for v in vol["value"][:7]] == [WEEKLY[d.weekday()] for d in vol["date"][:7]]


def test_each_queue_and_target_has_exactly_one_chosen_model() -> None:
    scores = forecast_all(daily_frame(noise=0.1), SETTINGS).scores
    assert (scores.groupby(["queue_id", "target"])["chosen"].sum() == 1).all()
    assert set(scores["model"]) == {"SeasonalNaive", "WeekdayAverage", "AutoETS", "AutoARIMA"}


def test_clean_events_uses_normal_day_weekday_median() -> None:
    frame = daily_frame(days=21, queues=("Q-1",)).assign(is_event=False)
    frame["ds"] = pd.to_datetime(frame["date"])
    frame["y"] = frame["calls_offered"].astype(float)
    spike = frame.index[-1]
    spike_date: date = START + timedelta(days=20)
    frame.loc[spike, ["y", "is_event"]] = [99999.0, True]
    cleaned = clean_events(frame)
    assert cleaned[spike] == WEEKLY[spike_date.weekday()]
    assert (cleaned.drop(spike) == frame["y"].drop(spike)).all()  # normal days untouched


def test_event_spike_does_not_leak_into_forecast() -> None:
    # Perfect weekly series with a labelled 5x spike on the last day of history.
    daily = daily_frame(days=60, queues=("Q-1",))
    last = daily.index[-1]
    last_date: date = START + timedelta(days=59)
    daily["event"] = None
    daily.loc[last, "calls_offered"] *= 5
    daily.loc[last, "event"] = "Outage"
    result = forecast_all(daily, SETTINGS)
    vol = result.points[result.points["target"] == "volume"]
    # Seasonal naive would repeat the spike 7 days later; cleaning keeps the normal level.
    same_weekday = vol[vol["date"] == last_date + timedelta(days=7)]
    assert round(same_weekday["value"].iloc[0]) == WEEKLY[last_date.weekday()]
    assert (
        result.diagnostics.loc[result.diagnostics["target"] == "volume", "event_days"].iloc[0] == 1
    )
    assert result.history.loc[result.history["event"].notna(), "event"].tolist() == ["Outage"]


def test_diagnostics_measure_weekday_share() -> None:
    diag = forecast_all(daily_frame(noise=0.0), SETTINGS).diagnostics
    volume = diag[diag["target"] == "volume"]
    assert (volume["weekday_share"] > 0.99).all()  # a pure weekly pattern
    assert set(diag["target"]) == {"volume", "aht"}


def test_aht_skipped_with_warning_when_a_day_has_no_handled_calls() -> None:
    daily = daily_frame()
    daily.loc[
        (daily["queue_id"] == "Q-3") & (daily["date"] == START), ["calls_handled", "handle_seconds"]
    ] = 0
    result = forecast_all(daily, SETTINGS)
    assert any("Q-3" in w for w in result.warnings)
    aht_queues = set(result.points.loc[result.points["target"] == "aht", "queue_id"])
    assert aht_queues == {"Q-1"}


def test_short_or_gappy_history_rejected() -> None:
    with pytest.raises(ForecastError, match="Need at least 49 days"):
        forecast_all(daily_frame(days=48), SETTINGS)
    gappy = daily_frame().drop(index=10)
    with pytest.raises(ForecastError, match="missing dates for: Q-1"):
        forecast_all(gappy, SETTINGS)


def test_conformal_quantile() -> None:
    errors = np.arange(1, 11, dtype=float)  # 1..10
    assert conformal_quantile(errors, 80) == 9.0  # ceil(11 * 0.8) = 9th smallest
    assert conformal_quantile(errors, 95) == 10.0  # capped at the largest error
    assert np.isnan(conformal_quantile(np.array([]), 80))


def test_save_and_reuse_only_matching_runs(tmp_path: Path) -> None:
    source = tmp_path / "acd.xlsx"
    source.write_bytes(b"v1")
    fingerprint = file_fingerprint(source)
    result = forecast_all(daily_frame(noise=0.1), SETTINGS)
    saved = save_run(tmp_path / "runs", result, SETTINGS, source, fingerprint, 1.23)

    run_dir = tmp_path / "runs" / "forecasts" / saved.run_id
    assert {p.name for p in run_dir.iterdir()} == {
        "points.csv",
        "scores.csv",
        "diagnostics.csv",
        "history.csv",
        "meta.json",
    }
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["source_sha256"] == fingerprint
    assert meta["method_version"] == METHOD_VERSION
    assert not list((tmp_path / "runs" / "forecasts").glob(".*.tmp"))

    loaded = load_matching(tmp_path / "runs", fingerprint, SETTINGS)
    assert loaded is not None and loaded.run_id == saved.run_id
    assert loaded.points["date"].iloc[0] == result.points["date"].iloc[0]
    assert len(loaded.history) == len(result.history)
    assert loaded.history["event"].isna().all()
    assert len(loaded.diagnostics) == len(result.diagnostics)

    assert load_matching(tmp_path / "runs", "other-file", SETTINGS) is None
    other_settings = SETTINGS.model_copy(update={"levels": [90]})
    assert load_matching(tmp_path / "runs", fingerprint, other_settings) is None

    # A run from an older forecasting method is not reused.
    meta["method_version"] = METHOD_VERSION - 1
    (run_dir / "meta.json").write_text(json.dumps(meta))
    assert load_matching(tmp_path / "runs", fingerprint, SETTINGS) is None


@pytest.fixture
def forecast_config(fixture_config: Path, tmp_path: Path) -> Path:
    """fixture_config, but with 50 days of hourly ACD history and small forecast settings."""
    hourly = []
    for row in daily_frame(days=50, noise=0.1).to_dict("records"):
        q = str(row["queue_id"])
        hourly.append(  # one hourly row per day carrying the whole day's totals
            {
                "bu_id": "BU-1" if q == "Q-1" else "BU-2",
                "mu_id": "MU-1" if q == "Q-1" else "MU-2",
                "queue_id": q,
                "interval_start_local": datetime.combine(row["date"], time(12)),
                "calls_offered": row["calls_offered"],
                "calls_handled": row["calls_handled"],
                "handle_seconds": row["handle_seconds"],
            }
        )
    events = pd.DataFrame(
        [
            {
                "start_date": "2026-07-06",
                "end_date": "2026-07-06",
                "scope": "MU-1",
                "event_name": "Campaign",
            }
        ]
    )
    acd = write_acd(tmp_path / "acd_long.xlsx", pd.DataFrame(hourly), events)
    text = fixture_config.read_text()
    text = text.replace(str(tmp_path / "acd.xlsx"), str(acd))
    text += "forecast:\n  horizon_days: 7\n  levels: [80, 95]\n  cv_windows: 2\n  cv_step_days: 7\n"
    fixture_config.write_text(text)
    main._cache.clear()
    return fixture_config


@pytest.mark.usefixtures("forecast_config")
def test_forecast_endpoint_generates_then_serves_saved_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(main.app)
    body = client.get("/api/queues/Q-1/forecast").json()
    assert body["horizon_start"] == "2026-08-12"  # day after 50 days from 2026-06-23
    assert body["levels"] == [80, 95]
    assert len(body["volume"]["points"]) == 7
    assert body["aht"]["points"][0]["intervals"][0]["level"] == 80
    assert sum(s["chosen"] for s in body["volume"]["scores"]) == 1
    assert body["volume"]["diagnostics"]["event_days"] == 1
    # History for the charts comes with the forecast, event days labelled (MU-1 only).
    assert len(body["history"]) == 50
    assert [d["event"] for d in body["history"] if d["event"]] == ["Campaign"]

    # Restarted backend: the saved run is served without re-reading the ACD workbook.
    main._cache.clear()

    def fail(*_: object) -> None:
        raise AssertionError("ACD workbook re-read")

    monkeypatch.setattr(main, "load_acd_history", fail)
    again = client.get("/api/queues/Q-3/forecast").json()
    assert again["run_id"] == body["run_id"]
    assert all(d["event"] is None for d in again["history"])  # Q-3 is in MU-2
    assert len(list((tmp_path / "runs" / "forecasts").iterdir())) == 1

    assert client.get("/api/queues/Q-2/forecast").status_code == 404  # no ACD history
