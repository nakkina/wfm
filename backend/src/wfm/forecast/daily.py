"""Daily call-volume and AHT forecasts per queue with StatsForecast.

Method (per queue, per target):
1. Event cleaning: days labelled in the ACD Events sheet (holidays, incidents, campaigns)
   are replaced *for training only* by the queue's median for that weekday on normal days,
   so one-off shocks don't distort weekday profiles or model fits.
2. Candidates: SeasonalNaive (same weekday last week — the baseline), WeekdayAverage (mean
   of the same weekday over the last 4 weeks, PRD §4A), AutoETS and AutoARIMA (both may pick
   a weekly season; ARIMA also captures the short-term day-to-day persistence seen in AHT).
3. Rolling-origin backtests: `cv_windows` windows of `horizon_days`, `cv_step_days` apart,
   scored on normal (non-event) days. Volume: WAPE. AHT: handled-call-weighted MAE (PRD §4A).
4. The baseline is kept unless another candidate scores strictly better.
5. Point forecasts come from the chosen model refit on the full (cleaned) history.
6. Prediction intervals are split-conformal: absolute backtest errors of the chosen model
   against the *raw* actuals — event days included, since unplanned incidents recur — pooled
   by forecast week (days 1–7, 8–14, ...) so later weeks can widen. StatsForecast's built-in
   conformal intervals use only `n_windows` errors per horizon step, which with ~90 days of
   history gives near-identical 80% and 95% bands.

Why AHT forecasts can look flat: in this data the weekday explains only a few percent of
daily AHT variance and day-to-day persistence fades within days, so the best point
forecast settles near the queue's typical AHT. The unpredictable variation is what the
intervals express. `diagnostics` reports both measures per queue.

Limitations: backtests share data with model selection, so intervals may be slightly
optimistic; three months of history cannot show annual seasonality; future events are not
modelled (no future event calendar is available).
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import AutoARIMA, AutoETS, SeasonalNaive, SeasonalWindowAverage

from wfm.config import ForecastSettings

# Bump when the method changes so saved runs from an older method are not reused.
METHOD_VERSION = 2

Target = Literal["volume", "aht"]
BASELINE = "SeasonalNaive"
SEASON = 7
WEEKDAY_AVERAGE_WEEKS = 4
MODEL_LABELS = {
    "SeasonalNaive": "Seasonal naive (same weekday last week)",
    "WeekdayAverage": "Same-weekday average (last 4 weeks)",
    "AutoETS": "AutoETS (automatic ETS; weekly season allowed)",
    "AutoARIMA": "AutoARIMA (automatic ARIMA; weekly season allowed)",
}


class ForecastError(ValueError):
    """History is too short or inconsistent for the configured backtests."""


@dataclass(frozen=True)
class ForecastResult:
    points: pd.DataFrame  # queue_id, target, date, value, model, lo_<L>, hi_<L> per level
    scores: pd.DataFrame  # queue_id, target, model, score, chosen
    diagnostics: pd.DataFrame  # queue_id, target, weekday_share, lag1_autocorr, event_days
    history: (
        pd.DataFrame
    )  # queue_id, date, calls_offered, calls_handled, handle_seconds, aht_seconds, event
    warnings: list[str]
    history_start: date
    history_end: date


def _models() -> list[object]:
    return [
        SeasonalNaive(season_length=SEASON),
        SeasonalWindowAverage(
            season_length=SEASON, window_size=WEEKDAY_AVERAGE_WEEKS, alias="WeekdayAverage"
        ),
        AutoETS(season_length=SEASON),
        AutoARIMA(season_length=SEASON),
    ]


def model_names() -> list[str]:
    return list(MODEL_LABELS)


def forecast_all(daily: pd.DataFrame, settings: ForecastSettings) -> ForecastResult:
    """`daily`: one row per queue_id/date with calls_offered, calls_handled, handle_seconds and
    an optional `event` label (None on normal days)."""
    daily = daily.copy()
    daily["ds"] = pd.to_datetime(daily["date"])
    if "event" not in daily.columns:
        daily["event"] = None
    daily["is_event"] = daily["event"].notna()
    _check_history(daily, settings)

    warnings: list[str] = []
    daily["aht_seconds"] = daily["handle_seconds"] / daily["calls_handled"].where(
        daily["calls_handled"] > 0
    )
    volume = daily.assign(y=daily["calls_offered"].astype(float))
    aht = daily.assign(y=daily["aht_seconds"])
    gaps = sorted(aht.loc[aht["y"].isna(), "queue_id"].unique())
    if gaps:
        # A day with no handled calls has no AHT; don't invent one (PRD §2: missing is not zero).
        warnings.append(f"AHT not forecast for {', '.join(gaps)}: days with no handled calls")
        aht = aht[~aht["queue_id"].isin(gaps)]

    targets: list[tuple[Target, pd.DataFrame]] = [("volume", volume), ("aht", aht)]
    points, scores, diagnostics = [], [], []
    for target, frame in targets:
        if frame.empty:
            continue
        target_points, target_scores = _forecast_target(frame, target, settings)
        points.append(target_points)
        scores.append(target_scores)
        diagnostics.append(_diagnostics(frame, target))

    history = daily[
        [
            "queue_id",
            "date",
            "calls_offered",
            "calls_handled",
            "handle_seconds",
            "aht_seconds",
            "event",
        ]
    ].sort_values(["queue_id", "date"])
    return ForecastResult(
        points=pd.concat(points, ignore_index=True),
        scores=pd.concat(scores, ignore_index=True),
        diagnostics=pd.concat(diagnostics, ignore_index=True),
        history=history.reset_index(drop=True),
        warnings=warnings,
        history_start=daily["ds"].min().date(),
        history_end=daily["ds"].max().date(),
    )


def _check_history(daily: pd.DataFrame, settings: ForecastSettings) -> None:
    lengths = daily.groupby("queue_id")["ds"].agg(["min", "max", "count"])
    span = (lengths["max"] - lengths["min"]).dt.days + 1
    if (span != lengths["count"]).any():
        gappy = ", ".join(lengths.index[span != lengths["count"]][:5])
        raise ForecastError(f"Daily history has missing dates for: {gappy}")
    needed = (
        settings.horizon_days
        + (settings.cv_windows - 1) * settings.cv_step_days
        + SEASON * WEEKDAY_AVERAGE_WEEKS
    )
    shortest = int(lengths["count"].min())
    if shortest < needed:
        raise ForecastError(
            f"Need at least {needed} days of history for {settings.cv_windows} backtests of "
            f"{settings.horizon_days} days; shortest queue has {shortest}"
        )


def clean_events(frame: pd.DataFrame) -> pd.Series:
    """`y` with event days replaced by the queue's median `y` for that weekday on normal days.

    Falls back to the queue's overall normal-day median if a weekday has no normal days.
    """
    weekday = frame["ds"].dt.dayofweek
    normal = frame[~frame["is_event"]]
    by_weekday = normal.groupby([normal["queue_id"], normal["ds"].dt.dayofweek])["y"].median()
    overall = normal.groupby("queue_id")["y"].median()
    cleaned = frame["y"].copy()
    for idx in frame.index[frame["is_event"]]:
        key = (frame.at[idx, "queue_id"], weekday.at[idx])
        cleaned.at[idx] = by_weekday.get(key, overall.get(frame.at[idx, "queue_id"], math.nan))
    return cleaned


def _forecast_target(
    frame: pd.DataFrame, target: Target, settings: ForecastSettings
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = frame.assign(y_train=clean_events(frame))
    df = frame.rename(columns={"queue_id": "unique_id"})[["unique_id", "ds", "y_train"]].rename(
        columns={"y_train": "y"}
    )
    sf = StatsForecast(models=_models(), freq="D", n_jobs=1)
    cv = sf.cross_validation(
        df=df,
        h=settings.horizon_days,
        n_windows=settings.cv_windows,
        step_size=settings.cv_step_days,
    )
    cv["horizon_day"] = (cv["ds"] - cv["cutoff"]).dt.days
    raw = frame.rename(columns={"queue_id": "unique_id", "y": "y_raw"})
    cv = cv.merge(
        raw[["unique_id", "ds", "y_raw", "calls_handled", "is_event"]],
        on=["unique_id", "ds"],
        how="left",
    )

    scores = _score(cv[~cv["is_event"]], target)
    chosen = _choose(scores)
    scores["chosen"] = scores["model"] == scores["unique_id"].map(chosen)

    fc = sf.forecast(df=df, h=settings.horizon_days)
    rows = []
    for queue_id, model in chosen.items():
        q_fc = fc[fc["unique_id"] == queue_id].sort_values("ds")
        q_cv = cv[cv["unique_id"] == queue_id]
        errors = (q_cv[model] - q_cv["y_raw"]).abs()  # raw actuals: event shocks count
        week = (q_cv["horizon_day"] - 1) // 7
        for step, (ds, value) in enumerate(zip(q_fc["ds"], q_fc[model], strict=True), start=1):
            bucket = errors[week == (step - 1) // 7].to_numpy()
            row: dict[str, object] = {
                "queue_id": queue_id,
                "target": target,
                "date": ds.date(),
                "value": _clip(float(value)),
                "model": model,
            }
            for level in settings.levels:
                q = conformal_quantile(bucket, level)
                row[f"lo_{level}"] = _clip(float(value) - q)
                row[f"hi_{level}"] = float(value) + q
            rows.append(row)

    scores = scores.rename(columns={"unique_id": "queue_id"}).assign(target=target)
    return pd.DataFrame(rows), scores[["queue_id", "target", "model", "score", "chosen"]]


def _score(cv: pd.DataFrame, target: Target) -> pd.DataFrame:
    records = []
    for queue_id, g in cv.groupby("unique_id"):
        for model in model_names():
            err = (g[model] - g["y_raw"]).abs()
            if target == "volume":
                total = g["y_raw"].sum()
                # WAPE is undefined when actual volume is all zero; fall back to MAE (PRD §4A).
                score = err.sum() / total if total > 0 else err.mean()
            else:
                score = (err * g["calls_handled"]).sum() / g["calls_handled"].sum()
            records.append({"unique_id": queue_id, "model": model, "score": float(score)})
    return pd.DataFrame(records)


def _choose(scores: pd.DataFrame) -> dict[str, str]:
    chosen: dict[str, str] = {}
    for queue_id, g in scores.groupby("unique_id"):
        by_model = dict(zip(g["model"], g["score"], strict=True))
        best = min(by_model, key=lambda m: by_model[m])
        # Keep the transparent baseline unless something is strictly better.
        chosen[str(queue_id)] = best if by_model[best] < by_model[BASELINE] else BASELINE
    return chosen


def _diagnostics(frame: pd.DataFrame, target: Target) -> pd.DataFrame:
    """How predictable each series is, on normal days: share of variance explained by
    weekday (eta squared) and lag-1 autocorrelation of weekday-adjusted values."""
    records = []
    for queue_id, g in frame.groupby("queue_id"):
        g = g.sort_values("ds")
        normal = g[~g["is_event"]]
        weekday = normal["ds"].dt.dayofweek
        weekday_mean = normal.groupby(weekday)["y"].transform("mean")
        total_var = float(((normal["y"] - normal["y"].mean()) ** 2).sum())
        share = (
            float(((weekday_mean - normal["y"].mean()) ** 2).sum()) / total_var
            if total_var
            else 0.0
        )
        residual = (normal["y"] - weekday_mean).reset_index(drop=True)
        # Undefined for a series with no residual variation (e.g. a perfectly repeating week).
        varies = len(residual) > 2 and float(residual.std()) > 1e-9
        lag1 = float(residual.autocorr(1)) if varies else math.nan
        records.append(
            {
                "queue_id": queue_id,
                "target": target,
                "weekday_share": round(share, 4),
                "lag1_autocorr": round(lag1, 4),  # NaN when undefined; the API reports null
                "event_days": int(g["is_event"].sum()),
            }
        )
    return pd.DataFrame(records)


def conformal_quantile(abs_errors: np.ndarray, level: int) -> float:
    """Split-conformal quantile: the ceil((n+1)·level)-th smallest absolute error."""
    n = len(abs_errors)
    if n == 0:
        return math.nan
    k = min(n, math.ceil((n + 1) * level / 100))
    return float(np.sort(abs_errors)[k - 1])


def _clip(value: float) -> float:
    # Negative calls or handle times are impossible.
    return value if math.isnan(value) else max(value, 0.0)
