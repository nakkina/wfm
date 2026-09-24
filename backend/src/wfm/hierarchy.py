"""Business Unit -> Management Unit -> Queue hierarchy, loaded from YAML config."""

from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator


class Queue(BaseModel):
    id: str
    name: str
    agent_count: int = 0


class ManagementUnit(BaseModel):
    code: str
    name: str
    queues: list[Queue]
    agent_count: int = 0


class BusinessUnit(BaseModel):
    code: str
    name: str
    management_units: list[ManagementUnit]
    agent_count: int = 0


class Organization(BaseModel):
    name: str
    business_units: list[BusinessUnit]
    agent_count: int = 0

    @model_validator(mode="after")
    def codes_are_unique(self) -> Organization:
        codes = [bu.code for bu in self.business_units]
        codes += [mu.code for bu in self.business_units for mu in bu.management_units]
        codes += [q.id for q in self.queues()]
        duplicates = sorted(code for code, n in Counter(codes).items() if n > 1)
        if duplicates:
            raise ValueError(f"Duplicate hierarchy codes: {', '.join(duplicates)}")
        return self

    def queues(self) -> list[Queue]:
        return [q for bu in self.business_units for mu in bu.management_units for q in mu.queues]


def load_hierarchy(path: Path) -> Organization:
    with path.open() as f:
        return Organization.model_validate(yaml.safe_load(f))


def with_agent_counts(org: Organization, queue_counts: Mapping[str, int]) -> Organization:
    """Returns a copy with queue agent counts set and summed up to MU, BU and organization."""
    org = org.model_copy(deep=True)
    for bu in org.business_units:
        for mu in bu.management_units:
            for q in mu.queues:
                q.agent_count = queue_counts.get(q.id, 0)
            mu.agent_count = sum(q.agent_count for q in mu.queues)
        bu.agent_count = sum(mu.agent_count for mu in bu.management_units)
    org.agent_count = sum(bu.agent_count for bu in org.business_units)
    return org
