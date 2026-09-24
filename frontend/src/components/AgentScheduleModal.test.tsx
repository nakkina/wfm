import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import type { Activity, AgentScheduleResponse, PlanDay, PlanWeek } from '../api/schedule.ts'
import { AgentScheduleModal } from './AgentScheduleModal.tsx'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const at = (date: string, hhmm: string) => `${date}T${hhmm}:00-04:00`

function working(date: string, weekday: string, start: string): PlanDay {
  const h = Number(start.slice(0, 2))
  const t = (hour: number, min = 0) => `${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}`
  const acts: Activity[] = [
    { activity_type: 'On Phone', start: at(date, t(h)), end: at(date, t(h + 2)), paid: true },
    { activity_type: 'Paid Break', start: at(date, t(h + 2)), end: at(date, t(h + 2, 15)), paid: true },
    { activity_type: 'On Phone', start: at(date, t(h + 2, 15)), end: at(date, t(h + 4)), paid: true },
    { activity_type: 'Unpaid Meal', start: at(date, t(h + 4)), end: at(date, t(h + 4, 30)), paid: false },
    { activity_type: 'On Phone', start: at(date, t(h + 4, 30)), end: at(date, t(h + 8, 30)), paid: true },
  ]
  return {
    date, weekday, status: 'Working', off: false, shift_code: 's1', shift_name: 'Early',
    start: at(date, t(h)), end: at(date, t(h + 8, 30)), paid_hours: 8,
    preferred_shift_matched: true, preferred_day_off: false, activities: acts,
  }
}

function week(dates: string[], statuses: PlanDay['status'][], start: string): PlanWeek {
  const days = dates.map((date, i): PlanDay =>
    statuses[i] === 'Working' ? working(date, WEEKDAYS[i], start) : { date, weekday: WEEKDAYS[i], status: statuses[i], off: true, activities: [] },
  )
  const inHorizon = days.filter((d) => d.status !== 'Outside horizon')
  return {
    week_start: dates[0],
    paid_hours: inHorizon.filter((d) => d.status === 'Working').length * 8,
    days_off: inHorizon.filter((d) => d.status === 'Off').length,
    leave_days: inHorizon.filter((d) => d.status === 'Leave').length,
    preferred_shift_matches: inHorizon.filter((d) => d.status === 'Working').length,
    working_days: inHorizon.filter((d) => d.status === 'Working').length,
    partial: inHorizon.length < 7,
    days,
  }
}

const W1 = ['2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24', '2026-09-25', '2026-09-26', '2026-09-27']
const W2 = ['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']

const response: AgentScheduleResponse = {
  agent: { agent_id: 'AG0012', agent_name: 'Yusuf Haddad', queue_name: 'Card Purchase Disputes', work_plan_id: 'WP-FT', languages: 'English;Spanish' },
  schedule: {
    source: 'optimized',
    run_id: 'R1',
    validation_status: 'Validated',
    queue_status: 'Feasible',
    timezone: 'America/New_York',
    weeks: [
      week(W1, ['Outside horizon', 'Outside horizon', 'Working', 'Working', 'Leave', 'Off', 'Working'], '08'),
      week(W2, ['Working', 'Working', 'Off', 'Working', 'Working', 'Working', 'Off'], '10'),
    ],
  },
}

function renderModal() {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(response))))
  render(
    <MantineProvider>
      <QueryClientProvider client={new QueryClient()}>
        <AgentScheduleModal agentId="AG0012" onClose={() => {}} />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

test('shows profile, run labels, partial week and Off/Leave/outside-horizon days', async () => {
  renderModal()
  expect(await screen.findByText('Yusuf Haddad')).toBeInTheDocument()
  expect(screen.getByText('Optimized · run R1')).toBeInTheDocument()
  expect(screen.getByText('Solver: Feasible')).toBeInTheDocument()
  expect(screen.getByText('Validated')).toBeInTheDocument()
  expect(screen.getByText(/partial week in horizon/)).toBeInTheDocument()
  expect(screen.getByText('Paid hours: 24h')).toBeInTheDocument()
  expect(screen.getByLabelText('Wed Sep 23: Early 08:00–16:30')).toBeInTheDocument()
  expect(screen.getByLabelText('Fri Sep 25: Leave')).toBeInTheDocument()
  expect(screen.getByLabelText('Sat Sep 26: Off')).toBeInTheDocument()
  expect(screen.getByLabelText('Mon Sep 21: Outside horizon')).toBeInTheDocument()
  // First working day's timeline shows breaks and meal with times.
  expect(screen.getByText('Paid Break: 10:00–10:15')).toBeInTheDocument()
  expect(screen.getByText('Unpaid Meal: 12:00–12:30')).toBeInTheDocument()
})

test('previous/next week navigation and day selection', async () => {
  renderModal()
  await screen.findByText('Yusuf Haddad')
  expect(screen.getByLabelText('Previous week')).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Next week'))
  expect(screen.getByLabelText('Mon Sep 28: Early 10:00–18:30')).toBeInTheDocument()
  expect(screen.getByLabelText('Next week')).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Thu Oct 1: Early 10:00–18:30'))
  expect(screen.getByText(/Thu Oct 1 · Early shift 10:00–18:30 · Card Purchase Disputes/)).toBeInTheDocument()
})
