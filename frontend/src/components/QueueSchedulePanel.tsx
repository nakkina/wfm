import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  ScrollArea,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { themeQuartz, type CellStyle, type ColDef } from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import type { Data, Layout } from 'plotly.js'
import { lazy, Suspense, useMemo, useState } from 'react'

import { exportUrl, fetchQueueSchedule, type QueueSchedule, type ScheduleAgent, type ScheduleDay } from '../api/schedule.ts'
import { DayTimeline } from './DayTimeline.tsx'
import { dayCoverage, formatHours, isoTime, shortDate, weekDates, weeksOf } from './scheduleUtils.ts'

const Plot = lazy(async () => {
  const [{ default: createPlotlyComponent }, plotly] = await Promise.all([
    import('react-plotly.js/factory'),
    import('plotly.js-dist-min'),
  ])
  return { default: createPlotlyComponent(plotly.default) }
})

const gridTheme = themeQuartz.withParams({ fontSize: 12, headerFontSize: 12, spacing: 5 })
const WEEKDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const STATUS_COLOR: Record<string, string> = { Optimal: 'green', Feasible: 'yellow', Infeasible: 'red', Unknown: 'gray' }

type Props = { queueId: string; onOpenAgent: (agentId: string) => void }

export function QueueSchedulePanel({ queueId, onOpenAgent }: Props) {
  const query = useQuery({ queryKey: ['queue-schedule', queueId], queryFn: () => fetchQueueSchedule(queueId), retry: false })
  const [weekIdx, setWeekIdx] = useState(0)
  const [day, setDay] = useState<string | null>(null)

  if (query.isPending) return <Loader size="sm" />
  if (query.isError)
    return (
      <Alert color="gray" title="No schedule for this queue yet">
        {query.error.message}. Use “Generate schedule” in the header to create one from the saved forecasts.
      </Alert>
    )

  const data = query.data
  const dates = [...new Set(data.agents.flatMap((a) => a.days.map((d) => d.work_date)))].sort()
  const weeks = weeksOf(dates)
  const week = weeks[Math.min(weekIdx, weeks.length - 1)]
  const weekDays = weekDates(week)
  const inHorizon = weekDays.filter((d) => dates.includes(d))
  const selectedDay = day && inHorizon.includes(day) ? day : inHorizon[0]

  return (
    <ScrollArea style={{ flex: 1 }} offsetScrollbars>
      <Stack gap="sm">
        <RunHeader data={data} queueId={queueId} />
        <SummaryCards data={data} />

        <Group justify="space-between">
          <SegmentedControl
            size="xs"
            value={String(weekIdx)}
            onChange={(v) => {
              setWeekIdx(Number(v))
              setDay(null)
            }}
            data={weeks.map((w, i) => ({ value: String(i), label: `Week of ${shortDate(w)}` }))}
            aria-label="Schedule week"
          />
          <SegmentedControl
            size="xs"
            value={selectedDay}
            onChange={setDay}
            data={inHorizon.map((d) => ({ value: d, label: `${WEEKDAY[weekDays.indexOf(d)]} ${shortDate(d)}` }))}
            aria-label="Day"
          />
        </Group>

        <Suspense fallback={<Loader size="sm" />}>
          <CoverageChart data={data} day={selectedDay} />
        </Suspense>

        <Paper withBorder p="xs">
          <Text size="sm" fw={600} px={4} mb={4}>
            Weekly shift plan — week of {shortDate(week)} (click an agent for their calendar)
          </Text>
          <WeekGrid agents={data.agents} weekDays={weekDays} onOpenAgent={onOpenAgent} />
        </Paper>

        <Paper withBorder p="xs">
          <Text size="sm" fw={600} px={4} mb={4}>
            Daily timeline — {WEEKDAY[weekDays.indexOf(selectedDay)]} {shortDate(selectedDay)} (working agents)
          </Text>
          <DayTimeline
            rows={data.agents
              .map((a) => ({ agent: a, d: a.days.find((x) => x.work_date === selectedDay) }))
              .filter((r) => r.d && (r.d.status === 'Working' || r.d.status === 'Leave'))
              .sort((p, q) => (p.d?.shift_start ?? '').localeCompare(q.d?.shift_start ?? ''))
              .map(({ agent, d }) => ({
                key: agent.agent_id,
                label: `${agent.agent_id} ${agent.agent_name.split(' ')[0]}`,
                activities: d?.activities ?? [],
                onClick: () => onOpenAgent(agent.agent_id),
              }))}
          />
        </Paper>
      </Stack>
    </ScrollArea>
  )
}

