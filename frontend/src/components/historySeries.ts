import type { Diagnostics, ForecastPoint, HistoryDay, TargetForecast } from '../api/forecast.ts'

export type Series = { dates: string[]; values: (number | null)[] }
export type EventMarkers = { dates: string[]; values: (number | null)[]; labels: string[] }

/** Daily series for the two charts. AHT stays null (a gap) on days with no handled calls. */
export function historySeries(days: HistoryDay[]): { volume: Series; aht: Series } {
  const dates = days.map((d) => d.date)
  return {
    volume: { dates, values: days.map((d) => d.calls_offered) },
    aht: { dates, values: days.map((d) => d.aht_seconds) },
  }
}

/** Points on event days (holidays, incidents, campaigns), to mark them on a series. */
export function eventMarkers(days: HistoryDay[], series: Series): EventMarkers {
  const idx = days.flatMap((d, i) => (d.event ? [i] : []))
  return {
    dates: idx.map((i) => series.dates[i]),
    values: idx.map((i) => series.values[i]),
    labels: idx.map((i) => days[i].event ?? ''),
  }
}

export type Band = { level: number; dates: string[]; lo: number[]; hi: number[] }

/** Interval bands, widest first so narrower bands draw on top. */
export function forecastBands(points: ForecastPoint[]): Band[] {
  const levels = [...new Set(points.flatMap((p) => p.intervals.map((i) => i.level)))].sort((a, b) => b - a)
  return levels.map((level) => {
    const at = (p: ForecastPoint) => p.intervals.find((i) => i.level === level)
    return {
      level,
      dates: points.map((p) => p.date),
      lo: points.map((p) => at(p)?.lo ?? Number.NaN),
      hi: points.map((p) => at(p)?.hi ?? Number.NaN),
    }
  })
}

/** Midday on the last history date, so the marker sits between the last actual and first forecast. */
export function forecastStartMarker(historyEnd: string): string {
  return `${historyEnd}T12:00:00`
}

/** e.g. "Backtest WAPE 15.1% — seasonal-naive baseline 18.5%" or "Backtest weighted MAE 25.0 s". */
export function scoreSummary(forecast: TargetForecast): string {
  const fmt = (s: number) => (forecast.target === 'volume' ? `${(s * 100).toFixed(1)}%` : `${s.toFixed(1)} s`)
  const name = forecast.target === 'volume' ? 'WAPE' : 'weighted MAE'
  const chosen = forecast.scores.find((s) => s.chosen)
  const baseline = forecast.scores.find((s) => s.model === 'SeasonalNaive')
  if (!chosen) return ''
  const base = baseline && !baseline.chosen ? ` — seasonal-naive baseline ${fmt(baseline.score)}` : ''
  return `Backtest ${name} ${fmt(chosen.score)}${base}`
}

/** Plain-language predictability note, e.g. explaining why a forecast looks flat. */
export function predictabilityNote(d: Diagnostics): string {
  const weekday = Math.round(d.weekday_share * 100)
  const persistence =
    d.lag1_autocorr === null
      ? ''
      : d.lag1_autocorr >= 0.5
        ? 'strong day-to-day carry-over'
        : d.lag1_autocorr >= 0.2
          ? 'some day-to-day carry-over that fades within days'
          : 'little day-to-day carry-over'
  const flat =
    weekday < 15 && (d.lag1_autocorr ?? 0) < 0.5
      ? ' Most variation is unpredictable noise, so the forecast stays near the typical level and the bands show the likely range.'
      : ''
  const events = d.event_days > 0 ? ` ${d.event_days} event day${d.event_days === 1 ? '' : 's'} excluded from training (✕).` : ''
  return `Weekday pattern explains ${weekday}% of daily variation${persistence ? `; ${persistence}` : ''}.${flat}${events}`
}
