import { Group, Text } from '@mantine/core'

import type { Activity, ActivityType } from '../api/schedule.ts'
import { ACTIVITY_STYLE, isoMinutes, isoTime } from './scheduleUtils.ts'

// Axis covers every shift template (08:00–20:30) and the closing allowance, with room either side.
const AXIS_START = 6 * 60
const AXIS_END = 22 * 60
const SLOT = 15
const WIDTH = 960
const LABEL_W = 150 // left gutter for row labels (multi-row only)
const PAD_X = 20
const AXIS_H = 18
const ROW_H = 22
const ROW_GAP = 4

export type TimelineRow = { key: string; label?: string; activities: Activity[]; onClick?: () => void }

function describe(a: Activity): string {
  return `${a.activity_type} ${isoTime(a.start)}–${isoTime(a.end)}`
}

/**
 * Activities on a 15-minute grid: one row for an agent's day, or many rows (one per agent)
 * for a queue's day. Each block has a colour and a letter (P/B/M/L) so it reads without colour.
 */
export function DayTimeline({ rows, showLegend = true }: { rows: TimelineRow[]; showLegend?: boolean }) {
  const labelled = rows.some((r) => r.label)
  const left = labelled ? LABEL_W : PAD_X
  const x = (minutes: number) => left + ((minutes - AXIS_START) / (AXIS_END - AXIS_START)) * (WIDTH - left - PAD_X)
  const height = AXIS_H + rows.length * (ROW_H + ROW_GAP) + 4
  const hours = Array.from({ length: (AXIS_END - AXIS_START) / 60 + 1 }, (_, i) => AXIS_START + i * 60)
  const slots = Array.from({ length: (AXIS_END - AXIS_START) / SLOT + 1 }, (_, i) => AXIS_START + i * SLOT)
  const present = [...new Set(rows.flatMap((r) => r.activities.map((a) => a.activity_type)))]

  return (
    <div>
      <svg viewBox={`0 0 ${WIDTH} ${height}`} width="100%" role="img" aria-label={rows.map((r) => `${r.label ?? ''} ${r.activities.map(describe).join(', ')}`).join('; ')}>
        {hours.map((m) => (
          <text key={`h${m}`} x={x(m)} y={12} textAnchor="middle" fontSize={11} fill="gray">
            {String(m / 60).padStart(2, '0')}:00
          </text>
        ))}
        {slots.map((m) => (
          <line
            key={`s${m}`}
            x1={x(m)}
            x2={x(m)}
            y1={AXIS_H - (m % 60 === 0 ? 4 : 1)}
            y2={height}
            stroke="var(--mantine-color-gray-3)"
            strokeWidth={m % 60 === 0 ? 1 : 0.4}
          />
        ))}
        {rows.map((row, i) => {
          const y = AXIS_H + i * (ROW_H + ROW_GAP)
          return (
            <g key={row.key} onClick={row.onClick} style={{ cursor: row.onClick ? 'pointer' : undefined }}>
              {row.label && (
                <text x={4} y={y + ROW_H / 2 + 4} fontSize={11} fill={row.onClick ? 'var(--mantine-color-blue-7)' : 'currentColor'}>
                  {row.label}
                </text>
              )}
              {row.activities.length === 0 && (
                <text x={x(AXIS_START) + 4} y={y + ROW_H / 2 + 4} fontSize={11} fill="gray">
                  —
                </text>
              )}
              {row.activities.map((a) => {
                const style = ACTIVITY_STYLE[a.activity_type] ?? ACTIVITY_STYLE['On Phone']
                const x1 = x(isoMinutes(a.start))
                const w = Math.max(x(isoMinutes(a.end)) - x1, 1)
                return (
                  <g key={`${a.activity_type}-${a.start}`}>
                    <rect x={x1} y={y} width={w} height={ROW_H} fill={style.fill} rx={2}>
                      <title>{describe(a)}</title>
                    </rect>
                    {w >= 12 && (
                      <text x={x1 + w / 2} y={y + ROW_H / 2 + 4} textAnchor="middle" fontSize={11} fill={style.text}>
                        {a.activity_type === 'On Phone' && w > 70 ? 'On Phone' : style.letter}
                      </text>
                    )}
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
      {showLegend && (
        <Group gap="md" mt={4}>
          {(Object.keys(ACTIVITY_STYLE) as ActivityType[])
            .filter((t) => present.includes(t) || t === 'On Phone' || t === 'Paid Break' || t === 'Unpaid Meal')
            .map((t) => (
              <Group key={t} gap={4}>
                <div style={{ width: 12, height: 12, borderRadius: 2, background: ACTIVITY_STYLE[t].fill }} />
                <Text size="xs">
                  {t} ({ACTIVITY_STYLE[t].letter})
                </Text>
              </Group>
            ))}
          <Text size="xs" c="dimmed">
            Grid: 15-minute intervals · local time
          </Text>
        </Group>
      )}
    </div>
  )
}
