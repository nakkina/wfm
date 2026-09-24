from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import agents_frame, dictionary_frame, write_workbook
from wfm.api.main import app
from wfm.hierarchy import load_hierarchy
from wfm.io.agents import RosterError, agents_in_queue, load_agent_roster, normalize_header


def test_normalize_header() -> None:
    assert normalize_header("  Queue   ID ") == "queue_id"
    assert normalize_header("weekly contracted hours") == "weekly_contracted_hours"


def test_loads_roster_with_normalized_headers(workbook_path: Path, hierarchy_path: Path) -> None:
    roster = load_agent_roster(workbook_path, load_hierarchy(hierarchy_path))
    assert len(roster.agents) == 6
    assert "queue_id" in roster.agents.columns
    assert roster.columns[0].field == "agent_id"
    assert {c.field: c.category for c in roster.columns}["csat_pct"] == "Performance"
    assert {s.shift_code for s in roster.shifts} == {"s1", "s2", "s3"}
    assert len(roster.shifts) == 6


def test_queue_records_are_sorted_and_json_safe(workbook_path: Path, hierarchy_path: Path) -> None:
    roster = load_agent_roster(workbook_path, load_hierarchy(hierarchy_path))
    records = agents_in_queue(roster, "Q-1")
    assert [r["agent_id"] for r in records] == ["AG1", "AG2", "AG3"]
    assert records[0]["hire_date"] == "2020-01-15"
    assert records[1]["csat_pct"] is None  # blank cell, not 0 or NaN


def test_unknown_queue_rejected(tmp_path: Path, hierarchy_path: Path) -> None:
    agents = agents_frame()
    agents.loc[0, "queue id"] = "Q-99"
    path = write_workbook(tmp_path / "bad.xlsx", agents, dictionary_frame())
    with pytest.raises(RosterError, match=r"AG3 \(BU-1/MU-1/Q-99\)"):
        load_agent_roster(path, load_hierarchy(hierarchy_path))


def test_duplicate_ids_and_blank_org_rejected(tmp_path: Path, hierarchy_path: Path) -> None:
    agents = agents_frame()
    agents.loc[1, "agent id"] = "AG3"
    agents.loc[4, "mu id"] = None
    path = write_workbook(tmp_path / "bad.xlsx", agents, dictionary_frame())
    with pytest.raises(RosterError) as exc:
        load_agent_roster(path, load_hierarchy(hierarchy_path))
    assert "Duplicate agent IDs: AG3" in str(exc.value)
    assert "Agents missing BU/MU/queue: AG5" in str(exc.value)


def test_dictionary_mismatch_rejected(tmp_path: Path, hierarchy_path: Path) -> None:
    dictionary = dictionary_frame()
    dictionary = dictionary[dictionary["field"] != "csat_pct"]
    path = write_workbook(tmp_path / "bad.xlsx", agents_frame(), dictionary)
    with pytest.raises(RosterError, match="fields not in the Dictionary: csat_pct"):
        load_agent_roster(path, load_hierarchy(hierarchy_path))


@pytest.mark.usefixtures("fixture_config")
def test_agent_endpoints() -> None:
    client = TestClient(app)
    columns = client.get("/api/agents/columns").json()
    assert [c["field"] for c in columns][:2] == ["agent_id", "agent_name"]

    agents = client.get("/api/queues/Q-3/agents").json()
    assert [a["agent_id"] for a in agents] == ["AG5", "AG6"]

    assert client.get("/api/queues/Q-99/agents").status_code == 404