function RunHeader({ data, queueId }: { data: QueueSchedule; queueId: string }) {
  const q = data.queue
  return (
    <Group justify="space-between">
      <Group gap="xs">
        <Tooltip
          multiline
          w={380}
          label={q.stages
            .map((s) => `${s.stage}: ${s.status} · objective ${s.objective ?? '—'} · bound ${s.bound ?? '—'} · ${s.seconds}s`)
            .join('\n')}
          style={{ whiteSpace: 'pre-line' }}
        >
          <Badge color={STATUS_COLOR[q.status] ?? 'gray'} variant="filled">
            Solver: {q.status}
          </Badge>
        </Tooltip>
        <Badge color={data.validation_passed ? 'green' : 'red'} variant="light">
          {data.validation_passed ? 'Hard rules validated' : 'Validation failed'}
        </Badge>
        {data.stale && (
          <Badge color="orange" variant="light">
            Stale — inputs changed since this run
          </Badge>
        )}
        <Text size="xs" c="dimmed">
          Run {data.run_id} · {shortDate(data.horizon.start)} – {shortDate(data.horizon.end)} ({data.horizon.days} days)
        </Text>
      </Group>
      <Group gap="xs">
        <Button component="a" href={exportUrl(data.run_id, 'shifts', queueId)} size="xs" variant="default">
          Shift plans CSV
        </Button>
        <Button component="a" href={exportUrl(data.run_id, 'activities', queueId)} size="xs" variant="default">
          Activities CSV
        </Button>
      </Group>
    </Group>
  )
}

function SummaryCards({ data }: { data: QueueSchedule }) {
  const t = data.totals
  const cards: [string, string, string?][] = [
    ['Required on-phone hours', formatHours(Math.round(t.required_on_phone_hours))],
    ['Scheduled on-phone hours', formatHours(Math.round(t.scheduled_on_phone_hours))],
    ['Shortage agent-hours', formatHours(Math.round(t.shortage_agent_hours)), t.shortage_agent_hours > 0 ? 'red' : 'green'],
    ['Excess agent-hours', formatHours(Math.round(t.excess_agent_hours))],
    ['Scheduled paid hours', formatHours(Math.round(t.paid_hours))],
    ['Peak required on phone', `${t.peak_required_on_phone} agents`],
    ['Shifts / agents', `${t.working_shifts} / ${t.agents}`],
  ]
  return (
    <SimpleGrid cols={{ base: 2, md: 7 }} spacing="xs">
      {cards.map(([label, value, color]) => (
        <Paper key={label} withBorder p="xs">
          <Text size="xs" c="dimmed">
            {label}
          </Text>
          <Text size="lg" fw={700} c={color}>
            {value}
          </Text>
        </Paper>
      ))}
    </SimpleGrid>
  )
}

