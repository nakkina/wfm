"""Loads the YAML configuration file into typed settings.

Only paths are modelled for now; shift templates, work rules and service targets
are added with the pipeline stages that use them.
"""

import os
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]


class Paths(BaseModel):
    hierarchy_yaml: Path
    agents_xlsx: Path
    acd_xlsx: Path
    runs_dir: Path


class ForecastSettings(BaseModel):
    horizon_days: int = Field(default=21, ge=7, le=42)
    levels: list[int] = [80, 95]
    cv_windows: int = Field(default=6, ge=2)
    cv_step_days: int = Field(default=7, ge=1)


class ScheduleSettings(BaseModel):
    horizon_start: date
    horizon_weeks: int = Field(default=6, ge=1, le=6)


class Config(BaseModel):
    timezone: str
    paths: Paths
    forecast: ForecastSettings = ForecastSettings()
    schedule: ScheduleSettings


def resolve(path: Path) -> Path:
    """Resolves a config path relative to the wfm/ folder."""
    return path if path.is_absolute() else REPO_ROOT / path


def load_config(path: Path | None = None) -> Config:
    config_path = path or Path(os.environ.get("WFM_CONFIG_PATH", "config/wfm.example.yaml"))
    with resolve(config_path).open() as f:
        return Config.model_validate(yaml.safe_load(f))
