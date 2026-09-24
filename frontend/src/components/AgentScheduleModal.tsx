import {
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
  UnstyledButton,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import type { AgentRecord } from '../api/agents.ts'
import { fetchAgentSchedule, type ScheduleDay } from '../api/schedule.ts'
import { DayTimeline } from './DayTimeline.tsx'
import { formatHours, shortDate, shortTime } from './scheduleUtils.ts'

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
  const selectedDay = week.days.find((d) => d.date === selectedDate) ?? week.days.find((d) => !d.off)

  return (
    <Stack gap="md">
      <AgentProfile agent={agent} />

      <Group justify="space-between" align="center">
        <Group gap="xs">
          {schedule.source === 'mock' && (
            <Badge color="orange" variant="filled" title="Placeholder until CP-SAT scheduling is integrated">
              MOCK
            </Badge>
          )}
          <Text size="sm" c="dimmed">
            {schedule.validation_status}
          </Text>
        </Group>
        <SegmentedControl
          size="xs"
          value={String(weekIndex)}
          onChange={(v) => {
            setWeekIndex(Number(v))
            setSelectedDate(null)
          }}
          data={schedule.weeks.map((_, i) => ({ value: String(i), label: `Week ${i + 1}` }))}
          aria-label="Schedule week"
        />
      </Group>

      <Group gap="lg">
        <Text size="sm" fw={600}>
          {shortDate(week.week_start)} – {shortDate(week.days[6].date)}
        </Text>
        <Text size="sm">Paid hours: {formatHours(week.paid_hours)}</Text>
        <Text size="sm">Days off: {week.days_off}</Text>
      </Group>

      <SimpleGrid cols={7} spacing="xs">
        {week.days.map((day) => (
          <DayCell
            key={day.date}
            day={day}
            selected={day.date === selectedDay?.date}
            onSelect={() => setSelectedDate(day.date)}
          />
        ))}
      </SimpleGrid>

      {selectedDay && !selectedDay.off && (
        <Paper withBorder p="sm">
          <Group justify="space-between" mb={4}>
            <Text size="sm" fw={600}>
              {selectedDay.weekday} {shortDate(selectedDay.date)} · {selectedDay.shift_name} shift{' '}
              {shortTime(selectedDay.start)}–{shortTime(selectedDay.end)}
            </Text>
            <Text size="sm" c="dimmed">
              Paid {formatHours(selectedDay.paid_hours)}
            </Text>
          </Group>
          <DayTimeline segments={selectedDay.segments} />
        </Paper>
      )}
    </Stack>
  )
}

function DayCell({ day, selected, onSelect }: { day: ScheduleDay; selected: boolean; onSelect: () => void }) {
  const label = day.off
    ? `${day.weekday} ${shortDate(day.date)}: day off`
    : `${day.weekday} ${shortDate(day.date)}: ${day.shift_name} ${shortTime(day.start)}–${shortTime(day.end)}`
  return (
    <UnstyledButton onClick={onSelect} disabled={day.off} aria-label={label} aria-pressed={selected}>
      <Paper
        withBorder
        p="xs"
        h="100%"
        bg={day.off ? 'var(--mantine-color-gray-1)' : selected ? 'var(--mantine-color-blue-light)' : undefined}
        style={{ borderColor: selected ? 'var(--mantine-color-blue-5)' : undefined }}
      >
        <Text size="xs" c="dimmed">
          {day.weekday} {shortDate(day.date)}
        </Text>
        {day.off ? (
          <Text size="sm" fw={600} c="dimmed" mt={4}>
            Off
          </Text>
        ) : (
          <>
            <Text size="sm" fw={600} mt={4}>
              {shortTime(day.start)}–{shortTime(day.end)}
            </Text>
            <Text size="xs" c="dimmed">
              {day.shift_name} ({day.shift_code})
            </Text>
          </>
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
  ['Preferred days off', 'preferred_days_off'],
]

function AgentProfile({ agent }: { agent: AgentRecord }) {
  const show = (v: AgentRecord[string]) => (v === null ? '—' : String(v).split(';').join(', '))
  return (
    <Paper withBorder p="sm">
      <Group gap="xs" mb="xs">
        <Title order={5}>{String(agent.agent_name)}</Title>
        <Badge variant="light">{String(agent.agent_id)}</Badge>
        <Text size="sm" c="dimmed">
          {show(agent.bu_name)} › {show(agent.mu_name)}
        </Text>
      </Group>
      <SimpleGrid cols={{ base: 2, md: 5 }} spacing="xs" verticalSpacing={4}>
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