function CoverageChart({ data, day }: { data: QueueSchedule; day: string }) {
  const c = dayCoverage(data.coverage, day)
  const short = c.shortage.map((s) => (s > 0 ? s : null))
  const traces: Data[] = [
    {
      type: 'bar',
      name: 'Shortage (agents)',
      x: c.labels,
      y: short,
      marker: { color: 'rgba(224, 49, 49, 0.55)' },
      text: short.map((s) => (s ? `−${s}` : '')),
      textposition: 'outside',
      hovertemplate: '%{x}: short %{y} agents<extra></extra>',
    },
    {
      type: 'scatter',
      mode: 'lines',
      name: 'Required on phone',
      x: c.labels,
      y: c.required,
      line: { shape: 'hv', color: 'rgb(60, 60, 60)', width: 2, dash: 'dot' },
      hovertemplate: '%{x}: required %{y}<extra></extra>',
    },
    {
      type: 'scatter',
      mode: 'lines',
      name: 'Scheduled on phone',
      x: c.labels,
      y: c.scheduled,
      line: { shape: 'hv', color: 'rgb(34, 139, 230)', width: 2 },
      hovertemplate: '%{x}: scheduled %{y}<extra></extra>',
    },
  ]
  // Category axis: shade from half a slot before the first closing interval to the end.
  const closingIdx = c.closing.indexOf(true)
  const closingStart = closingIdx >= 0 ? closingIdx - 0.5 : null
  const layout: Partial<Layout> = {
    height: 280,
    margin: { l: 50, r: 16, t: 24, b: 60 },
    xaxis: { type: 'category', tickangle: -45, nticks: 26 },
    yaxis: { title: { text: 'Agents' }, rangemode: 'tozero' },
    legend: { orientation: 'h', x: 0, y: -0.35 },
    hovermode: 'x unified',
    barmode: 'overlay',
    shapes: closingStart !== null
      ? [{ type: 'rect', xref: 'x', yref: 'paper', x0: closingStart, x1: c.labels.length - 0.5, y0: 0, y1: 1, fillcolor: 'rgba(0,0,0,0.06)', line: { width: 0 } }]
      : [],
    annotations: closingStart !== null
      ? [{ x: closingStart, xref: 'x', yref: 'paper', y: 1, yanchor: 'bottom', xanchor: 'left', text: 'After close (finishing work)', showarrow: false, font: { size: 10, color: '#555' } }]
      : [],
  }
  const shortSlots = c.shortage.filter((s) => s > 0).length
  return (
    <Paper withBorder p="xs">
      <Group justify="space-between" px={4}>
        <Text size="sm" fw={600}>
          Required vs scheduled on-phone agents — {shortDate(day)} (15-minute intervals)
        </Text>
        <Text size="xs" c={shortSlots ? 'red' : 'green'}>
          {shortSlots ? `Short in ${shortSlots} intervals` : 'Fully covered'}
        </Text>
      </Group>
      <Plot data={traces} layout={layout} config={{ displaylogo: false, responsive: true, showSendToCloud: false }} useResizeHandler style={{ width: '100%' }} />
    </Paper>
  )
}

// Off / Leave / Unscheduled cells are styled with text as well as colour; blank = outside horizon.
const CELL_STYLE: Record<string, CellStyle> = {
  Off: { color: 'var(--mantine-color-gray-6)', background: 'var(--mantine-color-gray-0)' },
  Leave: { color: 'var(--mantine-color-violet-7)', background: 'var(--mantine-color-violet-0)' },
  Unscheduled: { color: 'var(--mantine-color-red-7)' },
  none: { background: 'var(--mantine-color-gray-1)' },
}

type GridRow = { agent: ScheduleAgent; weekPaid: number; weekPref: string; [day: string]: unknown }

function cellText(d: ScheduleDay | undefined): string {
  if (!d) return ''
  if (d.status === 'Working') return `${d.shift_code} ${isoTime(d.shift_start)}–${isoTime(d.shift_end)}`
  return d.status
}

