"""End to end on synthetic fixtures: saved forecast → requirements → CP-SAT → plan → API."""

import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from wfm.api import main
from wfm.api import schedule as schedule_api
from wfm.config import load_config, resolve
from wfm.schedule.run import ScheduleRunError, apply_overrides, generate
from wfm.schedule.store import new_run_dir, write_status

FAST = {"scheduling": {"stage_time_limit_seconds": [5, 3, 3], "num_workers": 2}}


@pytest.fixture
def generated_run(forecast_config: Path) -> Path:
    client = TestClient(main.app)
    assert client.get("/api/queues/Q-1/forecast").status_code == 200  # saves the forecast run
    config = apply_overrides(load_config(), FAST)
    run_dir = new_run_dir(resolve(config.paths.runs_dir))
    meta = generate(config, run_dir, run_dir.name, lambda *_: None)
    write_status(run_dir, {"run_id": run_dir.name, "state": "completed", "done": 1, "total": 1})
    schedule_api._load.cache_clear()
    assert meta["validation_passed"]
    return run_dir


def test_run_outputs_are_complete_and_valid(generated_run: Path) -> None:
    shifts = pd.read_csv(generated_run / "shifts.csv")
    activities = pd.read_csv(generated_run / "activities.csv")
    coverage = pd.read_csv(generated_run / "coverage.csv")
    requirements = pd.read_csv(generated_run / "requirements.csv")

    # Every agent has exactly one status per horizon day (7 days, 6 agents).
    assert len(shifts) == 6 * 7
    assert shifts.groupby(["agent_id", "work_date"]).size().eq(1).all()
    assert set(shifts["status"]) <= {"Working", "Off", "Leave"}
    # Home queue only, and every working shift has a complete activity timeline.
    working = shifts[shifts["status"] == "Working"]
    for s in working.itertuples():
        acts = activities[
            (activities["agent_id"] == s.agent_id) & (activities["work_date"] == s.work_date)
        ]
        assert acts["start"].iloc[0] == s.shift_start and acts["end"].iloc[-1] == s.shift_end
        assert acts.loc[acts["activity_type"] == "On Phone", "queue_id"].eq(s.queue_id).all()
    # Coverage is visible and consistent; closing work is its own kind and can't be covered.
    assert (
        coverage["shortage"]
        == (coverage["required_on_phone"] - coverage["scheduled_on_phone"]).clip(lower=0)
    ).all()
    closing = coverage[coverage["kind"] == "closing"]
    assert len(closing) > 0 and (closing["scheduled_on_phone"] == 0).all()
    assert set(requirements["allocation"]) == {"estimated", "closing allowance"}
    assert requirements["interval_start"].str.endswith("-04:00").all()


def test_queue_schedule_endpoint(generated_run: Path) -> None:
    body = TestClient(main.app).get("/api/queues/Q-1/schedule").json()
    assert body["queue"]["status"] in ("Optimal", "Feasible")
    assert [s["stage"] for s in body["queue"]["stages"]] == [
        "shortage",
        "excess_and_paid",
        "preferences",
    ]
    assert len(body["agents"]) == 3 and all(len(a["days"]) == 7 for a in body["agents"])
    assert body["totals"]["shortage_agent_hours"] >= 0
    working = next(d for a in body["agents"] for d in a["days"] if d["status"] == "Working")
    assert {x["activity_type"] for x in working["activities"]} >= {"On Phone", "Paid Break"}


def test_rollups_reconcile_queue_to_mu_to_bu_to_org(generated_run: Path) -> None:
    client = TestClient(main.app)
    org = client.get("/api/schedule/summary", params={"level": "org"}).json()
    keys = [
        "required_on_phone_hours",
        "scheduled_on_phone_hours",
        "shortage_agent_hours",
        "paid_hours",
    ]
    for k in keys:
        assert org["total"][k] == pytest.approx(sum(c[k] for c in org["children"]))
    for bu in org["children"]:
        b = client.get("/api/schedule/summary", params={"level": "bu", "code": bu["code"]}).json()
        for k in keys:
            assert b["total"][k] == pytest.approx(bu[k])
            assert b["total"][k] == pytest.approx(sum(c[k] for c in b["children"]))
        for mu in b["children"]:
            m = client.get(
                "/api/schedule/summary", params={"level": "mu", "code": mu["code"]}
            ).json()
            for k in keys:
                assert m["total"][k] == pytest.approx(mu[k])
                assert m["total"][k] == pytest.approx(sum(c[k] for c in m["children"]))


def test_agent_schedule_uses_real_plan(generated_run: Path) -> None:
    body = TestClient(main.app).get("/api/agents/AG1/schedule").json()
    plan = body["schedule"]
    assert plan["source"] == "optimized"
    assert plan["validation_status"] == "Validated"
    week = plan["weeks"][0]
    in_horizon = [d for d in week["days"] if d["status"] != "Outside horizon"]
    assert week["paid_hours"] == sum(d["paid_hours"] for d in in_horizon)
    assert all(d["status"] in ("Working", "Off", "Leave") for d in in_horizon)


def test_csv_exports(generated_run: Path) -> None:
    client = TestClient(main.app)
    run_id = generated_run.name
    shifts = client.get(
        f"/api/schedule/runs/{run_id}/export/shifts.csv", params={"queue_id": "Q-1"}
    )
    assert shifts.status_code == 200 and "attachment" in shifts.headers["content-disposition"]
    lines = shifts.text.strip().splitlines()
    assert lines[0].startswith("run_id,agent_id,agent_name") and len(lines) == 1 + 3 * 7
    acts = client.get(f"/api/schedule/runs/{run_id}/export/activities.csv").text
    assert "On Phone" in acts and "Unpaid Meal" in acts


def test_run_requires_a_saved_forecast(fixture_config: Path) -> None:
    config = apply_overrides(load_config(), FAST)
    run_dir = new_run_dir(resolve(config.paths.runs_dir))
    with pytest.raises(ScheduleRunError, match="No saved forecast"):
        generate(config, run_dir, run_dir.name, lambda *_: None)


def test_start_run_endpoint_runs_worker_and_rejects_overlap(forecast_config: Path) -> None:
    client = TestClient(main.app)
    assert client.get("/api/queues/Q-1/forecast").status_code == 200
    assert client.get("/api/schedule/runs/latest").json()["state"] == "not_generated"
    bad = client.post("/api/schedule/runs", json={"staffing": {"max_occupancy": 1.5}})
    assert bad.status_code == 422
    started = client.post("/api/schedule/runs", json=FAST)
    assert started.status_code == 202
    assert client.post("/api/schedule/runs", json=FAST).status_code == 409  # one at a time
    deadline = time.time() + 120
    while time.time() < deadline:
        latest = client.get("/api/schedule/runs/latest").json()
        if latest["state"] not in ("queued", "running"):
            break
        time.sleep(1)
    assert latest["state"] in ("completed", "completed_with_shortages"), latest
    assert latest["summary"]["validation_passed"] is True
    assert latest["stale"] is False
    assert set(latest["summary"]["status_counts"]) <= {"Optimal", "Feasible"}
