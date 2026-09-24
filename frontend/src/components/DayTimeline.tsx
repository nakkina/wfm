import { Group, Text } from '@mantine/core'

import type { Segment, SegmentKind } from '../api/schedule.ts'
import { shortTime, toMinutes } from './scheduleUtils.ts'

// Axis covers every shift template (08:00–20:30) with some room either side.
const AXIS_START = 6 * 60
const AXIS_END = 22 * 60
const SLOT = 15
const WIDTH = 960
const PAD_X = 20 // room for the first and last hour labels
const BAR_Y = 22
const BAR_H = 30

const SEGMENT_STYLE: Record<SegmentKind, { fill: string; label: string }> = {
  work: { fill: 'var(--mantine-color-blue-5)', label: 'Work' },
  break: { fill: 'var(--mantine-color-yellow-5)', label: 'Break' },
  meal: { fill: 'var(--mantine-color-orange-6)', label: 'Meal' },
}

function x(minutes: number): number {
  return PAD_X + ((minutes - AXIS_START) / (AXIS_END - AXIS_START)) * (WIDTH - 2 * PAD_X)
}

/** Work, break and meal segments for one day on a 15-minute grid. */
export function DayTimeline({ segments }: { segments: Segment[] }) {
  const hours = Array.from({ length: (AXIS_END - AXIS_START) / 60 + 1 }, (_, i) => AXIS_START + i * 60)
  const slots = Array.from({ length: (AXIS_END - AXIS_START) / SLOT + 1 }, (_, i) => AXIS_START + i * SLOT)

  return (
    <div>
      <svg
        viewBox={`0 0 ${WIDTH} ${BAR_Y + BAR_H + 22}`}
        width="100%"
        role="img"
        aria-label={segments
          .map((s) => `${SEGMENT_STYLE[s.kind].label} ${shortTime(s.start)}–${shortTime(s.end)}`)
          .join(', ')}
      >
        {slots.map((m) => (
          <line
            key={m}
            x1={x(m)}
            x2={x(m)}
            y1={BAR_Y - (m % 60 === 0 ? 6 : 2)}
            y2={BAR_Y + BAR_H}
            stroke="var(--mantine-color-gray-3)"
            strokeWidth={m % 60 === 0 ? 1 : 0.5}
          />
        ))}
        {segments.map((s) => {
          const x1 = x(toMinutes(s.start))
          const w = x(toMinutes(s.end)) - x1
          return (
            <g key={`${s.kind}-${s.start}`}>
              <rect x={x1} y={BAR_Y} width={w} height={BAR_H} fill={SEGMENT_STYLE[s.kind].fill} rx={2}>
                <title>{`${SEGMENT_STYLE[s.kind].label} ${shortTime(s.start)}–${shortTime(s.end)}`}</title>
              </rect>
              {/* Letter labels so segments are distinguishable without colour (PRD §3). */}
              {w >= 14 && (
                <text
                  x={x1 + w / 2}
                  y={BAR_Y + BAR_H / 2 + 4}
                  textAnchor="middle"
                  fontSize={11}
                  fill={s.kind === 'work' ? 'white' : 'black'}
                >
                  {s.kind === 'work' ? (w > 70 ? 'Work' : 'W') : s.kind === 'meal' ? 'M' : 'B'}
                </text>
              )}
            </g>
          )
        })}
        {hours.map((m) => (
          <text key={m} x={x(m)} y={BAR_Y + BAR_H + 16} textAnchor="middle" fontSize={11} fill="gray">
            {String(m / 60).padStart(2, '0')}:00
          </text>
        ))}
      </svg>
      <Group gap="md" mt={4}>
        {(Object.keys(SEGMENT_STYLE) as SegmentKind[]).map((kind) => (
          <Group key={kind} gap={4}>
            <div style={{ width: 12, height: 12, borderRadius: 2, background: SEGMENT_STYLE[kind].fill }} />
            <Text size="xs">{SEGMENT_STYLE[kind].label}</Text>
          </Group>
        ))}
        <Text size="xs" c="dimmed">
          Grid: 15-minute intervals
        </Text>
      </Group>
    </div>
  )
}
