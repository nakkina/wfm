import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Loader,
  Modal,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { AgentRecord } from '../api/agents.ts'
import { fetchAgentSchedule, type PlanDay } from '../api/schedule.ts'
import { DayTimeline } from './DayTimeline.tsx'
import { formatHours, isoTime, shortDate } from './scheduleUtils.ts'

type Props = { agentId: string | null; onClose: () => void }

export function AgentScheduleModal({ agentId, onClose }: Props) {
  return (
    <Modal
      opened={agentId !== null}
      onClose={onClose}
      size="80rem"
      title="Agent schedule"
      styles={{ title: { fontWeight: 700, fontSize: 'var(--mantine-font-size-lg)' } }}
    >
      {agentId && <ScheduleContent key={agentId} agentId={agentId} />}
    </Modal>
  )
}

function ScheduleContent({ agentId }: { agentId: string }) {
  const query = useQuery({ queryKey: ['agent-schedule', agentId], queryFn: () => fetchAgentSchedule(agentId) })
  const [weekIndex, setWeekIndex] = useState(0)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  if (query.isPending) return <Loader size="sm" />
  if (query.isError)
    return (
      <Alert color="red" title="Could not load schedule">
        {query.error.message}
      </Alert>
    )

  const { agent, schedule } = query.data
  const week = schedule.weeks[weekIndex]
  const selectedDay =
    week.days.find((d) => d.date === selectedDate) ?? week.days.find((d) => d.status === 'Working' || d.status === 'Leave')
  const goTo = (i: number) => {
    setWeekIndex(i)
    setSelectedDate(null)
  }

  return (
    <Stack gap="md">
      <AgentProfile agent={agent} />

      <Group justify="space-between" align="center">
        <Group gap="xs">
          {schedule.source === 'mock' ? (
            <Tooltip label="No schedule has been generated yet; this placeholder follows the agent's work rules">
              <Badge color="orange" variant="filled">
                MOCK
              </Badge>
            </Tooltip>
          ) : (
            <Badge color="blue" variant="light">
              Optimized · run {schedule.run_id}
            </Badge>
          )}
          {schedule.queue_status && (
            <Badge variant="light" color={schedule.queue_status === 'Optimal' ? 'green' : 'yellow'}>
              Solver: {schedule.queue_status}
            </Badge>
          )}
          <Badge variant="light" color={schedule.validation_status === 'Validated' ? 'green' : 'gray'}>
            {schedule.validation_status}
          </Badge>
          <Text size="xs" c="dimmed">
            Times in {schedule.timezone}
          </Text>
        </Group>
        <Group gap={4}>
          <ActionIcon variant="default" aria-label="Previous week" disabled={weekIndex === 0} onClick={() => goTo(weekIndex - 1)}>
            ‹
          </ActionIcon>
          <SegmentedControl
            size="xs"
            value={String(weekIndex)}
            onChange={(v) => goTo(Number(v))}
            data={schedule.weeks.map((w, i) => ({ value: String(i), label: `Wk of ${shortDate(w.week_start)}` }))}
            aria-label="Schedule week"
          />
          <ActionIcon
            variant="default"
            aria-label="Next week"
            disabled={weekIndex === schedule.weeks.length - 1}
            onClick={() => goTo(weekIndex + 1)}
          >
            ›
          </ActionIcon>
        </Group>
      </Group>

      <Group gap="lg">
        <Text size="sm" fw={600}>
          {shortDate(week.week_start)} – {shortDate(week.days[6].date)}
          {week.partial && ' (partial week in horizon)'}
        </Text>
        <Text size="sm">Paid hours: {formatHours(week.paid_hours)}</Text>
        <Text size="sm">Working days: {week.working_days}</Text>
        <Text size="sm">Days off: {week.days_off}</Text>
        {week.leave_days > 0 && <Text size="sm">Leave: {week.leave_days}</Text>}
        {week.preferred_shift_matches !== null && (
          <Text size="sm">
            Preferred shift: {week.preferred_shift_matches}/{week.working_days}
          </Text>
        )}
      </Group>

      <SimpleGrid cols={7} spacing="xs">
        {week.days.map((day) => (
          <DayCell key={day.date} day={day} selected={day.date === selectedDay?.date} onSelect={() => setSelectedDate(day.date)} />
        ))}
      </SimpleGrid>

      {selectedDay && (selectedDay.status === 'Working' || selectedDay.status === 'Leave') && (
        <Paper withBorder p="sm">
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={600}>
              {selectedDay.weekday} {shortDate(selectedDay.date)} ·{' '}
              {selectedDay.status === 'Leave'
                ? 'Leave'
                : `${selectedDay.shift_name} shift ${isoTime(selectedDay.start)}–${isoTime(selectedDay.end)}`}
              {agent.queue_name ? ` · ${String(agent.queue_name)}` : ''}
            </Text>
            <Text size="sm" c="dimmed">
              Paid {formatHours(selectedDay.paid_hours ?? 0)}
            </Text>
          </Group>
          <DayTimeline rows={[{ key: selectedDay.date, activities: selectedDay.activities ?? [] }]} />
          <Group gap="md" mt={4}>
            {(selectedDay.activities ?? [])
              .filter((a) => a.activity_type !== 'On Phone')
              .map((a) => (
                <Text key={a.start} size="xs">
                  {a.activity_type}: {isoTime(a.start)}–{isoTime(a.end)}
                </Text>
              ))}
          </Group>
        </Paper>
      )}
    </Stack>
  )
}

