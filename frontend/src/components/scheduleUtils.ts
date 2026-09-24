/** "10:15:00" -> 615 */
export function toMinutes(time: string): number {
  const [h = 0, m = 0] = time.split(':').map(Number)
  return h * 60 + m
}

/** "10:15:00" -> "10:15" */
export function shortTime(time: string | null): string {
  return time ? time.slice(0, 5) : ''
}

/** "2026-09-28" -> "Sep 28" (parsed as a local calendar date, not UTC). */
export function shortDate(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function formatHours(hours: number): string {
  return Number.isInteger(hours) ? `${hours}h` : `${hours.toFixed(2).replace(/0+$/, '')}h`
}
