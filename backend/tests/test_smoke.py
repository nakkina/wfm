from fastapi.testclient import TestClient

from wfm.api.main import app
from wfm.config import load_config


def test_health() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_example_config_loads() -> None:
    config = load_config()
    assert config.timezone


def test_solver_and_forecasting_libraries_import() -> None:
    from ortools.sat.python import cp_model
    from statsforecast import StatsForecast

    assert cp_model.CpModel() is not None
    assert StatsForecast is not None