function WeekGrid({ agents, weekDays, onOpenAgent }: { agents: ScheduleAgent[]; weekDays: string[]; onOpenAgent: (id: string) => void }) {
  const rows = useMemo<GridRow[]>(
    () =>
      agents.map((a) => {
        const byDate = new Map(a.days.map((d) => [d.work_date, d]))
        const inWeek = weekDays.map((d) => byDate.get(d)).filter((d): d is ScheduleDay => !!d)
        const working = inWeek.filter((d) => d.status === 'Working')
        const row: GridRow = {
          agent: a,
          weekPaid: inWeek.reduce((s, d) => s + (d.paid_minutes || 0), 0) / 60,
          weekPref: `${working.filter((d) => d.preferred_shift_matched === true).length}/${working.length}`,
        }
        weekDays.forEach((d) => (row[d] = byDate.get(d)))
        return row
      }),
    [agents, weekDays],
  )
  const columns = useMemo<ColDef<GridRow>[]>(
    () => [
      {
        headerName: 'Agent',
        pinned: 'left',
        width: 190,
        valueGetter: ({ data }) => (data ? `${data.agent.agent_id} ${data.agent.agent_name}` : ''),
        cellRenderer: ({ data }: { data?: GridRow }) =>
          data ? (
            <Anchor component="button" size="sm" onClick={() => onOpenAgent(data.agent.agent_id)}>
              {data.agent.agent_id} {data.agent.agent_name}
            </Anchor>
          ) : null,
      },
      ...weekDays.map<ColDef<GridRow>>((d, i) => ({
        headerName: `${WEEKDAY[i]} ${shortDate(d)}`,
        colId: d,
        width: 132,
        valueGetter: ({ data }) => cellText(data?.[d] as ScheduleDay | undefined),
        cellStyle: ({ data }) => CELL_STYLE[(data?.[d] as ScheduleDay | undefined)?.status ?? 'none'] ?? null,
      })),
      { headerName: 'Paid h', field: 'weekPaid', width: 84, type: 'numericColumn' },
      { headerName: 'Pref. shift', field: 'weekPref', width: 96, headerTooltip: 'Working days on the preferred shift' },
    ],
    [weekDays, onOpenAgent],
  )
  return (
    <div style={{ height: Math.min(520, 42 + rows.length * 30) }}>
      <AgGridReact<GridRow> theme={gridTheme} rowData={rows} columnDefs={columns} rowHeight={30} getRowId={({ data }) => data.agent.agent_id} />
    </div>
  )
}

export function ScheduleTotalsTable({ rows }: { rows: { label: string; totals: QueueSchedule['totals'] }[] }) {
  return (
    <Table striped withTableBorder fz="sm">
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Unit</Table.Th>
          <Table.Th ta="right">Required on-phone h</Table.Th>
          <Table.Th ta="right">Scheduled on-phone h</Table.Th>
          <Table.Th ta="right">Shortage agent-h</Table.Th>
          <Table.Th ta="right">Excess agent-h</Table.Th>
          <Table.Th ta="right">Paid h</Table.Th>
          <Table.Th ta="right">Shifts</Table.Th>
          <Table.Th ta="right">Agents</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {rows.map(({ label, totals }) => (
          <Table.Tr key={label} fw={label === 'Total' ? 700 : undefined}>
            <Table.Td>{label}</Table.Td>
            <Table.Td ta="right">{Math.round(totals.required_on_phone_hours).toLocaleString()}</Table.Td>
            <Table.Td ta="right">{Math.round(totals.scheduled_on_phone_hours).toLocaleString()}</Table.Td>
            <Table.Td ta="right" c={totals.shortage_agent_hours > 0 ? 'red' : undefined}>
              {Math.round(totals.shortage_agent_hours).toLocaleString()}
            </Table.Td>
            <Table.Td ta="right">{Math.round(totals.excess_agent_hours).toLocaleString()}</Table.Td>
            <Table.Td ta="right">{Math.round(totals.paid_hours).toLocaleString()}</Table.Td>
            <Table.Td ta="right">{totals.working_shifts.toLocaleString()}</Table.Td>
            <Table.Td ta="right">{totals.agents.toLocaleString()}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  )
}
