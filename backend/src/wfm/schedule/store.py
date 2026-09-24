"""Schedule runs under runs/schedules/<run_id>/: status.json while running, outputs when done.

One generation at a time. A run left "running" by a process that no longer exists (e.g. the
backend restarted mid-run) is reported as failed/interrupted rather than running forever.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEDULES_DIR = "schedules"
ACTIVE_STATES = {"queued", "running"}


def schedules_root(runs_dir: Path) -> Path:
    return runs_dir / SCHEDULES_DIR


def new_run_dir(runs_dir: Path) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    path = schedules_root(runs_dir) / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_status(run_dir: Path, status: dict[str, Any]) -> None:
    status = {**status, "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    tmp = run_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2))
    tmp.replace(run_dir / "status.json")  # atomic, so readers never see half a file


def _alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)  # type: ignore[call-overload]
    except OSError, TypeError, ValueError:
        return False
    return True


def read_status(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "status.json"
    if not path.is_file():
        return None
    status: dict[str, Any] = json.loads(path.read_text())
    if status.get("state") in ACTIVE_STATES and "pid" in status and not _alive(status["pid"]):
        status = {**status, "state": "failed", "message": "Interrupted: the worker process stopped"}
        write_status(run_dir, status)
    return status


def list_runs(runs_dir: Path) -> list[Path]:
    root = schedules_root(runs_dir)
    if not root.is_dir():
        return []
    return sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)


def latest_run(runs_dir: Path, completed_only: bool = False) -> Path | None:
    for run_dir in list_runs(runs_dir):
        status = read_status(run_dir)
        if status is None:
            continue
        if not completed_only or (
            status["state"].startswith("completed") and (run_dir / "meta.json").is_file()
        ):
            return run_dir
    return None


def active_run(runs_dir: Path) -> Path | None:
    for run_dir in list_runs(runs_dir):
        status = read_status(run_dir)
        if status and status["state"] in ACTIVE_STATES:
            return run_dir
    return None
