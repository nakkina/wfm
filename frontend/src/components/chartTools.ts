import type { HistoryDay, TargetForecast } from '../api/forecast.ts'

/** Shared x-axis state for the linked charts: null means "show everything" (autorange). */
export type XRange = [string, string] | null

/**
 * Reads a Plotly relayout event. Returns the new x-range, null for autorange/reset,
 * or undefined when the event doesn't touch the x-axis (e.g. a y-only change or legend click).
 */
export function xRangeFromRelayout(event: Record<string, unknown>): XRange | undefined {
  if (event['xaxis.autorange'] === true) return null
  const pair = event['xaxis.range']
  if (Array.isArray(pair) && pair.length === 2) return [String(pair[0]), String(pair[1])]
  const lo = event['xaxis.range[0]']
  const hi = event['xaxis.range[1]']
  if (lo !== undefined && hi !== undefined) return [String(lo), String(hi)]
  return undefined
}

/** Last `weeksOfHistory` weeks of actuals through the end of the forecast, with a half-day margin. */
export function forecastFocusRange(historyEnd: string, forecastEnd: string, weeksOfHistory = 4): [string, string] {
  const [y, m, d] = historyEnd.split('-').map(Number)
  const start = new Date(Date.UTC(y, m - 1, d - weeksOfHistory * 7 + 1))
  return [`${start.toISOString().slice(0, 10)} 00:00`, `${forecastEnd} 12:00`]
}

export type SelectedPoint = { series: string; value: number }

export type SelectionSummary = {
  actualDays: number
  actualTotal: number
  actualMean: number
  forecastDays: number
  forecastTotal: number
  forecastMean: number
}

/** Totals and means of the selected Actual and Forecast points (bands and event marks ignored). */
export function summarizeSelection(points: SelectedPoint[]): SelectionSummary | null {
  const pick = (name: string) => points.filter((p) => p.series === name && Number.isFinite(p.value)).map((p) => p.value)
  const actual = pick('Actual')
  const forecast = pick('Forecast')
  if (actual.length === 0 && forecast.length === 0) return null
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0)
  return {
    actualDays: actual.length,
    actualTotal: sum(actual),
    actualMean: actual.length ? sum(actual) / actual.length : Number.NaN,
    forecastDays: forecast.length,
    forecastTotal: sum(forecast),
    forecastMean: forecast.length ? sum(forecast) / forecast.length : Number.NaN,
  }
}

type CsvCell = string | number | null | undefined

function csvCell(value: CsvCell): string {
  if (value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return ''
  const text = String(value)
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/**
 * One row per day: actuals (with event label) for history, forecast and interval bounds for
 * the horizon. `actual` picks the metric from each history day.
 */
export function chartCsv(
  history: HistoryDay[],
  actual: (day: HistoryDay) => number | null,
  forecast: TargetForecast | null,
): string {
  const levels = forecast
    ? [...new Set(forecast.points.flatMap((p) => p.intervals.map((i) => i.level)))].sort((a, b) => a - b)
    : []
  const header = ['date', 'actual', 'event', 'forecast', ...levels.flatMap((l) => [`lo_${l}`, `hi_${l}`])]
  const rows: CsvCell[][] = history.map((d) => [d.date, actual(d), d.event, null, ...levels.flatMap(() => [null, null])])
  for (const p of forecast?.points ?? []) {
    const bounds = levels.flatMap((l) => {
      const i = p.intervals.find((x) => x.level === l)
      return [i?.lo, i?.hi]
    })
    rows.push([p.date, null, null, p.value, ...bounds])
  }
  return [header, ...rows].map((r) => r.map(csvCell).join(',')).join('\n') + '\n'
}

export function downloadText(filename: string, text: string, type = 'text/csv'): void {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

/** "Card Purchase Disputes" + "call volume" -> "card-purchase-disputes_call-volume_2026-09-24" */
export function chartFileName(queueName: string, metric: string, date = new Date()): string {
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  return `${slug(queueName)}_${slug(metric)}_${date.toISOString().slice(0, 10)}`
}
