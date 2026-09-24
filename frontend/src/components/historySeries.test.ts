import { expect, test } from 'vitest'

import type { HistoryDay, TargetForecast } from '../api/forecast.ts'
import {
  eventMarkers,
  forecastBands,
  forecastStartMarker,
  historySeries,
  predictabilityNote,
  scoreSummary,
} from './historySeries.ts'
import { formatHours, shortDate, shortTime, toMinutes } from './scheduleUtils.ts'

const days: HistoryDay[] = [
  { date: '2026-06-23', calls_offered: 900, calls_handled: 860, aht_seconds: 600, event: null },
  { date: '2026-06-24', calls_offered: 0, calls_handled: 0, aht_seconds: null, event: null },
  { date: '2026-06-25', calls_offered: 1800, calls_handled: 1700, aht_seconds: 690, event: 'Outage' },
]

test('historySeries keeps calls offered and leaves AHT gaps as null', () => {
  const { volume, aht } = historySeries(days)
  expect(volume).toEqual({ dates: ['2026-06-23', '2026-06-24', '2026-06-25'], values: [900, 0, 1800] })
  expect(aht.values).toEqual([600, null, 690])
})

test('eventMarkers picks the event days of a series with their labels', () => {
  const { aht } = historySeries(days)
  expect(eventMarkers(days, aht)).toEqual({ dates: ['2026-06-25'], values: [690], labels: ['Outage'] })
})

test('predictabilityNote explains flat forecasts for mostly-noise series', () => {
  expect(predictabilityNote({ weekday_share: 0.03, lag1_autocorr: 0.28, event_days: 5 })).toBe(
    'Weekday pattern explains 3% of daily variation; some day-to-day carry-over that fades within days. ' +
      'Most variation is unpredictable noise, so the forecast stays near the typical level and the bands show the likely range. ' +
      '5 event days excluded from training (✕).',
  )
  expect(predictabilityNote({ weekday_share: 0.72, lag1_autocorr: 0.1, event_days: 0 })).toBe(
    'Weekday pattern explains 72% of daily variation; little day-to-day carry-over.',
  )
})

test('schedule time helpers', () => {
  expect(toMinutes('10:15:00')).toBe(615)
  expect(shortTime('08:30:00')).toBe('08:30')
  expect(shortTime(null)).toBe('')
  expect(shortDate('2026-09-28')).toBe('Sep 28')
  expect(formatHours(40)).toBe('40h')
  expect(formatHours(7.5)).toBe('7.5h')
})

const volumeForecast: TargetForecast = {
  target: 'volume',
  model: 'AutoETS',
  model_label: 'AutoETS (automatic ETS; weekly season allowed)',
  diagnostics: null,
  metric: 'WAPE',
  scores: [
    { model: 'SeasonalNaive', label: 'Seasonal naive', score: 0.185, chosen: false },
    { model: 'AutoETS', label: 'AutoETS', score: 0.151, chosen: true },
  ],
  points: [
    { date: '2026-09-23', value: 900, intervals: [{ level: 80, lo: 800, hi: 1000 }, { level: 95, lo: 700, hi: 1100 }] },
    { date: '2026-09-24', value: 950, intervals: [{ level: 80, lo: 850, hi: 1050 }, { level: 95, lo: 750, hi: 1150 }] },
  ],
}

test('forecastBands orders widest band first and pairs lo/hi by date', () => {
  const bands = forecastBands(volumeForecast.points)
  expect(bands.map((b) => b.level)).toEqual([95, 80])
  expect(bands[0]).toEqual({ level: 95, dates: ['2026-09-23', '2026-09-24'], lo: [700, 750], hi: [1100, 1150] })
})

test('forecast start marker sits between last actual and first forecast day', () => {
  expect(forecastStartMarker('2026-09-22')).toBe('2026-09-22T12:00:00')
})

test('scoreSummary formats the chosen model against the baseline', () => {
  expect(scoreSummary(volumeForecast)).toBe('Backtest WAPE 15.1% — seasonal-naive baseline 18.5%')
  const aht: TargetForecast = {
    ...volumeForecast,
    target: 'aht',
    scores: [{ model: 'SeasonalNaive', label: 'Seasonal naive', score: 36, chosen: true }],
  }
  expect(scoreSummary(aht)).toBe('Backtest weighted MAE 36.0 s')
})
