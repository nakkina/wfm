"""Synthetic workbook + config fixtures, so tests never read files in data/."""

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from wfm.api import main

HIERARCHY_YAML = """
name: Test Center
business_units:
  - code: BU-1
    name: Banking
    management_units:
      - code: MU-1
        name: Deposits
        queues:
          - { id: Q-1, name: Checking }
          - { id: Q-2, name: Savings }
  - code: BU-2
    name: Lending
    management_units:
      - code: MU-2
        name: Mortgages
        queues:
          - { id: Q-3, name: Payoff }
"""

# (agent_id, bu, mu, queue, work plan) — Q-1: 3 agents, Q-2: 1, Q-3: 2
AGENTS = [
    ("AG3", "BU-1", "MU-1", "Q-1", "WP-FT"),
    ("AG1", "BU-1", "MU-1", "Q-1", "WP-FT"),
    ("AG2", "BU-1", "MU-1", "Q-1", "WP-PT"),
    ("AG4", "BU-1", "MU-1", "Q-2", "WP-FT"),
    ("AG5", "BU-2", "MU-2", "Q-3", "WP-FT"),
    ("AG6", "BU-2", "MU-2", "Q-3", "WP-PT"),
]
NAMES = {"BU-1": "Banking", "BU-2": "Lending", "MU-1": "Deposits", "MU-2": "Mortgages"}
QUEUE_NAMES = {"Q-1": "Checking", "Q-2": "Savings", "Q-3": "Payoff"}
FIELDS = ["agent_id", "agent_name", "bu_id", "bu_name", "mu_id", "mu_name", "queue_id",
          "queue_name", "hire_date", "csat_pct", "work_plan_id", "preferred_shift",
          "allowed_shifts", "available_days", "preferred_days_off"]  # fmt: skip


def agents_frame() -> pd.DataFrame:
    rows = [
        {
            "agent id": a,
            "agent name": f"Agent {a}",
            "bu id": bu,
            "bu name": NAMES[bu],
            "mu id": mu,
            "mu name": NAMES[mu],
            "queue id": q,
            "queue name": QUEUE_NAMES[q],
            "hire date": pd.Timestamp("2020-01-15"),
            "csat pct": 80.5 if a != "AG2" else None,
            "work plan id": plan,
            "preferred shift": "s1",
            "allowed shifts": "s1;s2;s3",
            "available days": "Mon;Tue;Wed;Thu;Fri;Sat;Sun",
            "preferred days off": "Sat;Sun",
        }
        for a, bu, mu, q, plan in AGENTS
    ]
    return pd.DataFrame(rows)


def dictionary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "field": FIELDS,
            "category": ["Identity"] * 2
            + ["Organization"] * 6
            + ["Employment", "Performance"]
            + ["Scheduling"] * 5,
            "type": ["string"] * 8 + ["date", "number 0–100"] + ["string"] * 5,
            "poc use": ["Display"] * len(FIELDS),
            "description": [f"About {f}" for f in FIELDS],
        }
    )


def shifts_frame() -> pd.DataFrame:
    """Same templates as the real Shifts sheet (headers with spaces, blanks for PT)."""
    rows = []
    for plan, paid, breaks, meal in [("WP-FT", 8, 2, 30), ("WP-PT", 4, 1, 0)]:
        for code, name, start in [("s1", "Early", 8), ("s2", "Midday", 10), ("s3", "Late", 12)]:
            elapsed = paid + meal / 60
            end_h, end_m = start + int(elapsed), int((elapsed % 1) * 60)
            rows.append(
                {
                    "work plan id": plan,
                    "shift code": code,
                    "shift name": name,
                    "start time": f"{start:02d}:00",
                    "end time": f"{end_h:02d}:{end_m:02d}",
                    "paid hours": paid,
                    "elapsed hours": elapsed,
                    "paid break count": breaks,
                    "paid break minutes": 15,
                    "unpaid meal minutes": meal,
                    "break1 start offset min": 120,
                    "meal start offset min": 240 if meal else None,
                    "break2 start offset min": 375 if breaks == 2 else None,
                    "minimum rest hours": 11,
                    "max consecutive workdays": 5,
                    "workdays per week": 5,
                }
            )
    return pd.DataFrame(rows)


def write_workbook(path: Path, agents: pd.DataFrame, dictionary: pd.DataFrame) -> Path:
    with pd.ExcelWriter(path) as writer:
        agents.to_excel(writer, sheet_name="Agents", index=False)
        shifts_frame().to_excel(writer, sheet_name="Shifts", index=False)
        dictionary.to_excel(writer, sheet_name="Dictionary", index=False)
    return path


def acd_hourly_frame() -> pd.DataFrame:
    """Two days x 24 hours for Q-1 and Q-3. Day 2 of Q-3 handles nothing (AHT undefined)."""
    rows = []
    for bu, mu, q in [("BU-1", "MU-1", "Q-1"), ("BU-2", "MU-2", "Q-3")]:
        for day in ["2026-06-23", "2026-06-24"]:
            for hour in range(24):
                open_hour = 8 <= hour < 20
                handled = 5 if open_hour and not (q == "Q-3" and day == "2026-06-24") else 0
                rows.append(
                    {
                        "bu_id": bu,
                        "mu_id": mu,
                        "queue_id": q,
                        "interval_start_local": pd.Timestamp(f"{day} {hour:02d}:00"),
                        "calls_offered": 6 if open_hour else 0,
                        "calls_handled": handled,
                        "handle_seconds": handled * (300 if hour < 14 else 600),
                        "aht_seconds": (300 if hour < 14 else 600) if handled else None,
                    }
                )
    return pd.DataFrame(rows)


def write_acd(path: Path, hourly: pd.DataFrame, events: pd.DataFrame | None = None) -> Path:
    with pd.ExcelWriter(path) as writer:
        hourly.to_excel(writer, sheet_name="Queue hourly", index=False)
        if events is not None:
            events.to_excel(writer, sheet_name="Events", index=False)
    return path


@pytest.fixture
def hierarchy_path(tmp_path: Path) -> Path:
    path = tmp_path / "hierarchy.yaml"
    path.write_text(HIERARCHY_YAML)
    return path


@pytest.fixture
def workbook_path(tmp_path: Path) -> Path:
    return write_workbook(tmp_path / "agents.xlsx", agents_frame(), dictionary_frame())


@pytest.fixture
def acd_path(tmp_path: Path) -> Path:
    return write_acd(tmp_path / "acd.xlsx", acd_hourly_frame())


@pytest.fixture
def fixture_config(
    tmp_path: Path,
    hierarchy_path: Path,
    workbook_path: Path,
    acd_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    config = tmp_path / "wfm.yaml"
    config.write_text(
        f"""
timezone: America/New_York
paths:
  hierarchy_yaml: {hierarchy_path}
  agents_xlsx: {workbook_path}
  acd_xlsx: {acd_path}
  runs_dir: {tmp_path / "runs"}
schedule:
  horizon_start: 2026-09-28
  horizon_weeks: 6
"""
    )
    monkeypatch.setenv("WFM_CONFIG_PATH", str(config))
    main._cache.clear()
    yield config
    main._cache.clear()