const STATUS_BG: Partial<Record<PlanDay['status'], string>> = {
  Off: 'var(--mantine-color-gray-1)',
  Leave: 'var(--mantine-color-violet-0)',
  Unscheduled: 'var(--mantine-color-red-0)',
  'Outside horizon': 'var(--mantine-color-gray-0)',
}

function DayCell({ day, selected, onSelect }: { day: PlanDay; selected: boolean; onSelect: () => void }) {
  const clickable = day.status === 'Working' || day.status === 'Leave'
  const label =
    day.status === 'Working'
      ? `${day.weekday} ${shortDate(day.date)}: ${day.shift_name} ${isoTime(day.start)}–${isoTime(day.end)}`
      : `${day.weekday} ${shortDate(day.date)}: ${day.status}`
  return (
    <UnstyledButton onClick={onSelect} disabled={!clickable} aria-label={label} aria-pressed={selected}>
      <Paper
        withBorder
        p="xs"
        h="100%"
        bg={selected ? 'var(--mantine-color-blue-light)' : STATUS_BG[day.status]}
        style={{ borderColor: selected ? 'var(--mantine-color-blue-5)' : undefined, opacity: day.status === 'Outside horizon' ? 0.6 : 1 }}
      >
        <Text size="xs" c="dimmed">
          {day.weekday} {shortDate(day.date)}
        </Text>
        {day.status === 'Working' ? (
          <>
            <Text size="sm" fw={600} mt={4}>
              {isoTime(day.start)}–{isoTime(day.end)}
            </Text>
            <Text size="xs" c="dimmed">
              {day.shift_name} ({day.shift_code})
              {day.preferred_shift_matched === true && ' · preferred'}
            </Text>
          </>
        ) : (
          <Text size="sm" fw={600} c={day.status === 'Leave' ? 'violet' : 'dimmed'} mt={4}>
            {day.status === 'Outside horizon' ? 'Not in horizon' : day.status}
          </Text>
        )}
        {day.status === 'Working' && day.preferred_day_off === true && (
          <Text size="xs" c="orange">
            preferred day off
          </Text>
        )}
      </Paper>
    </UnstyledButton>
  )
}

const PROFILE_ROWS: [string, string][] = [
  ['Home queue', 'queue_name'],
  ['Team', 'team_id'],
  ['Role', 'role'],
  ['Employment', 'employment_type'],
  ['Work plan', 'work_plan_id'],
  ['Weekly hours', 'weekly_contracted_hours'],
  ['Primary skill', 'primary_skill'],
  ['Languages', 'languages'],
  ['Allowed shifts', 'allowed_shifts'],
  ['Preferred shift', 'preferred_shift'],
  ['Preferred days off', 'preferred_days_off'],
  ['Available days', 'available_days'],
]

function AgentProfile({ agent }: { agent: AgentRecord }) {
  const show = (v: AgentRecord[string]) => (v === null ? '—' : String(v).split(';').join(', '))
  return (
    <Paper withBorder p="sm">
      <Group gap="xs" mb="xs">
        <Title order={5}>{String(agent.agent_name)}</Title>
        <Badge variant="light">{String(agent.agent_id)}</Badge>
        <Text size="sm" c="dimmed">
          {show(agent.bu_name ?? null)} › {show(agent.mu_name ?? null)}
        </Text>
      </Group>
      <SimpleGrid cols={{ base: 2, md: 6 }} spacing="xs" verticalSpacing={4}>
        {PROFILE_ROWS.map(([label, field]) => (
          <div key={field}>
            <Text size="xs" c="dimmed">
              {label}
            </Text>
            <Text size="sm">{show(agent[field] ?? null)}</Text>
          </div>
        ))}
      </SimpleGrid>
    </Paper>
  )
}
