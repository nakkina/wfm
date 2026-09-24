import { errorDetail } from './hierarchy.ts'

export type Interval = { level: number; lo: number; hi: number }
export type ForecastPoint = { date: string; value: number; intervals: Interval[] }
export type ModelScore = { model: string; label: string; score: number; chosen: boolean }

export type Diagnostics = {
  weekday_share: number // share of normal-day variance explained by weekday, 0–1
  lag1_autocorr: number | null // day-to-day persistence after removing the weekday pattern
  event_days: number // history days replaced for training because of listed events
}

export type TargetForecast = {
  target: 'volume' | 'aht'
  model: string
  model_label: string
  metric: string
  scores: ModelScore[]
  diagnostics: Diagnostics | null
  points: ForecastPoint[]
}

export type HistoryDay = {
  date: string
  calls_offered: number
  calls_handled: number
  aht_seconds: number | null // null when no calls were handled that day
  event: string | null // Events sheet label; cleaned for model training
}

/** Everything the Forecasts tab draws, served from the saved forecast run. */
export type QueueForecast = {
  run_id: string
  created_at: string
  queue_id: string
  history_start: string
  history_end: string
  horizon_start: string
  horizon_days: number
  levels: number[]
  interval_method: string
  history: HistoryDay[]
  volume: TargetForecast | null
  aht: TargetForecast | null
  warnings: string[]
}

export async function fetchQueueForecast(queueId: string): Promise<QueueForecast> {
  const response = await fetch(`/api/queues/${encodeURIComponent(queueId)}/forecast`)
  if (!response.ok) throw new Error(await errorDetail(response, 'Failed to load forecast'))
  return (await response.json()) as QueueForecast
}
