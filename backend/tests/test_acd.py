from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tests.conftest import acd_hourly_frame, write_acd
from wfm.api.main import app
from wfm.hierarchy import load_hierarchy
from wfm.io.acd import AcdError, load_acd_history, queue_history


def test_daily_rollup_sums_counts_and_weights_aht(acd_path: Path, hierarchy_path: Path) -> None:
    history = queue_history(load_acd_history(acd_path, load_hierarchy(hierarchy_path)), "Q-1")
    assert history is not None
    assert [str(d.date) for d in history.days] == ["2026-06-23", "2026-06-24"]
    day = history.days[0]
    assert day.calls_offered == 72  # 12 open hours x 6
    assert day.calls_handled == 60
    # 6 hours at 300s + 6 hours at 600s, 5 calls each: weighted mean is 450, not a sum.
    assert day.aht_seconds == 450.0


def test_day_with_no_handled_calls_has_blank_aht(acd_path: Path, hierarchy_path: Path) -> None:
    history = queue_history(load_acd_history(acd_path, load_hierarchy(hierarchy_path)), "Q-3")
    assert history is not None
    assert history.days[1].calls_handled == 0
    assert history.days[1].aht_seconds is None


def test_queue_without_history_returns_none(acd_path: Path, hierarchy_path: Path) -> None:
    acd = load_acd_history(acd_path, load_hierarchy(hierarchy_path))
    assert queue_history(acd, "Q-2") is None


def test_unknown_hierarchy_and_duplicates_rejected(tmp_path: Path, hierarchy_path: Path) -> None:
    hourly = acd_hourly_frame()
    hourly.loc[0, "mu_id"] = "MU-2"  # Q-1 under the wrong MU
    hourly = pd.concat([hourly, hourly.iloc[[10]]], ignore_index=True)  # repeat one interval
    path = write_acd(tmp_path / "bad.xlsx", hourly)
    with pytest.raises(AcdError) as exc:
        load_acd_history(path, load_hierarchy(hierarchy_path))
    assert "BU-1/MU-2/Q-1" in str(exc.value)
    assert "1 duplicate queue/interval rows" in str(exc.value)


def test_blank_counts_rejected_not_treated_as_zero(tmp_path: Path, hierarchy_path: Path) -> None:
    hourly = acd_hourly_frame()
    hourly["calls_offered"] = hourly["calls_offered"].astype("float")
    hourly.loc[5, "calls_offered"] = None
    path = write_acd(tmp_path / "bad.xlsx", hourly)
    with pytest.raises(AcdError, match="Blank values in calls_offered"):
        load_acd_history(path, load_hierarchy(hierarchy_path))


@pytest.mark.usefixtures("fixture_config")
def test_history_endpoint() -> None:
    client = TestClient(app)
    body = client.get("/api/queues/Q-1/history").json()
    assert body["start"] == "2026-06-23"
    assert body["days"][0]["aht_seconds"] == 450.0
    assert client.get("/api/queues/Q-2/history").status_code == 404  # queue exists, no data
    assert client.get("/api/queues/Q-99/history").status_code == 404


def test_events_label_queue_days_by_scope(tmp_path: Path, hierarchy_path: Path) -> None:
    events = pd.DataFrame(
        [
            {
                "start_date": "2026-06-23",
                "end_date": "2026-06-24",
                "scope": "MU-1",
                "event_name": "Campaign",
            },
            {
                "start_date": "2026-06-24",
                "end_date": "2026-06-24",
                "scope": "ALL",
                "event_name": "Holiday",
            },
        ]
    )
    path = write_acd(tmp_path / "acd.xlsx", acd_hourly_frame(), events)
    daily = load_acd_history(path, load_hierarchy(hierarchy_path)).daily
    labels = {(r["queue_id"], str(r["date"])): r["event"] for r in daily.to_dict("records")}
    assert labels[("Q-1", "2026-06-23")] == "Campaign"
    assert labels[("Q-1", "2026-06-24")] == "Campaign; Holiday"
    assert labels[("Q-3", "2026-06-23")] is None  # MU-2 queue: only the ALL-scope event applies
    assert labels[("Q-3", "2026-06-24")] == "Holiday"


def test_events_with_unknown_scope_rejected(tmp_path: Path, hierarchy_path: Path) -> None:
    events = pd.DataFrame(
        [{"start_date": "2026-06-23", "end_date": "2026-06-23", "scope": "MU-9", "event_name": "X"}]
    )
    path = write_acd(tmp_path / "acd.xlsx", acd_hourly_frame(), events)
    with pytest.raises(AcdError, match="unknown scope MU-9"):
        load_acd_history(path, load_hierarchy(hierarchy_path))
