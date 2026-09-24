"""Response shape for one queue's forecast, built from a stored run."""

from datetime import date

import pandas as pd
from pydantic import BaseModel

from wfm.forecast.daily import MODEL_LABELS
from wfm.forecast.store import StoredForecast


class Interval(BaseModel):
    level: int
    lo: float
    hi: float


class ForecastPoint(BaseModel):
    date: date
    value: float
    intervals: list[Interval]


class ModelScore(BaseModel):
    model: str
    label: str
    score: float
    chosen: bool


class Diagnostics(BaseModel):
    weekday_share: float  # share of normal-day variance explained by weekday (0–1)
    lag1_autocorr: float | None  # day-to-day persistence after removing the weekday pattern
    event_days: int  # history days replaced for training because of listed events


class TargetForecast(BaseModel):
    target: str
    model: str
    model_label: str
    metric: str  # how `score` is measured
    scores: list[ModelScore]
    diagnostics: Diagnostics | None
    points: list[ForecastPoint]


class HistoryDay(BaseModel):
    date: date
    calls_offered: int
    calls_handled: int
    aht_seconds: float | None  # None when no calls were handled that day
    event: str | None  # Events sheet label; these days are cleaned for training


class QueueForecast(BaseModel):
    run_id: str
    created_at: str
    queue_id: str
    history_start: date
    history_end: date
    horizon_start: date
    horizon_days: int
    levels: list[int]
    interval_method: str
    history: list[HistoryDay]
    volume: TargetForecast | None
    aht: TargetForecast | None
    warnings: list[str]


METRICS = {
    "volume": "WAPE over backtests (lower is better)",
    "aht": "Handled-call-weighted MAE in seconds over backtests (lower is better)",
}
INTERVAL_METHOD = (
    "Split-conformal prediction intervals from absolute backtest errors of the chosen model "
    "against raw actuals (event days included), pooled by forecast week"
)


def queue_forecast(stored: StoredForecast, queue_id: str) -> QueueForecast | None:
    settings = stored.meta["settings"]
    levels: list[int] = settings["levels"]
    targets = {t: _target(stored, queue_id, t, levels) for t in ("volume", "aht")}
    if all(t is None for t in targets.values()):
        return None
    points = stored.points[stored.points["queue_id"] == queue_id]
    history = stored.history[stored.history["queue_id"] == queue_id].sort_values("date")
    return QueueForecast(
        run_id=stored.run_id,
        created_at=stored.meta["created_at"],
        queue_id=queue_id,
        history_start=date.fromisoformat(stored.meta["history_start"]),
        history_end=date.fromisoformat(stored.meta["history_end"]),
        horizon_start=points["date"].min(),
        horizon_days=settings["horizon_days"],
        levels=levels,
        interval_method=INTERVAL_METHOD,
        history=[
            HistoryDay(
                date=r["date"],
                calls_offered=int(r["calls_offered"]),
                calls_handled=int(r["calls_handled"]),
                aht_seconds=None
                if pd.isna(r["aht_seconds"])
                else round(float(r["aht_seconds"]), 1),
                event=r["event"] if isinstance(r["event"], str) else None,
            )
            for r in history.to_dict("records")
        ],
        volume=targets["volume"],
        aht=targets["aht"],
        warnings=[w for w in stored.meta["warnings"] if queue_id in w],
    )


def _target(
    stored: StoredForecast, queue_id: str, target: str, levels: list[int]
) -> TargetForecast | None:
    pts = stored.points[
        (stored.points["queue_id"] == queue_id) & (stored.points["target"] == target)
    ]
    if pts.empty:
        return None
    pts = pts.sort_values("date")
    scores = stored.scores[
        (stored.scores["queue_id"] == queue_id) & (stored.scores["target"] == target)
    ]
    model = str(pts["model"].iloc[0])
    diag = stored.diagnostics[
        (stored.diagnostics["queue_id"] == queue_id) & (stored.diagnostics["target"] == target)
    ]
    diagnostics = None
    if not diag.empty:
        d = diag.iloc[0]
        diagnostics = Diagnostics(
            weekday_share=float(d["weekday_share"]),
            lag1_autocorr=None if pd.isna(d["lag1_autocorr"]) else float(d["lag1_autocorr"]),
            event_days=int(d["event_days"]),
        )
    return TargetForecast(
        target=target,
        model=model,
        model_label=MODEL_LABELS.get(model, model),
        metric=METRICS[target],
        diagnostics=diagnostics,
        scores=[
            ModelScore(
                model=str(r["model"]),
                label=MODEL_LABELS.get(str(r["model"]), str(r["model"])),
                score=round(float(r["score"]), 4),
                chosen=bool(r["chosen"]),
            )
            for r in scores.to_dict("records")
        ],
        points=[
            ForecastPoint(
                date=r["date"],
                value=round(float(r["value"]), 2),
                intervals=[
                    Interval(
                        level=lv,
                        lo=round(float(r[f"lo_{lv}"]), 2),
                        hi=round(float(r[f"hi_{lv}"]), 2),
                    )
                    for lv in levels
                ],
            )
            for r in pts.to_dict("records")
        ],
    )
