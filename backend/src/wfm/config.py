"""Loads the YAML configuration file into typed settings.

Only paths are modelled for now; shift templates, work rules and service targets
are added with the pipeline stages that use them.
"""

import os
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

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


class StaffingSettings(BaseModel):
    interval_minutes: int = Field(default=15, ge=5, le=60)
    service_level_target: float = Field(default=0.80, gt=0, lt=1)
    answer_threshold_seconds: float = Field(default=20, gt=0)
    max_occupancy: float = Field(default=0.85, gt=0, le=1)
    residual_shrinkage: float = Field(default=0.10, ge=0, lt=1)
    closing_minutes: int = Field(default=15, ge=0)
    within_hour_proportions: list[float] | None = None

    @model_validator(mode="after")
    def proportions_match_grid(self) -> StaffingSettings:
        if 60 % self.interval_minutes:
            raise ValueError("interval_minutes must divide 60")
        props = self.within_hour_proportions
        if props is not None:
            if len(props) != 60 // self.interval_minutes or any(p < 0 for p in props):
                raise ValueError(
                    "within_hour_proportions needs one non-negative value per interval"
                )
            if abs(sum(props) - 1) > 1e-6:
                raise ValueError("within_hour_proportions must sum to 1")
        return self


class StageWeights(BaseModel):
    preferred_shift_miss: int = 10
    preferred_day_off_worked: int = 10
    weekend_spread: int = 5
    late_shift_spread: int = 5
    start_change: int = 1


class SchedulingSettings(BaseModel):
    break_stagger_slots: int = Field(default=0, ge=0, le=4)
    stage_time_limit_seconds: list[float] = Field(default=[20, 15, 15], min_length=3, max_length=3)
    num_workers: int = Field(default=8, ge=1)
    leave_csv: Path | None = None
    assume_rested_at_start: bool = True
    weights: StageWeights = StageWeights()


class ScheduleSettings(BaseModel):
    horizon_start: date
    horizon_weeks: int = Field(default=6, ge=1, le=6)


class Config(BaseModel):
    timezone: str
    paths: Paths
    forecast: ForecastSettings = ForecastSettings()
    staffing: StaffingSettings = StaffingSettings()
    scheduling: SchedulingSettings = SchedulingSettings()
    schedule: ScheduleSettings


def resolve(path: Path) -> Path:
    """Resolves a config path relative to the wfm/ folder."""
    return path if path.is_absolute() else REPO_ROOT / path


def load_config(path: Path | None = None) -> Config:
    config_path = path or Path(os.environ.get("WFM_CONFIG_PATH", "config/wfm.example.yaml"))
    with resolve(config_path).open() as f:
        return Config.model_validate(yaml.safe_load(f))
