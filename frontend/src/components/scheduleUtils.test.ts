import { expect, test } from 'vitest'

import type { CoverageRow } from '../api/schedule.ts'
import { dayCoverage, isoMinutes, isoTime, weekDates, weeksOf } from './scheduleUtils.ts'

test('isoTime reads the local wall clock from an offset timestamp', () => {
  expect(isoTime('2026-09-23T10:15:00-04:00')).toBe('10:15')
  expect(isoTime('2026-11-01T10:15:00-05:00')).toBe('10:15') // after the DST change
  expect(isoMinutes('2026-09-23T20:30:00-04:00')).toBe(20 * 60 + 30)
  expect(isoTime(null)).toBe('')
})

test('weeksOf groups dates into Monday-start weeks, including partial ones', () => {
  expect(weeksOf(['2026-09-23', '2026-09-27', '2026-09-28', '2026-10-13'])).toEqual(['2026-09-21', '2026-09-28', '2026-10-12'])
  expect(weekDates('2026-09-28')).toEqual([
    '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04',
  ])
})

test('dayCoverage picks one local day in time order and flags closing work', () => {
  const row = (start: string, req: number, sch: number, kind: CoverageRow['kind'] = 'open'): CoverageRow => ({
    queue_id: 'Q-1', interval_start: start, interval_end: start, interval_minutes: 15, kind,
    required_on_phone: req, scheduled_on_phone: sch, shortage: Math.max(req - sch, 0), excess: Math.max(sch - req, 0),
  })
  const rows = [
    row('2026-09-23T20:30:00-04:00', 5, 0, 'closing'),
    row('2026-09-23T08:00:00-04:00', 10, 12),
    row('2026-09-24T08:00:00-04:00', 1, 1),
    row('2026-09-23T08:15:00-04:00', 14, 11),
  ]
  expect(dayCoverage(rows, '2026-09-23')).toEqual({
    labels: ['08:00', '08:15', '20:30'],
    required: [10, 14, 5],
    scheduled: [12, 11, 0],
    shortage: [0, 3, 5],
    closing: [false, false, true],
  })
})
