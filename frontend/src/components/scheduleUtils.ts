import type { ActivityType, CoverageRow } from '../api/schedule.ts'

/** "10:15:00" -> 615 */
export function toMinutes(time: string): number {
  const [h = 0, m = 0] = time.split(':').map(Number)
  return h * 60 + m
}

/** "10:15:00" -> "10:15" */
export function shortTime(time: string | null): string {
  return time ? time.slice(0, 5) : ''
}

/**
 * Local wall-clock "HH:MM" of an ISO timestamp with offset ("2026-09-23T10:15:00-04:00" → "10:15").
 * The offset in the string is the queue's local offset, so this is correct on DST days too.
 */
export function isoTime(iso: string | null | undefined): string {
  return iso ? iso.slice(11, 16) : ''
}

/** Local minutes after midnight of an ISO timestamp with offset. */
export function isoMinutes(iso: string): number {
  return toMinutes(isoTime(iso))
}

/** "2026-09-28" -> "Sep 28" (parsed as a local calendar date, not UTC). */
export function shortDate(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function formatHours(hours: number): string {
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(2).replace(/0+$/, '')}h`
}

/** Colour, legend label and a letter so activities are distinguishable without colour. */
export const ACTIVITY_STYLE: Record<ActivityType, { fill: string; text: string; letter: string }> = {
  'On Phone': { fill: 'var(--mantine-color-blue-5)', text: 'white', letter: 'P' },
  'Paid Break': { fill: 'var(--mantine-color-yellow-5)', text: 'black', letter: 'B' },
  'Unpaid Meal': { fill: 'var(--mantine-color-orange-6)', text: 'black', letter: 'M' },
  Leave: { fill: 'var(--mantine-color-violet-4)', text: 'white', letter: 'L' },
  Training: { fill: 'var(--mantine-color-teal-5)', text: 'white', letter: 'T' },
}

/** Mon–Sun weeks (by start date) covering a list of ISO dates. */
export function weeksOf(dates: string[]): string[] {
  const starts = new Set<string>()
  for (const d of dates) {
    const [y, m, day] = d.split('-').map(Number)
    const dt = new Date(Date.UTC(y, m - 1, day))
    dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7))
    starts.add(dt.toISOString().slice(0, 10))
  }
  return [...starts].sort()
}

/** The 7 ISO dates of the week starting on `weekStart`. */
export function weekDates(weekStart: string): string[] {
  const [y, m, d] = weekStart.split('-').map(Number)
  return Array.from({ length: 7 }, (_, i) => new Date(Date.UTC(y, m - 1, d + i)).toISOString().slice(0, 10))
}

export type DayCoverage = {
  labels: string[] // "HH:MM" local
  required: number[]
  scheduled: number[]
  shortage: number[]
  closing: boolean[]
}

/** Coverage rows of one local date, in time order. */
export function dayCoverage(rows: CoverageRow[], date: string): DayCoverage {
  const day = rows.filter((r) => r.interval_start.slice(0, 10) === date).sort((a, b) => a.interval_start.localeCompare(b.interval_start))
  return {
    labels: day.map((r) => isoTime(r.interval_start)),
    required: day.map((r) => r.required_on_phone),
    scheduled: day.map((r) => r.scheduled_on_phone),
    shortage: day.map((r) => r.shortage),
    closing: day.map((r) => r.kind === 'closing'),
  }
}
