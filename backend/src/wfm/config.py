"""Loads the YAML configuration file into typed settings.

Only paths are modelled for now; shift templates, work rules and service targets
are added with the pipeline stages that use them.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[3]


class Paths(BaseModel):
    agents_csv: Path
    acd_history_csv: Path
    runs_dir: Path


class Config(BaseModel):
    timezone: str
    paths: Paths


def load_config(path: Path | None = None) -> Config:
    config_path = path or Path(os.environ.get("WFM_CONFIG_PATH", "config/wfm.example.yaml"))
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    with config_path.open() as f:
        return Config.model_validate(yaml.safe_load(f))
