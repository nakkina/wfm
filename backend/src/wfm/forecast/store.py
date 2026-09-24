"""Saves forecast runs under runs/forecasts/<run_id>/ and finds the one matching current inputs.

Each run directory holds everything the Forecasts tab needs, so charts render from these
files alone — the ACD workbook is only parsed when a new run is generated:
  points.csv       forecasts and interval bounds per queue/target/day
  scores.csv       backtest score of every candidate model
  diagnostics.csv  weekday share and day-to-day persistence per queue/target
  history.csv      daily actuals (with event labels) the run was trained on
  meta.json        source fingerprint, settings, method version, runtime, package versions

A run is reused only if source fingerprint, settings and method version all match.
Old runs are left in place (runs/ is gitignored); nothing is deleted automatically.
"""

import hashlib
import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pandas as pd

from wfm.config import ForecastSettings
from wfm.forecast.daily import METHOD_VERSION, ForecastResult

FORECASTS_DIR = "forecasts"
FILES = ("points", "scores", "diagnostics", "history")


@dataclass(frozen=True)
class StoredForecast:
    run_id: str
    meta: dict[str, Any]
    points: pd.DataFrame
    scores: pd.DataFrame
    diagnostics: pd.DataFrame
    history: pd.DataFrame


def file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_run(
    runs_dir: Path,
    result: ForecastResult,
    settings: ForecastSettings,
    source: Path,
    fingerprint: str,
    runtime_seconds: float,
) -> StoredForecast:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{fingerprint[:8]}"
    final = runs_dir / FORECASTS_DIR / run_id
    staging = final.with_name(f".{run_id}.tmp")  # written fully, then renamed (PRD §6)
    staging.mkdir(parents=True, exist_ok=False)

    meta = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method_version": METHOD_VERSION,
        "source_file": source.name,
        "source_sha256": fingerprint,
        "settings": settings.model_dump(),
        "history_start": result.history_start.isoformat(),
        "history_end": result.history_end.isoformat(),
        "runtime_seconds": round(runtime_seconds, 2),
        "warnings": result.warnings,
        "versions": {
            "python": platform.python_version(),
            "statsforecast": version("statsforecast"),
            "pandas": version("pandas"),
        },
    }
    for name in FILES:
        getattr(result, name).to_csv(staging / f"{name}.csv", index=False)
    (staging / "meta.json").write_text(json.dumps(meta, indent=2))
    staging.rename(final)
    return StoredForecast(
        run_id=run_id,
        meta=meta,
        points=result.points,
        scores=result.scores,
        diagnostics=result.diagnostics,
        history=result.history,
    )


def load_matching(
    runs_dir: Path, fingerprint: str, settings: ForecastSettings
) -> StoredForecast | None:
    """Newest completed run built from this exact source file, settings and method."""
    root = runs_dir / FORECASTS_DIR
    if not root.is_dir():
        return None
    for run_dir in sorted((d for d in root.iterdir() if not d.name.startswith(".")), reverse=True):
        meta_path = run_dir / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text())
        if (
            meta.get("method_version") == METHOD_VERSION
            and meta.get("source_sha256") == fingerprint
            and meta.get("settings") == settings.model_dump()
            and all((run_dir / f"{name}.csv").is_file() for name in FILES)
        ):
            frames = {name: pd.read_csv(run_dir / f"{name}.csv") for name in FILES}
            for name in ("points", "history"):
                frames[name]["date"] = pd.to_datetime(frames[name]["date"]).dt.date
            history = frames["history"]
            history["event"] = history["event"].astype(object).where(history["event"].notna(), None)
            return StoredForecast(run_id=meta["run_id"], meta=meta, **frames)
    return None
