from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from wfm.api.main import app
from wfm.config import load_config, resolve
from wfm.hierarchy import Organization, load_hierarchy, with_agent_counts


def test_configured_hierarchy_shape() -> None:
    org = load_hierarchy(resolve(load_config().paths.hierarchy_yaml))
    assert org.name == "Financial Services Contact Center"
    assert [bu.code for bu in org.business_units] == ["BU-CB", "BU-CL"]
    assert sum(len(bu.management_units) for bu in org.business_units) == 6
    assert len(org.queues()) == 30


def test_duplicate_codes_rejected(tmp_path: Path) -> None:
    path = tmp_path / "hierarchy.yaml"
    path.write_text(
        """
name: Org
business_units:
  - code: BU-1
    name: BU
    management_units:
      - code: MU-1
        name: MU
        queues:
          - { id: Q-1, name: A }
          - { id: Q-1, name: B }
"""
    )
    with pytest.raises(ValidationError, match="Duplicate hierarchy codes: Q-1"):
        load_hierarchy(path)


def test_agent_counts_roll_up(hierarchy_path: Path) -> None:
    org = with_agent_counts(load_hierarchy(hierarchy_path), {"Q-1": 3, "Q-2": 1, "Q-3": 2})
    banking, lending = org.business_units
    assert [q.agent_count for q in banking.management_units[0].queues] == [3, 1]
    assert banking.management_units[0].agent_count == 4
    assert banking.agent_count == 4
    assert lending.agent_count == 2
    assert org.agent_count == 6


def test_queue_without_agents_counts_zero(hierarchy_path: Path) -> None:
    org = with_agent_counts(load_hierarchy(hierarchy_path), {"Q-1": 3})
    assert org.business_units[1].agent_count == 0
    assert org.agent_count == 3


@pytest.mark.usefixtures("fixture_config")
def test_hierarchy_endpoint_includes_counts() -> None:
    response = TestClient(app).get("/api/hierarchy")
    assert response.status_code == 200
    org = Organization.model_validate(response.json())
    assert org.agent_count == 6
    assert org.business_units[0].management_units[0].queues[0].agent_count == 3
