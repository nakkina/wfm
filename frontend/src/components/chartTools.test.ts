import { expect, test } from 'vitest'

import type { HistoryDay, TargetForecast } from '../api/forecast.ts'
import {
  chartCsv,
  chartFileName,
  forecastFocusRange,
  summarizeSelection,
  xRangeFromRelayout,
} from './chartTools.ts'

test('xRangeFromRelayout reads zoom, pan and reset events', () => {
  expect(xRangeFromRelayout({ 'xaxis.range[0]': '2026-08-01', 'xaxis.range[1]': '2026-09-01' })).toEqual([
    '2026-08-01',
    '2026-09-01',
  ])
  expect(xRangeFromRelayout({ 'xaxis.range': ['2026-08-01', '2026-09-01'] })).toEqual(['2026-08-01', '2026-09-01'])
  expect(xRangeFromRelayout({ 'xaxis.autorange': true, 'yaxis.autorange': true })).toBeNull()
  expect(xRangeFromRelayout({ 'yaxis.range[0]': 0, 'yaxis.range[1]': 10 })).toBeUndefined()
  expect(xRangeFromRelayout({})).toBeUndefined()
})

test('forecastFocusRange covers the last 4 weeks of history through the forecast end', () => {
  expect(forecastFocusRange('2026-09-22', '2026-10-13')).toEqual(['2026-08-26 00:00', '2026-10-13 12:00'])
})

test('summarizeSelection totals actual and forecast points separately', () => {
  const summary = summarizeSelection([
    { series: 'Actual', value: 100 },
    { series: 'Actual', value: 300 },
    { series: 'Forecast', value: 250 },
    { series: '80% interval', value: 1 }, // bands ignored
  ])
  expect(summary).toEqual({
    actualDays: 2,
    actualTotal: 400,
    actualMean: 200,
    forecastDays: 1,
    forecastTotal: 250,
    forecastMean: 250,
  })
  expect(summarizeSelection([{ series: '95% interval', value: 5 }])).toBeNull()
})

test('chartCsv writes history rows then forecast rows with interval bounds', () => {
  const history: HistoryDay[] = [
    { date: '2026-09-21', calls_offered: 900, calls_handled: 880, aht_seconds: 600, event: null },
    { date: '2026-09-22', calls_offered: 950, calls_handled: 0, aht_seconds: null, event: 'Outage, "major"' },
  ]
  const forecast = {
    points: [{ date: '2026-09-23', value: 925.5, intervals: [{ level: 95, lo: 700, hi: 1100 }, { level: 80, lo: 800, hi: 1000 }] }],
  } as TargetForecast
  expect(chartCsv(history, (d) => d.aht_seconds, forecast)).toBe(
    'date,actual,event,forecast,lo_80,hi_80,lo_95,hi_95\n' +
      '2026-09-21,600,,,,,,\n' +
      '2026-09-22,,"Outage, ""major""",,,,,\n' +
      '2026-09-23,,,925.5,800,1000,700,1100\n',
  )
})

test('chartFileName makes a filesystem-safe name', () => {
  expect(chartFileName('Bank Transfers & Bill Pay', 'Call volume', new Date('2026-09-24T10:00:00Z'))).toBe(
    'bank-transfers-bill-pay_call-volume_2026-09-24',
  )
})
